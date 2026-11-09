"""Append-only JSON-lines audit log.

Every command (including refusals) is recorded as one JSON object per line.
Callers that run actuating commands must treat :class:`AuditError` as a reason
not to run the command (fail closed).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUTCOMES = frozenset({"attempt", "ok", "error", "refused"})
REDACTED = "***"
_SECRET_MARKERS = ("token", "password", "secret", "key")
_ALLOWED = {
    "caller", "command", "outcome", "device", "args", "result", "error", "refusal_code",
}


class AuditError(Exception):
    """The audit log could not be written or read."""


def _check_json_safe(value: Any, where: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{where}: non-finite float is not JSON-safe")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_json_safe(item, f"{where}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"{where}: dict key {k!r} is not a string")
            _check_json_safe(v, f"{where}.{k}")
        return
    raise ValueError(f"{where}: {type(value).__name__} is not JSON-safe")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: REDACTED if any(m in k.lower() for m in _SECRET_MARKERS) else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _validate(event: dict) -> dict:
    if not isinstance(event, dict):
        raise ValueError("event must be a dict")
    unknown = set(event) - _ALLOWED - {"ts"}
    if unknown:
        raise ValueError(f"unknown audit fields: {sorted(unknown)}")
    caller = event.get("caller")
    if not isinstance(caller, str) or not caller.strip():
        raise ValueError("caller must be a non-empty string")
    if not isinstance(event.get("command"), str):
        raise ValueError("command must be a string")
    if event.get("outcome") not in OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(OUTCOMES)}")
    device = event.get("device")
    if device is not None and not isinstance(device, str):
        raise ValueError("device must be a string or null")
    if "args" in event and not isinstance(event["args"], dict):
        raise ValueError("args must be a dict")
    for name in ("error", "refusal_code"):
        if name in event and not isinstance(event[name], str):
            raise ValueError(f"{name} must be a string")
    for name in ("args", "result"):
        if name in event:
            _check_json_safe(event[name], name)
    return {k: v for k, v in event.items() if k != "ts"}


class AuditLog:
    """Thread-safe JSON-lines audit log; each record is fsynced."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AuditError(f"cannot create audit log directory: {exc}") from exc

    def record(self, event: dict) -> dict:
        body = _validate(event)
        if "args" in body:
            body["args"] = _redact(body["args"])
        rec = {"ts": datetime.now(timezone.utc).isoformat()} | body
        line = json.dumps(rec, allow_nan=False) + "\n"
        try:
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
        except OSError as exc:
            raise AuditError(f"cannot write audit log {self.path}: {exc}") from exc
        return rec


def read_entries(path: str | os.PathLike[str]) -> list[dict]:
    """Parse every line of an audit log file."""
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError as exc:
        raise AuditError(f"cannot read audit log {path}: {exc}") from exc
