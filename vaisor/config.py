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
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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


class BenchConfig(_Strict):
    version: Literal[1] = 1
    name: str = "bench"
    devices: dict[str, DeviceConfig]

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
        msg = err["msg"]
        if err["type"] == "union_tag_not_found":
            expected = ", ".join(sorted(_CONNECTION_TYPES))
            msg = f"missing 'type' (expected one of: {expected})"
        elif msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        lines.append(f"{path}: {_format_loc(tuple(err['loc']))}: {msg}")
    return "\n".join(lines)


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
        data = yaml.safe_load(text)
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
