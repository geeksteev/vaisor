"""Base class for all instrument drivers.

A driver declares its :attr:`device_class` and the capability names it offers;
nothing outside the driver knows about vendor protocols.
"""

from __future__ import annotations

from abc import ABC
from types import TracebackType
from typing import ClassVar

from vaisor.config import DeviceClass, DeviceConfig
from vaisor.transport import Transport


class DriverError(Exception):
    """Raised for driver lookup problems and instrument-level failures."""


class Driver(ABC):
    device_class: ClassVar[DeviceClass]
    capabilities: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, name: str, config: DeviceConfig, transport: Transport) -> None:
        self.name = name
        self.config = config
        self.transport = transport

    def open(self) -> None:
        self.transport.open()

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> Driver:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
