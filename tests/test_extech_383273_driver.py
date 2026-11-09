from __future__ import annotations

from pathlib import Path

import pytest

from vaisor.config import DeviceConfig, load_bench
from vaisor.drivers import DriverError
from vaisor.drivers.multimeter.extech_383273 import RESERVED_BYTES, Extech383273
from vaisor.registry import discover_drivers
from vaisor.transport import FakeTransport

ROOT = Path(__file__).parent.parent
FIX = Path(__file__).parent / "fixtures" / "extech_383273"


def make(fixture: str, **conn: object) -> tuple[Extech383273, FakeTransport]:
    cfg = DeviceConfig.model_validate(
        {
            "class": "multimeter",
            "driver": "extech_383273",
            "connection": {"type": "serial", "port": "loop://", "rts": False, **conn},
        }
    )
    t = FakeTransport.from_fixture(FIX / f"{fixture}.jsonl")
    return Extech383273("dmm1", cfg, t), t


def test_dcv_negative() -> None:
    drv, t = make("dcv_neg_12_3")
    with drv:
        r = drv.read_measurement()
    assert r.value == -12.3
    assert r.device == "dmm1"
    assert t.rts_history == [False]
    assert t.written == [b" "]


def test_overrange() -> None:
    drv, _ = make("overrange")
    with drv:
        r = drv.read_measurement()
    assert r.value is None and r.overrange


def test_resync() -> None:
    drv, t = make("garbage_then_frame")
    with drv:
        assert drv.read_measurement().value == -12.3
    assert t.written == [b" "]


def test_silence_times_out() -> None:
    drv, t = make("silent")
    with drv, pytest.raises(DriverError, match="timeout"):
        drv.read_measurement()
    assert t.written == [b" "]


def test_too_much_garbage() -> None:
    drv, t = make("silent")
    with drv:
        t.reset_input = lambda: None  # type: ignore[method-assign]
        t._rx += b"\xff" * 40
        with pytest.raises(DriverError, match="no frame start"):
            drv.read_measurement()


@pytest.mark.parametrize("byte", list(RESERVED_BYTES))
def test_send_refuses_reserved(byte: int) -> None:
    drv, t = make("silent")
    with drv, pytest.raises(DriverError, match="reserved"):
        drv._send(bytes([byte]))
    assert t.written == []


def test_warns_on_wrong_line_settings(caplog: pytest.LogCaptureFixture) -> None:
    drv, _ = make("silent", baudrate=19200)
    with caplog.at_level("WARNING"):
        drv.open()
    assert "9600 8N1" in caplog.text


def test_entry_point_registered() -> None:
    assert discover_drivers()["extech_383273"] is Extech383273


def test_example_bench_loads() -> None:
    cfg = load_bench(ROOT / "examples" / "devices" / "extech_383273.yaml")
    assert cfg.devices["dmm1"].driver == "extech_383273"
