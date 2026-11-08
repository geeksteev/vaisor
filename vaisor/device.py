"""Device model shown by the front doors."""

from __future__ import annotations

from dataclasses import dataclass

from vaisor.config import DeviceClass, SerialConnection


@dataclass(frozen=True)
class DeviceInfo:
    """A configured device instance and what it can do."""

    name: str
    device_class: DeviceClass
    driver: str
    connection: SerialConnection
    capabilities: tuple[str, ...]
