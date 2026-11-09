from __future__ import annotations

import pytest

from vaisor.config import BenchConfigError, DeviceClass, load_bench, typed_limits
from vaisor.limits import (
    EmptyLimits,
    MotionLimits,
    PsuLimits,
    limits_model_for,
    validate_limits,
)

CONN = "{type: serial, port: /dev/ttyUSB0}"


def _load(tmp_path, cls, limits):
    f = tmp_path / "bench.yaml"
    f.write_text(
        f"devices:\n  dev1:\n    class: {cls}\n    driver: x\n    connection: {CONN}\n"
        f"    limits: {limits}\n"
    )
    return load_bench(f)


def test_valid_psu(tmp_path):
    dev = _load(tmp_path, "psu", "{max_voltage: 12, max_current: 1.5}").devices["dev1"]
    t = typed_limits(dev)
    assert isinstance(t, PsuLimits)
    assert (t.max_voltage, t.max_current, t.min_voltage) == (12, 1.5, 0)
    assert dev.limits["max_voltage"] == 12


@pytest.mark.parametrize(
    "limits, expected",
    [
        ("{max_current: 1}", "devices.dev1.limits.max_voltage: is required"),
        ("{max_voltage: -1, max_current: 1}", "devices.dev1.limits.max_voltage: must be > 0"),
        ("{max_voltage: 0, max_current: 1}", "devices.dev1.limits.max_voltage: must be > 0"),
        (
            "{max_voltage: 5, max_current: 1, min_voltage: -1}",
            "devices.dev1.limits.min_voltage: must be >= 0",
        ),
        ("{max_voltage: .nan, max_current: 1}", "devices.dev1.limits.max_voltage: must be finite"),
        ("{max_voltage: .inf, max_current: 1}", "devices.dev1.limits.max_voltage: must be finite"),
        ("{max_voltage: -.inf, max_current: 1}", "devices.dev1.limits.max_voltage"),
        ("{max_voltage: true, max_current: 1}", "devices.dev1.limits.max_voltage: must be a number"),
        ("{max_voltage: '5', max_current: 1}", "devices.dev1.limits.max_voltage: must be a number"),
        ("{max_voltage: 5, max_current: 1, bogus: 1}", "devices.dev1.limits.bogus: unknown key"),
        (
            "{max_voltage: 5, max_current: 1, min_voltage: 6}",
            "devices.dev1.limits: min_voltage must be <= max_voltage",
        ),
        ("{}", "devices.dev1.limits.max_current: is required"),
    ],
)
def test_psu_rejects(tmp_path, limits, expected):
    with pytest.raises(BenchConfigError) as e:
        _load(tmp_path, "psu", limits)
    assert expected in str(e.value)


def test_multimeter_non_empty_rejected(tmp_path):
    with pytest.raises(BenchConfigError, match=r"devices\.dev1\.limits\.max_voltage: unknown key"):
        _load(tmp_path, "multimeter", "{max_voltage: 5}")


def test_multimeter_empty_ok(tmp_path):
    dev = _load(tmp_path, "multimeter", "{}").devices["dev1"]
    assert isinstance(typed_limits(dev), EmptyLimits)


def test_limits_not_a_mapping(tmp_path):
    with pytest.raises(BenchConfigError, match=r"devices\.dev1\.limits"):
        _load(tmp_path, "psu", "[1]")


def test_model_lookup():
    assert limits_model_for(DeviceClass.PSU) is PsuLimits
    assert limits_model_for("psu") is PsuLimits
    for c in DeviceClass:
        if c is not DeviceClass.PSU:
            assert limits_model_for(c) is EmptyLimits


def test_motion_limits():
    ok = MotionLimits(position_min=-1, position_max=1, max_speed=2)
    assert ok.max_speed == 2
    for bad in (
        dict(position_min=1, position_max=1, max_speed=1),
        dict(position_min=float("nan"), position_max=1, max_speed=1),
        dict(position_min=0, position_max=float("inf"), max_speed=1),
        dict(position_min=0, position_max=1, max_speed=0),
        dict(position_min=True, position_max=2, max_speed=1),
    ):
        with pytest.raises(ValueError):
            MotionLimits(**bad)


def test_validate_limits_returns_dict():
    out = validate_limits("psu", {"max_voltage": 5, "max_current": 1})
    assert out == {"max_voltage": 5, "max_current": 1, "min_voltage": 0}
