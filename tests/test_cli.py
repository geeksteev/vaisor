from __future__ import annotations

import json
from pathlib import Path

import pytest

from vaisor import __version__
from vaisor.cli import main
from vaisor.config import SerialConnection
from vaisor.transport import FakeTransport

FIX = Path(__file__).parent / "fixtures" / "extech_383273"
BENCH = (
    "version: 1\n"
    "devices:\n"
    "  dmm1:\n"
    "    class: multimeter\n"
    "    driver: extech_383273\n"
    "    connection: {type: serial, port: /dev/ttyFAKE0, rts: false}\n"
)


def bench_file(tmp_path: Path) -> list[str]:
    p = tmp_path / "bench.yaml"
    p.write_text(BENCH)
    return ["--config", str(p)]


class Factory:
    """Builds one FakeTransport per call from the first exchange of a fixture."""

    def __init__(self, fixture: str, repeat: int = 1) -> None:
        self.fixture = fixture
        self.repeat = repeat
        self.made: list[FakeTransport] = []

    def __call__(self, conn: SerialConnection) -> FakeTransport:
        line = (FIX / f"{self.fixture}.jsonl").read_text().splitlines()[0]
        entry = json.loads(line)
        exchange = (bytes.fromhex(entry["tx"]), bytes.fromhex(entry["rx"]))
        t = FakeTransport([exchange] * self.repeat)
        self.made.append(t)
        return t


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_args_returns_zero() -> None:
    assert main([]) == 0


def test_devices_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("dcv_neg_12_3")
    assert main([*bench_file(tmp_path), "devices"], transport_factory=f) == 0
    out = capsys.readouterr().out
    for word in ("dmm1", "multimeter", "extech_383273", "/dev/ttyFAKE0", "read_measurement"):
        assert word in out
    assert f.made == []


def test_devices_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("dcv_neg_12_3")
    assert main([*bench_file(tmp_path), "devices", "--json"], transport_factory=f) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["name"] == "dmm1"
    assert data[0]["class"] == "multimeter"
    assert data[0]["connection"]["port"] == "/dev/ttyFAKE0"
    assert data[0]["capabilities"] == ["read_measurement"]
    assert f.made == []


def test_read_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("dcv_neg_12_3")
    assert main([*bench_file(tmp_path), "read", "dmm1"], transport_factory=f) == 0
    out = capsys.readouterr().out
    assert "-12.3" in out and "voltage_dc" in out
    assert not f.made[0].is_open


def test_read_overrange(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("overrange")
    assert main([*bench_file(tmp_path), "read", "dmm1"], transport_factory=f) == 0
    assert "OL" in capsys.readouterr().out


def test_read_jsonl(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("dcv_neg_12_3")
    assert main([*bench_file(tmp_path), "read", "dmm1", "--jsonl"], transport_factory=f) == 0
    assert json.loads(capsys.readouterr().out)["value"] == -12.3


def test_watch_jsonl_count(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("dcv_neg_12_3", repeat=3)
    argv = [
        *bench_file(tmp_path), "read", "dmm1", "--watch", "--jsonl",
        "--count", "3", "--interval", "0",
    ]
    assert main(argv, transport_factory=f) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    assert all(json.loads(line)["value"] == -12.3 for line in lines)
    assert not f.made[0].is_open


def test_watch_gives_up_after_five_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = Factory("silent")
    argv = [*bench_file(tmp_path), "read", "dmm1", "--watch", "--interval", "0"]
    assert main(argv, transport_factory=f) == 1
    assert capsys.readouterr().err.count("timeout") == 5


def test_missing_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(tmp_path / "nope.yaml"), "devices"]) == 2
    assert "not found" in capsys.readouterr().err


def test_config_from_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VAISOR_CONFIG", bench_file(tmp_path)[1])
    assert main(["devices"], transport_factory=Factory("dcv_neg_12_3")) == 0
    assert "dmm1" in capsys.readouterr().out


def test_unknown_device(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("dcv_neg_12_3")
    assert main([*bench_file(tmp_path), "read", "nope"], transport_factory=f) == 2
    err = capsys.readouterr().err
    assert "nope" in err and "dmm1" in err
    assert f.made == []


def test_silent_meter(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = Factory("silent")
    assert main([*bench_file(tmp_path), "read", "dmm1"], transport_factory=f) == 1
    assert "timeout" in capsys.readouterr().err
    assert not f.made[0].is_open
