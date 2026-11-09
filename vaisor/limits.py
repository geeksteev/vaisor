"""Typed, validated limits per device class.

``limits`` in ``bench.yaml`` stays a plain mapping on the device config, but it
is validated against the model for the device's class so that the safety layer
can rely on its shape. Numbers must be finite; booleans and strings are not
accepted where a number is expected.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class LimitsError(ValueError):
    """Invalid limits. ``issues`` is a list of ``(path, message)``; path is relative to ``limits``."""

    def __init__(self, issues: list[tuple[str, str]]) -> None:
        self.issues = issues
        super().__init__("; ".join(f"{p}: {m}" if p else m for p, m in issues))


class _StrictLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _reject_non_numbers(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a number")
    return value


_NUMBER = {"allow_inf_nan": False}


class PsuLimits(_StrictLimits):
    max_voltage: float = Field(gt=0, **_NUMBER)
    max_current: float = Field(gt=0, **_NUMBER)
    min_voltage: float = Field(default=0, ge=0, **_NUMBER)

    @field_validator("*", mode="before")
    @classmethod
    def _numbers_only(cls, value: Any) -> Any:
        return _reject_non_numbers(value)

    @model_validator(mode="after")
    def _check_range(self) -> PsuLimits:
        if self.min_voltage > self.max_voltage:
            raise ValueError("min_voltage must be <= max_voltage")
        return self


class MotionLimits(_StrictLimits):
    """Not attached to any device class until a motion class exists."""

    position_min: float = Field(allow_inf_nan=False)
    position_max: float = Field(allow_inf_nan=False)
    max_speed: float = Field(gt=0, **_NUMBER)

    @field_validator("*", mode="before")
    @classmethod
    def _numbers_only(cls, value: Any) -> Any:
        return _reject_non_numbers(value)

    @model_validator(mode="after")
    def _check_range(self) -> MotionLimits:
        if not self.position_min < self.position_max:
            raise ValueError("position_min must be < position_max")
        return self


class EmptyLimits(_StrictLimits):
    """For classes with nothing to limit yet: accepts only ``{}``."""


_MODELS: dict[str, type[BaseModel]] = {"psu": PsuLimits}


def _class_name(device_class: Any) -> str:
    return str(getattr(device_class, "value", device_class))


def limits_model_for(device_class: Any) -> type[BaseModel]:
    """Return the limits model for a device class (``DeviceClass`` or its string value)."""
    return _MODELS.get(_class_name(device_class), EmptyLimits)


def _message(err: Any, model: type[BaseModel]) -> str:
    kind, ctx = err["type"], err.get("ctx") or {}
    if kind == "missing":
        return "is required"
    if kind == "extra_forbidden":
        if model is EmptyLimits:
            return "unknown key (this device class accepts no limits yet)"
        return "unknown key"
    if kind == "greater_than":
        return f"must be > {ctx['gt']}"
    if kind == "greater_than_equal":
        return f"must be >= {ctx['ge']}"
    if kind == "finite_number":
        return "must be finite (not NaN or infinity)"
    if kind in ("float_parsing", "float_type"):
        return "must be a number"
    msg = err["msg"]
    return msg[len("Value error, ") :] if msg.startswith("Value error, ") else msg


def validate_limits(device_class: Any, raw: Any) -> dict[str, Any]:
    """Validate ``raw`` for ``device_class`` and return it as a dict (defaults filled in).

    Raises :class:`LimitsError` listing every problem.
    """
    model = limits_model_for(device_class)
    if not isinstance(raw, dict):
        raise LimitsError([("", "must be a mapping")])
    try:
        return model.model_validate(raw).model_dump()
    except ValidationError as exc:
        issues = [
            (".".join(str(p) for p in err["loc"]), _message(err, model))
            for err in exc.errors(include_url=False)
        ]
        raise LimitsError(issues) from None
