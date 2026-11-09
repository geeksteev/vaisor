from __future__ import annotations

import json
from pathlib import Path

import pytest

from vaisor import __version__
from vaisor.cli import main
from vaisor.config import SerialConnection
from vaisor.transport import FakeTransport

FIX = Path(__file__).parent / "fixtures" / "extech_383273"

BENCH = """\
version: 1
name: test
devices:
  dmm1:
    class: multimeter
    driver: extech_383273
    connection:
      type: serial
      port: /dev/ttyFAKE0
      timeout: 0.01
      rts: false
    limits: {}
"""


@pytest.fixture
def bench_file(tmp_path: Path) -> str:
    path = tmp_path / "bench.yaml"
    path.write_text(BENCH, encoding="utf-8")
    return str(path)


class Factory:
    """Builds FakeTransports from a fixture and records how often it is used."""

    def __init__(self, fixture: str, repeat: int = 1) -> None:
        self.fixture = fixture
        self.repeat = repeat
        self.created: list[FakeTransport] = []

    def __call__(self, conn: SerialConnection) -> FakeTransport:
        t = FakeTransport.from_fixture(FIX / f"{self.fixture}.jsonl")
        if self.repeat > 1:
            script = list(t._script) * self.repeat
            t = FakeTransport(script)
        self.created.append(t)
        return t


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_args_returns_zero() -> None:
    assert main([]) == 0


def test_devices_table(bench_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("ohm_12_3k")
    assert main(["--config", bench_file, "devices"], transport_factory=f) == 0
    out = capsys.readouterr().out
    for word in ("dmm1", "multimeter", "extech_383273", "/dev/ttyFAKE0", "read_measurement"):
        assert word in out
    assert f.created == []


def test_devices_json(bench_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("ohm_12_3k")
    assert main(["--config", bench_file, "devices", "--json"], transport_factory=f) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == [
        {
            "name": "dmm1",
            "class": "multimeter",
            "driver": "extech_383273",
            "port": "/dev/ttyFAKE0",
            "capabilities": ["read_measurement"],
        }
    ]
    assert f.created == []


def test_config_from_env(
    bench_file: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VAISOR_CONFIG", bench_file)
    assert main(["devices"]) == 0
    assert "dmm1" in capsys.readouterr().out


def test_read_text(bench_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("ohm_12_3k")
    assert main(["--config", bench_file, "read", "dmm1"], transport_factory=f) == 0
    out = capsys.readouterr().out
    assert "12.3" in out and "kΩ" in out
    assert out.strip() == "dmm1: 12.3 kΩ (resistance, 200kΩ)"
    assert not f.created[0].is_open


def test_read_overrange(bench_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("overrange")
    assert main(["--config", bench_file, "read", "dmm1"], transport_factory=f) == 0
    assert "OL" in capsys.readouterr().out


def test_read_jsonl(bench_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("ohm_12_3k")
    assert main(["--config", bench_file, "read", "dmm1", "--jsonl"], transport_factory=f) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["value"] == 12.3
    assert data["device"] == "dmm1"


def test_watch_jsonl_count(bench_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("ohm_12_3k", repeat=3)
    argv = ["--config", bench_file, "read", "dmm1", "--watch", "--jsonl",
            "--count", "3", "--interval", "0"]
    assert main(argv, transport_factory=f) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    assert all(json.loads(line)["value"] == 12.3 for line in lines)
    assert not f.created[0].is_open


def test_watch_error_then_recovers(
    bench_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    def factory(conn: SerialConnection) -> FakeTransport:
        return FakeTransport([(b" ", b""), (b" ", bytes.fromhex("020c21b103"))])

    argv = ["--config", bench_file, "read", "dmm1", "--watch", "--jsonl",
            "--count", "2", "--interval", "0"]
    assert main(argv, transport_factory=factory) == 0
    cap = capsys.readouterr()
    assert len(cap.out.splitlines()) == 1
    assert "timeout" in cap.err


def test_watch_gives_up_after_five_errors(
    bench_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    f = Factory("silent")
    argv = ["--config", bench_file, "read", "dmm1", "--watch", "--interval", "0"]
    assert main(argv, transport_factory=f) == 1
    cap = capsys.readouterr()
    assert cap.err.count("timeout") == 5
    assert cap.out == ""


def test_negative_interval_rejected(bench_file: str) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--config", bench_file, "read", "dmm1", "--watch", "--interval", "-1"])
    assert info.value.code == 2


def test_missing_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(tmp_path / "nope.yaml"), "devices"]) == 2
    assert capsys.readouterr().err


def test_unknown_device(bench_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("ohm_12_3k")
    assert main(["--config", bench_file, "read", "nope"], transport_factory=f) == 2
    err = capsys.readouterr().err
    assert "nope" in err and "dmm1" in err
    assert f.created == []


def test_silent_meter(bench_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("silent")
    assert main(["--config", bench_file, "read", "dmm1"], transport_factory=f) == 1
    assert "timeout" in capsys.readouterr().err
    assert not f.created[0].is_open


def test_read_writes_audit_line(bench_file: str, tmp_path: Path) -> None:
    rc = main(
        ["--config", bench_file, "read", "dmm1", "--caller", "me"],
        transport_factory=Factory("dcv_neg_12_3"),
    )
    assert rc == 0
    lines = (tmp_path / ".vaisor" / "audit.jsonl").read_text().splitlines()
    recs = [json.loads(x) for x in lines]
    assert [r["outcome"] for r in recs] == ["attempt", "ok"]
    assert recs[0]["caller"] == "me" and recs[0]["command"] == "read_measurement"
