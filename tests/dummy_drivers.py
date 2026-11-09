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


class NotADriver:
    pass
