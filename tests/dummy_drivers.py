"""Dummy drivers for registry and bench tests."""

from __future__ import annotations

from typing import ClassVar

from vaisor.config import DeviceClass
from vaisor.drivers.base import Driver
from vaisor.drivers.multimeter import Multimeter
from vaisor.measurement import Reading


class DummyMeter(Multimeter):
    capabilities: ClassVar[frozenset[str]] = frozenset({"read_voltage_dc", "read_current_dc"})

    def read_measurement(self) -> Reading:
        return Reading(device=self.name, value=1.5, unit="V", function="voltage_dc")


class DummyPsu(Driver):
    device_class: ClassVar[DeviceClass] = DeviceClass.PSU
    capabilities: ClassVar[frozenset[str]] = frozenset({"set_voltage"})


class FakeActuatingPsu(Driver):
    """Records every method call so tests can prove what did (not) reach the driver."""

    device_class: ClassVar[DeviceClass] = DeviceClass.PSU
    capabilities: ClassVar[frozenset[str]] = frozenset(
        {"set_voltage", "output_on", "output_off", "read_voltage_dc"}
    )
    fail_output_off: ClassVar[bool] = False
    fail_set_voltage: ClassVar[bool] = False
    calls: ClassVar[list[str]] = []

    def open(self) -> None:
        super().open()
        self.calls.append("open")

    def close(self) -> None:
        self.calls.append("close")
        super().close()

    def output_off(self) -> None:
        self.calls.append("output_off")
        if self.fail_output_off:
            raise RuntimeError("output_off boom")

    def output_on(self) -> None:
        self.calls.append("output_on")

    def set_voltage(self, voltage: float) -> dict[str, float]:
        self.calls.append("set_voltage")
        if self.fail_set_voltage:
            raise RuntimeError("set_voltage boom")
        return {"voltage": voltage}

    def read_voltage_dc(self) -> float:
        self.calls.append("read_voltage_dc")
        return 3.3


class NotADriver:
    pass
