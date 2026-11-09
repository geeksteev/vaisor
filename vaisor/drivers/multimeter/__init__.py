"""Multimeter driver interface."""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from vaisor.config import DeviceClass
from vaisor.drivers.base import Driver
from vaisor.measurement import Reading


class Multimeter(Driver):
    device_class: ClassVar[DeviceClass] = DeviceClass.MULTIMETER

    @abstractmethod
    def read_measurement(self) -> Reading:
        """Take one reading of whatever the meter is currently measuring."""
