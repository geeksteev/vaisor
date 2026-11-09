"""Device model shown by the front doors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vaisor.config import DeviceClass, SerialConnection


@dataclass(frozen=True)
class DeviceInfo:
    """A configured device instance and what it can do."""

    name: str
    device_class: DeviceClass
    driver: str
    connection: SerialConnection
    capabilities: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form shared by the CLI (``devices --json``) and MCP."""
        return {
            "name": self.name,
            "class": self.device_class.value,
            "driver": self.driver,
            "port": self.connection.port,
            "capabilities": list(self.capabilities),
        }
