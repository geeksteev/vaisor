"""Pure functions that turn a bench into MCP tool definitions (no MCP SDK needed)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vaisor.bench import Bench
from vaisor.safety import ACTUATING_CAPABILITIES


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]


# Argument schemas of known capabilities: name -> {argument: JSON Schema type}.
CAPABILITY_ARGS: dict[str, dict[str, str]] = {
    "read_measurement": {},
    "set_voltage": {"voltage": "number"},
    "set_current": {"current": "number"},
    "output_on": {},
    "output_off": {},
    "relay_set": {"closed": "boolean"},
    "capture_frame": {},
}


def build_catalogue(bench: Bench) -> list[Tool]:
    """One tool per distinct capability, named verbatim, sorted by tool name."""
    by_cap: dict[str, list[str]] = {}
    lines: dict[str, list[str]] = {}
    for info in bench.devices():
        desc = bench.config.devices[info.name].description
        line = f"{info.name} ({info.device_class.value}, driver {info.driver})"
        if desc:
            line += f": {desc}"
        for cap in info.capabilities:
            by_cap.setdefault(cap, []).append(info.name)
            lines.setdefault(cap, []).append(line)

    tools = []
    for cap in sorted(by_cap):
        args = CAPABILITY_ARGS.get(cap, {})
        properties: dict[str, Any] = {
            "device": {"type": "string", "enum": sorted(by_cap[cap])}
        }
        properties.update({a: {"type": t} for a, t in args.items()})
        description = f"Run '{cap}' on a bench device. Devices: " + "; ".join(
            sorted(lines[cap])
        )
        if cap in ACTUATING_CAPABILITIES:
            description += (
                ". This actuates hardware: the limits in bench.yaml apply "
                "and out-of-range requests are refused."
            )
        tools.append(
            Tool(
                name=cap,
                description=description,
                input_schema={
                    "type": "object",
                    "properties": properties,
                    "required": ["device", *args],
                    "additionalProperties": False,
                },
            )
        )
    return tools
