"""The Bench runtime: configured devices, created lazily."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from vaisor.audit import AuditLog
from vaisor.config import BenchConfig, SerialConnection, typed_limits
from vaisor.device import DeviceInfo
from vaisor.drivers.base import Driver, DriverError
from vaisor.lock import BenchLock, LockTimeout
from vaisor.registry import discover_drivers, get_driver
from vaisor.safety import ACTUATING_CAPABILITIES, SafetyRefused, check_command
from vaisor.transport import Transport, make_transport

UNKNOWN_DEVICE = "unknown_device"
UNKNOWN_CAPABILITY = "unknown_capability"
LOCK_TIMEOUT = "lock_timeout"
DEFAULT_LOCK_TIMEOUT = 5.0


def _json_safe(result: Any) -> Any:
    """Turn a driver result into something the audit log accepts."""
    if hasattr(result, "to_dict"):
        result = result.to_dict()
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError):
        return repr(result)
    return result


class Bench:
    def __init__(
        self,
        config: BenchConfig,
        driver_classes: dict[str, type[Driver]],
        transport_factory: Callable[[SerialConnection], Transport],
        *,
        audit: AuditLog | None = None,
        lock: BenchLock | None = None,
        base_dir: str | Path | None = None,
        lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
    ) -> None:
        state = Path(config.state_dir)
        if not state.is_absolute() and base_dir is not None:
            state = Path(base_dir) / state
        self.audit = audit if audit is not None else AuditLog(state / "audit.jsonl")
        self.lock = lock if lock is not None else BenchLock(state / "bench.lock")
        self.lock_timeout = lock_timeout
        self.config = config
        self._classes = driver_classes
        self._transport_factory = transport_factory
        self._open: dict[str, Driver] = {}

    @classmethod
    def from_config(
        cls,
        config: BenchConfig,
        drivers: dict[str, type[Driver]] | None = None,
        transport_factory: Callable[[SerialConnection], Transport] = make_transport,
        **kwargs: Any,
    ) -> Bench:
        """Resolve every device's driver; no transport is created or opened."""
        if drivers is None:
            drivers = discover_drivers()
        classes: dict[str, type[Driver]] = {}
        for name, dev in config.devices.items():
            try:
                klass = get_driver(dev.driver, drivers)
            except DriverError as exc:
                raise DriverError(f"device '{name}': {exc}") from None
            if klass.device_class != dev.device_class:
                raise DriverError(
                    f"device '{name}': driver '{dev.driver}' is a "
                    f"{klass.device_class.value}, config says {dev.device_class.value}"
                )
            classes[name] = klass
        return cls(config, classes, transport_factory, **kwargs)

    def devices(self) -> list[DeviceInfo]:
        return [
            DeviceInfo(
                name=name,
                device_class=dev.device_class,
                driver=dev.driver,
                connection=dev.connection,
                capabilities=tuple(sorted(self._classes[name].capabilities)),
            )
            for name, dev in self.config.devices.items()
        ]

    def device(self, name: str) -> Driver:
        """Return the driver for ``name``, creating and opening it on first use."""
        if name not in self.config.devices:
            valid = ", ".join(sorted(self.config.devices)) or "none"
            raise DriverError(f"unknown device '{name}' (devices: {valid})")
        drv = self._open.get(name)
        if drv is None:
            dev = self.config.devices[name]
            transport = self._transport_factory(dev.connection)
            drv = self._classes[name](name, dev, transport)
            try:
                drv.open()
            except Exception as exc:
                # Release the port so a later retry is not blocked.
                try:
                    transport.close()
                except Exception:
                    pass
                if isinstance(exc, DriverError):
                    raise
                raise DriverError(f"device '{name}': failed to open: {exc}") from exc
            if "output_off" in self._classes[name].capabilities:
                # Outputs must be off before anything else touches the device.
                try:
                    drv.output_off()  # type: ignore[attr-defined]
                except Exception as exc:
                    try:
                        drv.close()
                    except Exception:
                        pass
                    raise DriverError(
                        f"device '{name}': startup output_off failed: {exc}"
                    ) from exc
            self._open[name] = drv
        return drv

    def run(self, caller: str, device: str, capability: str, **args: Any) -> Any:
        """Run a capability on a device through audit, lock and safety.

        The only way front doors invoke driver methods. Raises ``SafetyRefused``
        for refusals (all audited) and re-raises driver errors after auditing.
        If the audit log cannot be written, ``AuditError`` propagates and the
        command does not run.
        """

        def record(outcome: str, **extra: Any) -> None:
            self.audit.record(
                {"caller": caller, "command": capability, "outcome": outcome,
                 "device": device, **extra}
            )

        def refuse(code: str, message: str) -> SafetyRefused:
            record("refused", args=args, refusal_code=code, error=message)
            return SafetyRefused(code, message)

        dev = self.config.devices.get(device)
        if dev is None:
            valid = ", ".join(sorted(self.config.devices)) or "none"
            raise refuse(UNKNOWN_DEVICE, f"unknown device '{device}' (devices: {valid})")
        if capability not in self._classes[device].capabilities:
            raise refuse(
                UNKNOWN_CAPABILITY, f"device '{device}' has no capability '{capability}'"
            )

        record("attempt", args=args)
        actuating = capability in ACTUATING_CAPABILITIES
        guard = (
            self.lock.exclusive(caller, self.lock_timeout)
            if actuating
            else self.lock.shared(caller, self.lock_timeout)
        )
        try:
            with guard:
                try:
                    limits: Any = typed_limits(dev)
                except ValidationError:
                    limits = dev.limits
                try:
                    check_command(device, dev.device_class, limits, capability, args)
                except SafetyRefused as exc:
                    record("refused", args=args, refusal_code=exc.code, error=exc.message)
                    raise
                try:
                    result = getattr(self.device(device), capability)(**args)
                except Exception as exc:
                    record("error", args=args, error=str(exc))
                    raise
                record("ok", args=args, result=_json_safe(result))
                return result
        except LockTimeout as exc:
            raise refuse(LOCK_TIMEOUT, str(exc)) from exc

    def close(self) -> None:
        """Close every device that was opened; keeps going if one fails."""
        errors: list[str] = []
        while self._open:
            name, drv = self._open.popitem()
            try:
                drv.close()
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        if errors:
            raise DriverError("error closing devices: " + "; ".join(errors))

    def __enter__(self) -> Bench:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
