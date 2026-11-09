"""Pure safety policy: decides whether a command may reach a driver.

No I/O happens here. :func:`check_command` raises :class:`SafetyRefused` or
returns ``None``. Default is refusal: an actuating command needs usable limits.

Argument names per capability:

* ``set_voltage``: ``voltage``; ``set_current``: ``current``
* ``output_on`` / ``output_off``: none
* ``relay_set``: ``closed`` (bool)
* ``move``: ``position`` and ``speed``
* ``flash``: refused until a flasher limits model exists
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from vaisor.limits import MotionLimits, PsuLimits

ACTUATING_CAPABILITIES = frozenset(
    {"set_voltage", "set_current", "output_on", "relay_set", "move", "flash"}
)
SAFE_CAPABILITIES = frozenset({"output_off"})

NO_LIMITS = "no_limits"
OUT_OF_RANGE = "out_of_range"
INVALID_ARGUMENT = "invalid_argument"
UNKNOWN_CAPABILITY = "unknown_capability"
ESTOPPED = "estopped"


class SafetyRefused(Exception):
    """A command was refused. ``code`` is machine-readable, ``message`` is for humans."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _number(name: str, args: Mapping[str, Any]) -> float:
    value = args[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SafetyRefused(INVALID_ARGUMENT, f"{name} must be a number")
    if not math.isfinite(value):
        raise SafetyRefused(INVALID_ARGUMENT, f"{name} must be finite (not NaN or infinity)")
    return float(value)


def _exact_args(args: Any, names: set[str]) -> Mapping[str, Any]:
    if not isinstance(args, Mapping):
        raise SafetyRefused(INVALID_ARGUMENT, "arguments must be a mapping")
    missing = sorted(names - set(args))
    if missing:
        raise SafetyRefused(INVALID_ARGUMENT, f"missing argument(s): {', '.join(missing)}")
    extra = sorted(str(k) for k in set(args) - names)
    if extra:
        raise SafetyRefused(INVALID_ARGUMENT, f"unexpected argument(s): {', '.join(extra)}")
    return args


def _no_limits(device_name: str, capability: str) -> SafetyRefused:
    return SafetyRefused(
        NO_LIMITS, f"{capability} on {device_name!r} refused: no usable limits configured"
    )


def _limits_as(model: type[BaseModel], limits: Any, device_name: str, capability: str) -> Any:
    if isinstance(limits, model):
        return limits
    try:
        if isinstance(limits, Mapping) and limits:
            return model.model_validate(dict(limits))
    except ValidationError:
        pass
    raise _no_limits(device_name, capability)


def check_command(
    device_name: str,
    device_class: Any,
    limits: Any,
    capability: str,
    args: Mapping[str, Any] | None,
) -> None:
    """Raise :class:`SafetyRefused` unless the command is allowed."""
    if not isinstance(capability, str) or not capability:
        raise SafetyRefused(UNKNOWN_CAPABILITY, "capability must be a non-empty string")
    if capability in SAFE_CAPABILITIES:
        return
    if capability not in ACTUATING_CAPABILITIES:
        return  # read capability
    args = {} if args is None else args

    if capability in ("set_voltage", "set_current", "output_on"):
        psu = _limits_as(PsuLimits, limits, device_name, capability)
        if capability == "output_on":
            _exact_args(args, set())
        elif capability == "set_voltage":
            v = _number("voltage", _exact_args(args, {"voltage"}))
            if not psu.min_voltage <= v <= psu.max_voltage:
                raise SafetyRefused(
                    OUT_OF_RANGE,
                    f"voltage {v} outside [{psu.min_voltage}, {psu.max_voltage}] "
                    f"for {device_name!r}",
                )
        else:
            i = _number("current", _exact_args(args, {"current"}))
            if not 0 <= i <= psu.max_current:
                raise SafetyRefused(
                    OUT_OF_RANGE,
                    f"current {i} outside [0, {psu.max_current}] for {device_name!r}",
                )
    elif capability == "move":
        motion = _limits_as(MotionLimits, limits, device_name, capability)
        a = _exact_args(args, {"position", "speed"})
        pos, speed = _number("position", a), _number("speed", a)
        if not motion.position_min <= pos <= motion.position_max:
            raise SafetyRefused(
                OUT_OF_RANGE,
                f"position {pos} outside [{motion.position_min}, {motion.position_max}]",
            )
        if not 0 <= speed <= motion.max_speed:
            raise SafetyRefused(OUT_OF_RANGE, f"speed {speed} outside [0, {motion.max_speed}]")
    elif capability == "relay_set":
        # No relay limits model exists yet; any non-empty limits mapping counts as configured.
        if not isinstance(limits, Mapping) or not limits:
            raise _no_limits(device_name, capability)
        closed = _exact_args(args, {"closed"})["closed"]
        if not isinstance(closed, bool):
            raise SafetyRefused(INVALID_ARGUMENT, "closed must be a bool")
    else:  # flash
        raise SafetyRefused(
            NO_LIMITS, f"flash on {device_name!r} refused: no flasher limits model exists yet"
        )
