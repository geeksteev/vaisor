from __future__ import annotations

import json

import pytest
from dummy_drivers import DummyMeter, DummyPsu

from vaisor.bench import Bench
from vaisor.config import BenchConfig, DeviceClass, SerialConnection
from vaisor.drivers import Driver, DriverError
from vaisor.measurement import Reading
from vaisor.transport import FakeTransport, Transport

DRIVERS: dict[str, type[Driver]] = dict(dummy=DummyMeter, psu=DummyPsu)


def make_config(driver: str = "dummy", cls: str = "multimeter") -> BenchConfig:
    return BenchConfig.model_validate(
        dict(
            devices=dict(
                dmm1={
                    "class": cls,
                    "driver": driver,
                    "connection": {"type": "serial", "port": "loop://"},
                }
            )
        )
    )


class Factory:
    def __init__(self) -> None:
        self.made: list[FakeTransport] = []

    def __call__(self, conn: SerialConnection) -> Transport:
        t = FakeTransport()
        self.made.append(t)
        return t


def test_devices_do_not_open_transports() -> None:
    factory = Factory()
    bench = Bench.from_config(make_config(), DRIVERS, factory)
    (info,) = bench.devices()
    assert info.name == "dmm1"
    assert info.device_class is DeviceClass.MULTIMETER
    assert info.driver == "dummy"
    assert info.capabilities == ("read_current_dc", "read_voltage_dc")
    assert factory.made == []


def test_device_is_lazy_cached_and_closed() -> None:
    factory = Factory()
    bench = Bench.from_config(make_config(), DRIVERS, factory)
    drv = bench.device("dmm1")
    assert bench.device("dmm1") is drv
    assert len(factory.made) == 1 and factory.made[0].is_open
    assert isinstance(drv, DummyMeter)
    assert drv.read_measurement().value == 1.5
    bench.close()
    assert not factory.made[0].is_open
    bench.close()  # idempotent


class FailingOpenTransport(FakeTransport):
    def open(self) -> None:
        super().open()
        raise OSError("port busy")


def test_failed_open_closes_transport_and_allows_retry() -> None:
    made: list[FakeTransport] = []

    def factory(conn: SerialConnection) -> Transport:
        t = FailingOpenTransport() if not made else FakeTransport()
        made.append(t)
        return t

    bench = Bench.from_config(make_config(), DRIVERS, factory)
    with pytest.raises(DriverError, match="dmm1.*port busy"):
        bench.device("dmm1")
    assert not made[0].is_open
    assert bench._open == {}
    assert bench.device("dmm1").transport is made[1]
    assert made[1].is_open
    bench.close()


def test_close_without_opening_anything() -> None:
    factory = Factory()
    Bench.from_config(make_config(), DRIVERS, factory).close()
    assert factory.made == []


def test_unknown_device() -> None:
    bench = Bench.from_config(make_config(), DRIVERS, Factory())
    with pytest.raises(DriverError, match=r"unknown device 'x' \(devices: dmm1\)"):
        bench.device("x")


def test_unknown_driver() -> None:
    with pytest.raises(
        DriverError,
        match=r"device 'dmm1': unknown driver 'foo' \(installed: dummy, psu\)",
    ):
        Bench.from_config(make_config("foo"), DRIVERS, Factory())


def test_class_mismatch() -> None:
    with pytest.raises(
        DriverError, match="device 'dmm1': driver 'psu' is a psu, config says multimeter"
    ):
        Bench.from_config(make_config("psu"), DRIVERS, Factory())


def test_reading_to_dict_roundtrips_json() -> None:
    r = Reading(
        device="dmm1", value=None, unit="V", function="voltage_dc",
        overrange=True, raw=b"\x02\xff",
    )
    d = json.loads(json.dumps(r.to_dict()))
    assert d["raw"] == "02ff"
    assert d["value"] is None and d["overrange"] is True
    assert d["timestamp"].endswith("+00:00")
