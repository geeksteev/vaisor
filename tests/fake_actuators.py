"""Fake actuating drivers for end-to-end safety tests. No real hardware or ports."""

from __future__ import annotations

from typing import Any, ClassVar

from vaisor.config import DeviceClass
from vaisor.drivers.base import Driver


class _Logged(Driver):
    """Keeps a per-instance call log of ``(method, kwargs)``."""

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _log(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


class FakePSU(_Logged):
    device_class: ClassVar[DeviceClass] = DeviceClass.PSU
    capabilities: ClassVar[frozenset[str]] = frozenset(
        {"set_voltage", "set_current", "output_on", "output_off", "read_measurement"}
    )

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.voltage = 0.0
        self.current = 0.0
        self.output = False

    def set_voltage(self, voltage: float) -> dict[str, float]:
        self._log("set_voltage", voltage=voltage)
        self.voltage = voltage
        return {"voltage": voltage}

    def set_current(self, current: float) -> dict[str, float]:
        self._log("set_current", current=current)
        self.current = current
        return {"current": current}

    def output_on(self) -> None:
        self._log("output_on")
        self.output = True

    def output_off(self) -> None:
        self._log("output_off")
        self.output = False

    def read_measurement(self) -> dict[str, Any]:
        self._log("read_measurement")
        return {"voltage": self.voltage if self.output else 0.0, "output": self.output}


class FakeRelay(_Logged):
    device_class: ClassVar[DeviceClass] = DeviceClass.RELAY
    capabilities: ClassVar[frozenset[str]] = frozenset({"relay_set", "output_off"})

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.closed = True  # contacts closed = power flowing

    def relay_set(self, closed: bool) -> None:
        self._log("relay_set", closed=closed)
        self.closed = closed

    def output_off(self) -> None:
        self._log("output_off")
        self.closed = False
