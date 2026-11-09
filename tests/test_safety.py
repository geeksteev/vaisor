from __future__ import annotations

import math
from importlib.metadata import entry_points

import pytest

from vaisor.safety import (
    ACTUATING_CAPABILITIES,
    SAFE_CAPABILITIES,
    SafetyRefused,
    check_command,
)

PSU = dict(max_voltage=30.0, max_current=3.0, min_voltage=1.0)
MOTION = dict(position_min=-10.0, position_max=10.0, max_speed=5.0)
RELAY = dict(configured=1)


def run(cap, args, limits, cls="psu"):
    check_command("dev", cls, limits, cap, args)


def code(cap, args, limits, cls="psu"):
    with pytest.raises(SafetyRefused) as exc:
        run(cap, args, limits, cls)
    return exc.value.code


@pytest.mark.parametrize(
    "cap,args,limits",
    [
        ("set_voltage", dict(voltage=1), PSU),
        ("set_voltage", dict(voltage=30), PSU),
        ("set_voltage", dict(voltage=12.5), PSU),
        ("set_current", dict(current=0), PSU),
        ("set_current", dict(current=3), PSU),
        ("output_on", {}, PSU),
        ("relay_set", dict(closed=True), RELAY),
        ("move", dict(position=10, speed=5), MOTION),
        ("move", dict(position=-10, speed=0), MOTION),
        ("output_off", {}, {}),
        ("output_off", {}, None),
        ("read_measurement", {}, {}),
        ("whatever_read", None, None),
    ],
)
def test_accepted(cap, args, limits):
    run(cap, args, limits)


@pytest.mark.parametrize(
    "cap,args,limits,expected",
    [
        ("set_voltage", dict(voltage=30.001), PSU, "out_of_range"),
        ("set_voltage", dict(voltage=0.999), PSU, "out_of_range"),
        ("set_current", dict(current=3.001), PSU, "out_of_range"),
        ("set_current", dict(current=-0.001), PSU, "out_of_range"),
        ("move", dict(position=10.01, speed=1), MOTION, "out_of_range"),
        ("move", dict(position=-10.01, speed=1), MOTION, "out_of_range"),
        ("move", dict(position=0, speed=5.01), MOTION, "out_of_range"),
        ("set_voltage", {}, PSU, "invalid_argument"),
        ("set_voltage", dict(voltage=1, x=2), PSU, "invalid_argument"),
        ("set_voltage", dict(volts=1), PSU, "invalid_argument"),
        ("output_on", dict(x=1), PSU, "invalid_argument"),
        ("relay_set", {}, RELAY, "invalid_argument"),
        ("relay_set", dict(closed=1), RELAY, "invalid_argument"),
        ("relay_set", dict(closed="yes"), RELAY, "invalid_argument"),
        ("move", dict(position=0), MOTION, "invalid_argument"),
        ("set_voltage", dict(voltage=math.nan), PSU, "invalid_argument"),
        ("set_voltage", dict(voltage=math.inf), PSU, "invalid_argument"),
        ("set_current", dict(current=-math.inf), PSU, "invalid_argument"),
        ("set_voltage", dict(voltage=True), PSU, "invalid_argument"),
        ("set_current", dict(current=False), PSU, "invalid_argument"),
        ("set_voltage", dict(voltage="5"), PSU, "invalid_argument"),
        ("set_voltage", dict(voltage=None), PSU, "invalid_argument"),
        ("move", dict(position=math.nan, speed=1), MOTION, "invalid_argument"),
        ("move", dict(position=0, speed=True), MOTION, "invalid_argument"),
        ("set_voltage", [1], PSU, "invalid_argument"),
        ("", {}, PSU, "unknown_capability"),
        (None, {}, PSU, "unknown_capability"),
    ],
)
def test_refused(cap, args, limits, expected):
    assert code(cap, args, limits) == expected


@pytest.mark.parametrize("cap", sorted(ACTUATING_CAPABILITIES))
@pytest.mark.parametrize("limits", [None, {}])
def test_actuating_without_limits_refused(cap, limits):
    args = {
        "set_voltage": dict(voltage=1),
        "set_current": dict(current=1),
        "relay_set": dict(closed=True),
        "move": dict(position=0, speed=1),
    }.get(cap, {})
    assert code(cap, args, limits, "relay") == "no_limits"


def test_flash_refused_even_with_limits():
    assert code("flash", {}, PSU) == "no_limits"


def test_wrong_limits_shape_is_no_limits():
    assert code("set_voltage", dict(voltage=1), MOTION) == "no_limits"
    assert code("move", dict(position=0, speed=1), PSU) == "no_limits"


def test_error_has_code_and_message():
    with pytest.raises(SafetyRefused) as exc:
        run("set_voltage", dict(voltage=99), PSU)
    assert exc.value.code == "out_of_range" and exc.value.message


def test_builtin_driver_capabilities_are_classified():
    eps = entry_points(group="vaisor.drivers")
    assert list(eps), "no built-in drivers found"
    for ep in eps:
        for cap in ep.load().capabilities:
            if cap in ACTUATING_CAPABILITIES or cap in SAFE_CAPABILITIES:
                continue
            assert cap.startswith(("read_", "capture_")), (
                f"{ep.name}: capability {cap!r} is not clearly a read capability; "
                "classify it in vaisor/safety.py"
            )
