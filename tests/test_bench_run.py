"""Bench.run: every command goes through audit, lock and safety."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from dummy_drivers import DummyMeter, FakeActuatingPsu

from vaisor.audit import AuditError, AuditLog, read_entries
from vaisor.bench import Bench
from vaisor.config import BenchConfig, SerialConnection
from vaisor.drivers import DriverError
from vaisor.lock import BenchLock
from vaisor.measurement import Reading
from vaisor.safety import SafetyRefused
from vaisor.transport import FakeTransport, Transport

CONN = {"type": "serial", "port": "loop://"}


def factory(conn: SerialConnection) -> Transport:
    return FakeTransport()


def psu_config() -> BenchConfig:
    return BenchConfig.model_validate(
        {"devices": {"psu1": {"class": "psu", "driver": "fake", "connection": CONN}}}
    )


def make_bench(
    tmp_path: Path,
    limits: dict[str, Any] | None = None,
    audit: AuditLog | None = None,
    lock: BenchLock | None = None,
    lock_timeout: float = 0.1,
) -> Bench:
    dev: dict[str, Any] = {"class": "psu", "driver": "fake", "connection": CONN}
    if limits is None:
        dev["limits"] = {"max_voltage": 12, "max_current": 1}
    # limits == {} means "omitted": a PSU with no configured limits.
    config = BenchConfig.model_validate({"devices": {"psu1": dev}})
    return Bench.from_config(
        config,
        {"fake": FakeActuatingPsu},
        factory,
        audit=audit or AuditLog(tmp_path / "audit.jsonl"),
        lock=lock or BenchLock(tmp_path / "lock"),
        lock_timeout=lock_timeout,
    )


def entries(tmp_path: Path) -> list[dict[str, Any]]:
    return read_entries(tmp_path / "audit.jsonl")


@pytest.fixture(autouse=True)
def reset_fake() -> None:
    FakeActuatingPsu.calls = []
    FakeActuatingPsu.fail_output_off = False
    FakeActuatingPsu.fail_set_voltage = False


def test_allowed_audits_attempt_then_ok(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    assert bench.run("tester", "psu1", "set_voltage", voltage=5.0) == {"voltage": 5.0}
    recs = entries(tmp_path)
    assert [r["outcome"] for r in recs] == ["attempt", "ok"]
    assert recs[0]["caller"] == "tester" and recs[0]["device"] == "psu1"
    assert recs[0]["command"] == "set_voltage" and recs[0]["args"] == {"voltage": 5.0}
    assert recs[1]["result"] == {"voltage": 5.0}


class ReadingMeter(DummyMeter):
    capabilities = frozenset({"read_measurement"})


def test_result_uses_to_dict(tmp_path: Path) -> None:
    config = BenchConfig.model_validate(
        {"devices": {"dmm1": {"class": "multimeter", "driver": "dummy", "connection": CONN}}}
    )
    bench = Bench.from_config(
        config,
        {"dummy": ReadingMeter},
        factory,
        audit=AuditLog(tmp_path / "audit.jsonl"),
        lock=BenchLock(tmp_path / "lock"),
    )
    assert isinstance(bench.run("t", "dmm1", "read_measurement"), Reading)
    assert entries(tmp_path)[-1]["result"]["value"] == 1.5


def test_refused_without_limits_never_calls_driver(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, limits={})
    with pytest.raises(SafetyRefused) as ei:
        bench.run("t", "psu1", "set_voltage", voltage=5.0)
    assert ei.value.code == "no_limits"
    assert "set_voltage" not in FakeActuatingPsu.calls
    last = entries(tmp_path)[-1]
    assert last["outcome"] == "refused" and last["refusal_code"] == "no_limits"


def test_refused_out_of_range(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    with pytest.raises(SafetyRefused) as ei:
        bench.run("t", "psu1", "set_voltage", voltage=50.0)
    assert ei.value.code == "out_of_range"
    assert "set_voltage" not in FakeActuatingPsu.calls
    assert entries(tmp_path)[-1]["refusal_code"] == "out_of_range"


def test_unknown_device_and_capability_refused(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    with pytest.raises(SafetyRefused) as ei:
        bench.run("t", "nope", "set_voltage", voltage=1.0)
    assert ei.value.code == "unknown_device"
    with pytest.raises(SafetyRefused) as ei:
        bench.run("t", "psu1", "explode")
    assert ei.value.code == "unknown_capability"
    assert [r["outcome"] for r in entries(tmp_path)] == ["refused", "refused"]
    assert FakeActuatingPsu.calls == []


def test_driver_error_audited_and_reraised(tmp_path: Path) -> None:
    FakeActuatingPsu.fail_set_voltage = True
    bench = make_bench(tmp_path)
    with pytest.raises(RuntimeError, match="set_voltage boom"):
        bench.run("t", "psu1", "set_voltage", voltage=5.0)
    recs = entries(tmp_path)
    assert [r["outcome"] for r in recs] == ["attempt", "error"]
    assert "set_voltage boom" in recs[1]["error"]


def test_lock_timeout_refused(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    with BenchLock(tmp_path / "lock").exclusive("other", 0):
        with pytest.raises(SafetyRefused) as ei:
            bench.run("t", "psu1", "set_voltage", voltage=5.0)
        assert ei.value.code == "lock_timeout"
    assert entries(tmp_path)[-1]["refusal_code"] == "lock_timeout"
    assert "set_voltage" not in FakeActuatingPsu.calls


def test_reads_share_but_actuation_is_exclusive(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    with BenchLock(tmp_path / "lock").shared("reader", 0):
        assert bench.run("t", "psu1", "read_voltage_dc") == 3.3
        with pytest.raises(SafetyRefused) as ei:
            bench.run("t", "psu1", "set_voltage", voltage=5.0)
        assert ei.value.code == "lock_timeout"


def test_output_off_runs_once_before_other_commands(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.run("t", "psu1", "set_voltage", voltage=5.0)
    bench.run("t", "psu1", "set_voltage", voltage=6.0)
    assert FakeActuatingPsu.calls[:3] == ["open", "output_off", "set_voltage"]
    assert FakeActuatingPsu.calls.count("output_off") == 1


def test_failing_startup_output_off_closes_and_raises(tmp_path: Path) -> None:
    FakeActuatingPsu.fail_output_off = True
    bench = make_bench(tmp_path)
    with pytest.raises(DriverError, match="output_off"):
        bench.device("psu1")
    assert FakeActuatingPsu.calls == ["open", "output_off", "close"]
    with pytest.raises(DriverError):
        bench.run("t", "psu1", "set_voltage", voltage=5.0)
    assert "set_voltage" not in FakeActuatingPsu.calls


def test_audit_failure_blocks_actuation(tmp_path: Path) -> None:
    class BrokenAudit(AuditLog):
        def record(self, event: dict) -> dict:  # type: ignore[type-arg]
            raise AuditError("disk full")

    bench = make_bench(tmp_path, audit=BrokenAudit(tmp_path / "audit.jsonl"))
    with pytest.raises(AuditError):
        bench.run("t", "psu1", "set_voltage", voltage=5.0)
    assert FakeActuatingPsu.calls == []
