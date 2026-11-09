"""Loading and validating ``bench.yaml``.

A bench file describes one bench: named device instances, the driver each one
uses, how to connect to it and (later) the limits the safety layer enforces.
All models forbid unknown keys so typos are reported instead of ignored.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from vaisor.limits import LimitsError, limits_model_for, validate_limits

DEVICE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class BenchConfigError(Exception):
    """Raised when a bench file cannot be loaded; the message is meant for humans."""


class DeviceClass(str, Enum):
    MULTIMETER = "multimeter"
    CAMERA = "camera"
    PSU = "psu"
    SCOPE = "scope"
    LOGIC = "logic"
    FLASHER = "flasher"
    RELAY = "relay"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SerialConnection(_Strict):
    type: Literal["serial"]
    port: str
    baudrate: int = 9600
    bytesize: Literal[5, 6, 7, 8] = 8
    parity: Literal["N", "E", "O"] = "N"
    stopbits: Literal[1, 2] = 1
    timeout: float = Field(default=1.0, gt=0)
    rts: bool | None = None
    """RTS line state; ``None`` means the driver decides."""


# Discriminated on ``type`` so further transports (usb, tcp, ...) can be added.
Connection = Annotated[Union[SerialConnection], Field(discriminator="type")]
_CONNECTION_TYPES = frozenset({"serial"})


class DeviceConfig(_Strict):
    device_class: DeviceClass = Field(alias="class")
    driver: str
    connection: Connection
    limits: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None

    @field_validator("limits")
    @classmethod
    def _check_limits(cls, value: dict[str, Any], info: ValidationInfo) -> dict[str, Any]:
        device_class = info.data.get("device_class")
        if device_class is None:  # class itself invalid; already reported
            return value
        try:
            return validate_limits(device_class, value)
        except LimitsError as exc:
            raise PydanticCustomError(
                "limits_invalid", "invalid limits", {"issues": exc.issues}
            ) from None


def typed_limits(dev: DeviceConfig) -> BaseModel:
    """Return the validated limits of ``dev`` as its class's limits model instance."""
    return limits_model_for(dev.device_class).model_validate(dev.limits)


class BenchConfig(_Strict):
    version: Literal[1] = 1
    name: str = "bench"
    # Runtime state (lock, ...), relative to the bench file unless absolute.
    state_dir: str = Field(default=".vaisor", min_length=1)
    devices: dict[str, DeviceConfig]
    # Optional relay device the e-stop opens to cut power after outputs are off.
    estop_relay: str | None = None

    @model_validator(mode="after")
    def _check_estop_relay(self) -> BenchConfig:
        name = self.estop_relay
        if name is None:
            return self
        dev = self.devices.get(name)
        if dev is None:
            valid = ", ".join(sorted(self.devices)) or "none"
            raise ValueError(f"estop_relay '{name}' is not a configured device (devices: {valid})")
        if dev.device_class is not DeviceClass.RELAY:
            raise ValueError(
                f"estop_relay '{name}' must be a relay device, it is a {dev.device_class.value}"
            )
        return self

    @field_validator("devices", mode="before")
    @classmethod
    def _check_device_names(cls, value: Any) -> Any:
        if isinstance(value, dict):
            bad = [str(k) for k in value if not DEVICE_NAME_RE.match(str(k))]
            if bad:
                names = ", ".join(repr(b) for b in bad)
                raise ValueError(
                    f"invalid device name {names}: names must match "
                    f"{DEVICE_NAME_RE.pattern} (lowercase letter first, then "
                    "lowercase letters, digits, '_' or '-')"
                )
        return value


def _format_loc(loc: tuple[int | str, ...]) -> str:
    parts = [str(item) for item in loc]
    # Pydantic inserts the discriminator tag after the union field
    # (devices.<name>.connection.serial.port); users never wrote it, so drop it.
    if len(parts) > 3 and parts[0] == "devices" and parts[2] == "connection":
        if parts[3] in _CONNECTION_TYPES:
            del parts[3]
    return ".".join(parts) if parts else "<top level>"


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = []
    for err in exc.errors(include_url=False):
        if err["type"] == "limits_invalid":
            for sub, issue in err["ctx"]["issues"]:
                loc = _format_loc(tuple(err["loc"]) + ((sub,) if sub else ()))
                lines.append(f"{path}: {loc}: {issue}")
            continue
        msg = err["msg"]
        if err["type"] == "union_tag_not_found":
            expected = ", ".join(sorted(_CONNECTION_TYPES))
            msg = f"missing 'type' (expected one of: {expected})"
        elif msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        lines.append(f"{path}: {_format_loc(tuple(err['loc']))}: {msg}")
    return "\n".join(lines)


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of keeping the last one."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        # Check only the node's own explicit keys, before merge keys are flattened,
        # so that overriding a key pulled in via ``<<: *anchor`` stays valid YAML.
        seen: set[Any] = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            try:
                key = self.construct_object(key_node, deep=True)
                hash(key)
            except TypeError:
                continue  # unhashable keys are reported by the base class
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def load_bench(path: str | Path) -> BenchConfig:
    """Load and validate a bench file, raising :class:`BenchConfigError` on any problem."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise BenchConfigError(f"{path}: bench file not found") from None
    except OSError as exc:
        raise BenchConfigError(f"{path}: cannot read bench file: {exc.strerror}") from None

    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)  # SafeLoader subclass
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        problem = getattr(exc, "problem", None) or str(exc)
        if mark is not None:
            where = f"line {mark.line + 1}, column {mark.column + 1}"
            raise BenchConfigError(f"{path}: invalid YAML at {where}: {problem}") from None
        raise BenchConfigError(f"{path}: invalid YAML: {problem}") from None

    if not isinstance(data, dict):
        found = "an empty file" if data is None else type(data).__name__
        raise BenchConfigError(
            f"{path}: top level must be a mapping (e.g. 'devices: ...'), found {found}"
        )

    try:
        return BenchConfig.model_validate(data)
    except ValidationError as exc:
        raise BenchConfigError(_format_validation_error(path, exc)) from None
