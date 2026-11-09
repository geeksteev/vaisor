"""The Bench runtime: configured devices, created lazily."""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from vaisor.audit import AuditLog
from vaisor.config import BenchConfig, SerialConnection, typed_limits
from vaisor.device import DeviceInfo
from vaisor.drivers.base import Driver, DriverError
from vaisor.lock import BenchLock, LockTimeout
from vaisor.registry import discover_drivers, get_driver
from vaisor.safety import ACTUATING_CAPABILITIES, ESTOPPED, SafetyRefused, check_command
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
        self._lock_caller = "unknown"
        if self.lock.on_stale is None:
            self.lock.on_stale = self._audit_stale_lock
        self.lock_timeout = lock_timeout
        self.config = config
        self.estop_path = state / "estop.json"
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

    def _audit_stale_lock(self, owner: dict[str, Any]) -> None:
        """Audit the takeover of a stale lock left by a dead or expired holder."""
        self.audit.record(
            {"caller": self._lock_caller, "command": "lock_takeover", "outcome": "ok",
             "device": None, "args": {"stale_owner": owner}}
        )

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
            if isinstance(extra.get("args"), dict):
                # NaN/inf are not JSON-safe; log them as strings so refusals can be audited.
                extra["args"] = {
                    k: repr(v) if isinstance(v, float) and not math.isfinite(v) else v
                    for k, v in extra["args"].items()
                }
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

        actuating = capability in ACTUATING_CAPABILITIES
        self._lock_caller = caller
        if actuating:
            self._refuse_if_latched(refuse)
        record("attempt", args=args)
        guard = (
            self.lock.exclusive(caller, self.lock_timeout)
            if actuating
            else self.lock.shared(caller, self.lock_timeout)
        )
        try:
            with guard:
                if actuating:
                    # Re-check: the latch may have been set while we waited for the lock.
                    self._refuse_if_latched(refuse)
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

    def estop_state(self) -> dict[str, Any] | None:
        """The latch contents if the e-stop is latched, else ``None``.

        An unreadable or corrupt latch file still counts as latched (fail safe).
        """
        try:
            text = self.estop_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            return {"error": f"cannot read latch: {exc}"}
        try:
            data = json.loads(text)
        except ValueError:
            return {"error": "corrupt latch file"}
        return data if isinstance(data, dict) else {"error": "corrupt latch file"}

    def _refuse_if_latched(self, refuse: Callable[[str, str], SafetyRefused]) -> None:
        state = self.estop_state()
        if state is not None:
            why = state.get("reason") or state.get("error") or "no reason given"
            raise refuse(ESTOPPED, f"e-stop is latched ({why}); clear it to actuate")

    def estop(self, caller: str, reason: str = "") -> dict[str, Any]:
        """Latch the e-stop, turn off every output and open the e-stop relay.

        Never takes the bench lock and keeps going past individual failures.
        Returns ``{"latched", "ok", "results"}``; each result has ``device``,
        ``command``, ``ok`` and ``error`` when it failed.
        """
        results: list[dict[str, Any]] = []

        def audit(command: str, outcome: str, device: str | None, **extra: Any) -> None:
            try:
                self.audit.record(
                    {"caller": caller, "command": command, "outcome": outcome,
                     "device": device, **extra}
                )
            except Exception:
                pass  # the e-stop must proceed even if auditing is broken

        def step(device: str, command: str, **args: Any) -> None:
            audit(command, "attempt", device, args=args)
            try:
                getattr(self.device(device), command)(**args)
            except Exception as exc:
                results.append(
                    {"device": device, "command": command, "ok": False, "error": str(exc)}
                )
                audit(command, "error", device, args=args, error=str(exc))
            else:
                results.append({"device": device, "command": command, "ok": True})
                audit(command, "ok", device, args=args)

        latched = True
        try:
            self.estop_path.parent.mkdir(parents=True, exist_ok=True)
            body = {
                "caller": caller,
                "reason": reason,
                "time": datetime.now(timezone.utc).isoformat(),
            }
            fd, tmp = tempfile.mkstemp(dir=self.estop_path.parent, prefix=".estop-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(body, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.estop_path)
        except Exception as exc:
            latched = False
            results.append({"device": None, "command": "latch", "ok": False, "error": str(exc)})

        for name in self.config.devices:
            if "output_off" in self._classes[name].capabilities:
                step(name, "output_off")
        relay = self.config.estop_relay
        if relay is not None:
            step(relay, "relay_set", closed=False)

        ok = latched and all(r["ok"] for r in results)
        extra: dict[str, Any] = {} if ok else {"error": "one or more e-stop steps failed"}
        audit(
            "estop", "ok" if ok else "error", None,
            args={"reason": reason},
            result={"latched": latched, "ok": ok, "results": results},
            **extra,
        )
        return {"latched": latched, "ok": ok, "results": results}

    def clear_estop(self, caller: str) -> bool:
        """Remove the latch. Returns whether one was present. Audited."""
        base: dict[str, Any] = {"caller": caller, "command": "clear_estop", "device": None}
        self.audit.record({**base, "outcome": "attempt"})
        try:
            self.estop_path.unlink()
            existed = True
        except FileNotFoundError:
            existed = False
        except OSError as exc:
            self.audit.record({**base, "outcome": "error", "error": str(exc)})
            raise DriverError(f"cannot clear e-stop latch: {exc}") from exc
        self.audit.record({**base, "outcome": "ok", "result": {"was_latched": existed}})
        return existed

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
