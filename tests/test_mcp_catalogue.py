"""MCP catalogue tests: no hardware, ports or network."""

from __future__ import annotations

from typing import ClassVar

from fake_actuators import FakePSU, FakeRelay

from vaisor.bench import Bench
from vaisor.config import BenchConfig, DeviceClass, SerialConnection
from vaisor.drivers.base import Driver
from vaisor.mcp import build_catalogue
from vaisor.transport import FakeTransport, Transport

CONN = {"type": "serial", "port": "loop://"}


class Odd(Driver):
    device_class: ClassVar[DeviceClass] = DeviceClass.PSU
    capabilities: ClassVar[frozenset[str]] = frozenset({"frobnicate"})


def make(tmp_path) -> Bench:
    cfg = BenchConfig.model_validate(
        {
            "state_dir": str(tmp_path),
            "devices": {
                "psu_b": {"class": "psu", "driver": "psu", "connection": CONN,
                          "description": "Second supply"},
                "psu_a": {"class": "psu", "driver": "psu", "connection": CONN,
                          "description": "Main supply"},
                "rly": {"class": "relay", "driver": "relay", "connection": CONN},
                "odd": {"class": "psu", "driver": "odd", "connection": CONN},
            },
        }
    )

    def factory(conn: SerialConnection) -> Transport:
        return FakeTransport()

    drivers = {"psu": FakePSU, "relay": FakeRelay, "odd": Odd}
    return Bench.from_config(cfg, drivers, factory)  # type: ignore[arg-type]


def test_catalogue(tmp_path):
    with make(tmp_path) as bench:
        tools = build_catalogue(bench)
        assert build_catalogue(bench) == tools
    names = [t.name for t in tools]
    assert names == sorted(names)
    by = {t.name: t for t in tools}

    sv = by["set_voltage"].input_schema
    assert sv["properties"]["device"]["enum"] == ["psu_a", "psu_b"]
    assert sv["properties"]["voltage"] == {"type": "number"}
    assert "device" in sv["required"] and "voltage" in sv["required"]
    assert by["set_current"].input_schema["properties"]["current"] == {"type": "number"}
    assert by["relay_set"].input_schema["properties"]["closed"] == {"type": "boolean"}
    assert by["relay_set"].input_schema["properties"]["device"]["enum"] == ["rly"]
    for cap in ("read_measurement", "output_on", "output_off"):
        assert set(by[cap].input_schema["properties"]) == {"device"}
    assert by["output_off"].input_schema["properties"]["device"]["enum"] == [
        "psu_a", "psu_b", "rly"
    ]

    unknown = by["frobnicate"].input_schema
    assert unknown["type"] == "object"
    assert set(unknown["properties"]) == {"device"}

    d = by["set_voltage"].description
    assert "bench.yaml" in d and "Main supply" in d and "psu" in d
    for cap in ("set_voltage", "set_current", "output_on", "relay_set"):
        assert "bench.yaml" in by[cap].description
    assert "bench.yaml" not in by["read_measurement"].description
