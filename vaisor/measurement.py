"""Measurement results returned by drivers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Reading:
    """One measurement from a multimeter."""

    device: str
    value: float | None
    """``None`` means overrange or a special display (e.g. open circuit)."""
    unit: str | None = None
    function: str = "unknown"
    range: str | None = None
    overrange: bool = False
    raw: bytes = b""
    timestamp: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form: ``raw`` as hex, ``timestamp`` as ISO 8601."""
        return {
            "device": self.device,
            "value": self.value,
            "unit": self.unit,
            "function": self.function,
            "range": self.range,
            "overrange": self.overrange,
            "raw": self.raw.hex(),
            "timestamp": self.timestamp.isoformat(),
        }
