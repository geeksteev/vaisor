"""Built-in MCP tools (list_devices, bench_status, estop): definitions and handlers.

Pure functions over a ``Bench``; the MCP server wires them in next to the
capability tools. ``list_devices`` and ``bench_status`` touch no hardware and
never take the bench lock. ``estop`` calls ``Bench.estop`` directly, which does
not lock either (see the separate issue on making e-stop safe against
concurrent transport use).
"""

from __future__ import annotations

from typing import Any

from vaisor.bench import Bench
from vaisor.mcp.catalogue import Tool

ESTOP_CALLER = "mcp:stdio"
REASON_MAX_LENGTH = 200

_NO_ARGS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

BUILTIN_TOOLS: list[Tool] = [
    Tool(
        "list_devices",
        "List the configured bench devices (name, class, driver, port, capabilities). "
        "Does not touch hardware.",
        _NO_ARGS,
    ),
    Tool(
        "bench_status",
        "Bench name, devices, e-stop latch state and the current exclusive lock "
        "holder. Read-only: does not touch hardware or take the lock.",
        _NO_ARGS,
    ),
    Tool(
        "estop",
        "EMERGENCY STOP: latch the e-stop, turn off every output and open the "
        "e-stop relay. Works even if already latched. Actuation is refused until "
        "the latch is cleared.",
        {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "maxLength": REASON_MAX_LENGTH},
            },
            "additionalProperties": False,
        },
    ),
]

BUILTIN_NAMES = frozenset(t.name for t in BUILTIN_TOOLS)


def list_devices(bench: Bench) -> list[dict[str, Any]]:
    return [i.to_dict() for i in bench.devices()]


def bench_status(bench: Bench) -> dict[str, Any]:
    return {
        "bench": bench.config.name,
        "devices": [
            {
                "name": d["name"],
                "class": d["class"],
                "driver": d["driver"],
                "capabilities": d["capabilities"],
            }
            for d in list_devices(bench)
        ],
        "estop": bench.estop_state(),
        "lock_holder": bench.lock.inspect(),
    }


def estop(bench: Bench, reason: str = "") -> dict[str, Any]:
    if not isinstance(reason, str):
        raise ValueError("reason must be a string")
    if len(reason) > REASON_MAX_LENGTH:
        raise ValueError(f"reason is longer than {REASON_MAX_LENGTH} characters")
    return bench.estop(ESTOP_CALLER, reason)


def call_builtin(bench: Bench, name: str, arguments: dict[str, Any] | None) -> Any:
    """Run a built-in tool by name; ``KeyError`` if ``name`` is not built in."""
    if name not in BUILTIN_NAMES:
        raise KeyError(name)
    args = dict(arguments or {})
    if name == "estop":
        extra = set(args) - {"reason"}
        if extra:
            raise ValueError(f"estop does not accept: {', '.join(sorted(extra))}")
        return estop(bench, **args)
    if args:
        raise ValueError(f"{name} takes no arguments")
    return list_devices(bench) if name == "list_devices" else bench_status(bench)
