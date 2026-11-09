"""Software e-stop: latch, ordering, failure isolation, lock independence."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, ClassVar

import pytest
from dummy_drivers import FakeActuatingPsu

from vaisor.audit import read_entries
from vaisor.bench import Bench
from vaisor.cli import main
from vaisor.config import BenchConfig, BenchConfigError, DeviceClass, SerialConnection, load_bench
from vaisor.drivers.base import Driver
from vaisor.safety import SafetyRefused
from vaisor.transport import FakeTransport, Transport

CONN = {"type": "serial", "port": "loop://"}
LIMITS = {"max_voltage": 12, "max_current": 1}
LOG: list[tuple[str, str]] = []


class LogPsu(FakeActuatingPsu):
    fail: ClassVar[bool] = False

    def output_off(self) -> None:
        LOG.append((self.name, "output_off"))
        if self.fail:
            raise RuntimeError("boom")

    def set_voltage(self, voltage: float) -> dict[str, float]:
        LOG.append((self.name, "set_voltage"))
        return {"voltage": voltage}

    def read_voltage_dc(self) -> float:
        LOG.append((self.name, "read"))
        return 3.3


class FailingPsu(LogPsu):
    fail: ClassVar[bool] = True


class FakeRelay(Driver):
    device_class: ClassVar[DeviceClass] = DeviceClass.RELAY
    capabilities: ClassVar[frozenset[str]] = frozenset({"relay_set"})

    def relay_set(self, closed: bool) -> None:
        LOG.append((self.name, f"relay_set:{closed}"))


def factory(conn: SerialConnection) -> Transport:
    return FakeTransport()


def make(tmp_path: Path) -> Bench:
    def psu(driver: str) -> dict[str, Any]:
        return {"class": "psu", "driver": driver, "connection": CONN, "limits": LIMITS}

    cfg = BenchConfig.model_validate(
        {
            "state_dir": str(tmp_path),
            "estop_relay": "rly",
            "devices": {
                "a": psu("psu"),
                "b": psu("bad"),
                "c": psu("psu"),
                "rly": {"class": "relay", "driver": "relay", "connection": CONN},
            },
        }
    )
    drivers = {"psu": LogPsu, "bad": FailingPsu, "relay": FakeRelay}
    return Bench.from_config(cfg, drivers, factory, lock_timeout=0.1)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def clear_log() -> None:
    LOG.clear()


def test_order_and_failure_isolation(tmp_path: Path) -> None:
    bench = make(tmp_path)
    for name in ("a", "c", "rly"):  # opening runs a startup output_off; do it up front
        bench.device(name)
    LOG.clear()
    res = bench.estop("t", "because")
    # "b" cannot open (its startup output_off fails), so its failure is reported by the e-stop.
    assert LOG == [
        ("a", "output_off"),
        ("b", "output_off"),
        ("c", "output_off"),
        ("rly", "relay_set:False"),
    ]
    assert res["latched"] and not res["ok"]
    failed = [r for r in res["results"] if not r["ok"]]
    assert [r["device"] for r in failed] == ["b"] and "boom" in failed[0]["error"]
    assert (tmp_path / "estop.json").exists()


def test_latch_refuses_actuation_but_not_reads(tmp_path: Path) -> None:
    bench = make(tmp_path)
    bench.estop("t", "r")
    LOG.clear()
    with pytest.raises(SafetyRefused) as ei:
        bench.run("t", "a", "set_voltage", voltage=5.0)
    assert ei.value.code == "estopped"
    assert LOG == []
    assert bench.run("t", "a", "read_voltage_dc") == 3.3


def test_estop_does_not_wait_for_lock(tmp_path: Path) -> None:
    bench = make(tmp_path)
    held, release = threading.Event(), threading.Event()

    def holder() -> None:
        with bench.lock.exclusive("other", 1.0):
            held.set()
            release.wait(5)

    th = threading.Thread(target=holder)
    th.start()
    assert held.wait(2)
    try:
        assert bench.estop("t", "r")["latched"]
    finally:
        release.set()
        th.join()


def test_second_bench_sees_latch_and_clear_is_audited(tmp_path: Path) -> None:
    one, two = make(tmp_path), make(tmp_path)
    one.estop("t", "r")
    with pytest.raises(SafetyRefused) as ei:
        two.run("t", "a", "set_voltage", voltage=5.0)
    assert ei.value.code == "estopped"
    two.clear_estop("t")
    assert one.run("t", "a", "set_voltage", voltage=5.0) == {"voltage": 5.0}
    recs = read_entries(tmp_path / "audit.jsonl")
    assert any(r["command"] == "clear_estop" and r["outcome"] == "ok" for r in recs)


def test_audit_records(tmp_path: Path) -> None:
    bench = make(tmp_path)
    bench.estop("t", "r")
    recs = read_entries(tmp_path / "audit.jsonl")
    assert len([r for r in recs if r["command"] == "estop"]) == 1
    assert any(r["command"] == "relay_set" and r["outcome"] == "ok" for r in recs)
    assert any(r["command"] == "output_off" and r["outcome"] == "error" for r in recs)


def test_corrupt_latch_fails_safe(tmp_path: Path) -> None:
    bench = make(tmp_path)
    (tmp_path / "estop.json").write_text("{nope")
    with pytest.raises(SafetyRefused) as ei:
        bench.run("t", "a", "set_voltage", voltage=1.0)
    assert ei.value.code == "estopped"


def _cfg(extra: dict[str, Any]) -> dict[str, Any]:
    return {"devices": {"p": {"class": "psu", "driver": "x", "connection": CONN}}, **extra}


def test_estop_relay_validation() -> None:
    with pytest.raises(ValueError, match="relay device"):
        BenchConfig.model_validate(_cfg({"estop_relay": "p"}))
    with pytest.raises(ValueError, match="not a configured device"):
        BenchConfig.model_validate(_cfg({"estop_relay": "nope"}))


def test_estop_relay_validation_via_load(tmp_path: Path) -> None:
    f = tmp_path / "bench.yaml"
    f.write_text(
        "estop_relay: p\ndevices:\n  p:\n    class: psu\n    driver: x\n"
        "    connection: {type: serial, port: 'loop://'}\n"
    )
    with pytest.raises(BenchConfigError, match="estop_relay"):
        load_bench(f)


def test_cli_estop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "bench.yaml"
    f.write_text(
        f"state_dir: {tmp_path}/state\ndevices:\n  p:\n    class: psu\n    driver: psu\n"
        "    connection: {type: serial, port: 'loop://'}\n"
    )
    monkeypatch.setattr("vaisor.bench.discover_drivers", lambda: {"psu": LogPsu})
    assert main(["--config", str(f), "estop"], transport_factory=factory) == 0
    assert (tmp_path / "state" / "estop.json").exists()
    assert main(["--config", str(f), "estop", "--clear"], transport_factory=factory) == 0
    assert not (tmp_path / "state" / "estop.json").exists()
