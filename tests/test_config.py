from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from vaisor.config import (
    BenchConfig,
    BenchConfigError,
    DeviceClass,
    SerialConnection,
    load_bench,
)
from vaisor.device import DeviceInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "bench.example.yaml"

DEVICE_HEAD = """\
devices:
  {name}:
    class: {cls}
    driver: extech_383273
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "bench.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def device_yaml(
    name: str = "dmm1",
    cls: str = "multimeter",
    connection: str = "    connection: {type: serial, port: /dev/ttyUSB0}\n",
    extra: str = "",
) -> str:
    return DEVICE_HEAD.format(name=name, cls=cls) + connection + extra


def load_error(path: Path) -> str:
    with pytest.raises(BenchConfigError) as info:
        load_bench(path)
    return str(info.value)


def test_load_example() -> None:
    bench = load_bench(EXAMPLE)
    assert isinstance(bench, BenchConfig)
    assert bench.version == 1
    assert list(bench.devices) == ["dmm1"]
    dmm = bench.devices["dmm1"]
    assert dmm.device_class is DeviceClass.MULTIMETER
    assert dmm.driver == "extech_383273"
    conn = dmm.connection
    assert isinstance(conn, SerialConnection)
    assert (conn.port, conn.baudrate, conn.bytesize, conn.parity, conn.stopbits) == (
        "/dev/ttyUSB0",
        9600,
        8,
        "N",
        1,
    )
    assert conn.rts is None
    assert dmm.limits == {}


def test_defaults_applied(tmp_path: Path) -> None:
    bench = load_bench(write(tmp_path, device_yaml()))
    assert bench.name == "bench"
    conn = bench.devices["dmm1"].connection
    assert conn.baudrate == 9600
    assert conn.timeout == 1.0
    assert conn.rts is None
    assert bench.devices["dmm1"].description is None


def test_accepts_str_path(tmp_path: Path) -> None:
    assert "dmm1" in load_bench(str(write(tmp_path, device_yaml()))).devices


def test_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "nope.yaml"
    msg = load_error(path)
    assert str(path) in msg
    assert "not found" in msg


def test_bad_yaml_reports_line(tmp_path: Path) -> None:
    path = write(tmp_path, "devices:\n  dmm1:\n    class: [multimeter\n    driver: x\n")
    msg = load_error(path)
    assert str(path) in msg
    assert "invalid YAML" in msg
    assert "line " in msg


def test_duplicate_device_name_rejected(tmp_path: Path) -> None:
    text = device_yaml() + device_yaml().split("devices:\n", 1)[1]
    path = write(tmp_path, text)
    msg = load_error(path)
    assert str(path) in msg
    assert "duplicate key 'dmm1'" in msg
    assert "line " in msg


def test_duplicate_limits_block_rejected(tmp_path: Path) -> None:
    text = device_yaml(extra="    limits: {max_v: 5}\n    limits: {max_v: 50}\n")
    msg = load_error(write(tmp_path, text))
    assert "duplicate key 'limits'" in msg
    last = len(text.splitlines())
    assert f"line {last}," in msg


@pytest.mark.parametrize("text", ["- a\n- b\n", "just a string\n", ""])
def test_top_level_not_mapping(tmp_path: Path, text: str) -> None:
    msg = load_error(write(tmp_path, text))
    assert "top level must be a mapping" in msg


def test_unknown_key(tmp_path: Path) -> None:
    path = write(tmp_path, device_yaml(extra="    colour: red\n"))
    msg = load_error(path)
    assert msg == f"{path}: devices.dmm1.colour: Extra inputs are not permitted"


def test_unknown_connection_key(tmp_path: Path) -> None:
    conn = "    connection: {type: serial, port: /dev/ttyUSB0, speed: 9600}\n"
    msg = load_error(write(tmp_path, device_yaml(connection=conn)))
    assert "devices.dmm1.connection.speed: Extra inputs are not permitted" in msg


def test_unknown_top_level_key(tmp_path: Path) -> None:
    msg = load_error(write(tmp_path, "bogus: 1\n" + device_yaml()))
    assert "bogus: Extra inputs are not permitted" in msg


def test_missing_port(tmp_path: Path) -> None:
    path = write(tmp_path, device_yaml(connection="    connection:\n      type: serial\n"))
    msg = load_error(path)
    assert msg == f"{path}: devices.dmm1.connection.port: Field required"


def test_missing_connection_type(tmp_path: Path) -> None:
    conn = "    connection: {port: /dev/ttyUSB0}\n"
    msg = load_error(write(tmp_path, device_yaml(connection=conn)))
    assert "devices.dmm1.connection: missing 'type'" in msg
    assert "serial" in msg


def test_unknown_connection_type(tmp_path: Path) -> None:
    conn = "    connection: {type: carrier-pigeon, port: x}\n"
    msg = load_error(write(tmp_path, device_yaml(connection=conn)))
    assert "devices.dmm1.connection:" in msg
    assert "carrier-pigeon" in msg


def test_invalid_class(tmp_path: Path) -> None:
    path = write(tmp_path, device_yaml(cls="toaster"))
    msg = load_error(path)
    assert msg.startswith(f"{path}: devices.dmm1.class: ")
    assert "multimeter" in msg


@pytest.mark.parametrize("name", ["DMM1", "1dmm", "my dmm", "_dmm"])
def test_invalid_device_name(tmp_path: Path, name: str) -> None:
    path = write(tmp_path, device_yaml(name=f"'{name}'"))
    msg = load_error(path)
    assert msg.startswith(f"{path}: devices: invalid device name")
    assert repr(name) in msg


@pytest.mark.parametrize("name", ["dmm1", "psu-main", "cam_top", "a"])
def test_valid_device_names(tmp_path: Path, name: str) -> None:
    assert name in load_bench(write(tmp_path, device_yaml(name=name))).devices


@pytest.mark.parametrize(
    "field,value",
    [("timeout", "0"), ("timeout", "-1"), ("bytesize", "9"), ("parity", "X"), ("stopbits", "3")],
)
def test_invalid_serial_values(tmp_path: Path, field: str, value: str) -> None:
    conn = f"    connection: {{type: serial, port: /dev/ttyUSB0, {field}: {value}}}\n"
    msg = load_error(write(tmp_path, device_yaml(connection=conn)))
    assert f"devices.dmm1.connection.{field}: " in msg


def test_multiple_errors_one_line_each(tmp_path: Path) -> None:
    text = device_yaml(cls="toaster", connection="    connection:\n      type: serial\n")
    path = write(tmp_path, text)
    lines = load_error(path).splitlines()
    assert len(lines) == 2
    assert all(line.startswith(f"{path}: devices.dmm1.") for line in lines)


def test_no_pydantic_traceback_chained(tmp_path: Path) -> None:
    with pytest.raises(BenchConfigError) as info:
        load_bench(write(tmp_path, device_yaml(cls="toaster")))
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_unsupported_version(tmp_path: Path) -> None:
    msg = load_error(write(tmp_path, "version: 2\n" + device_yaml()))
    assert "version: " in msg


def test_device_info_is_frozen() -> None:
    dmm = load_bench(EXAMPLE).devices["dmm1"]
    info = DeviceInfo(
        name="dmm1",
        device_class=dmm.device_class,
        driver=dmm.driver,
        connection=dmm.connection,
        capabilities=("read_voltage_dc",),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.name = "other"  # type: ignore[misc]


def test_merge_key_override_is_not_duplicate(tmp_path: Path) -> None:
    text = """\
devices:
  dmm1: &base
    class: multimeter
    driver: extech_383273
    connection: {type: serial, port: /dev/ttyUSB0}
  dmm2:
    <<: *base
    connection: {type: serial, port: /dev/ttyUSB1}
"""
    cfg = load_bench(write(tmp_path, text))
    assert set(cfg.devices) == {"dmm1", "dmm2"}
    assert cfg.devices["dmm2"].connection.port == "/dev/ttyUSB1"  # type: ignore[union-attr]


def test_duplicate_explicit_key_next_to_merge_still_rejected(tmp_path: Path) -> None:
    text = """\
devices:
  dmm1: &base
    class: multimeter
    driver: extech_383273
    connection: {type: serial, port: /dev/ttyUSB0}
  dmm2:
    <<: *base
    connection: {type: serial, port: /dev/ttyUSB1}
    connection: {type: serial, port: /dev/ttyUSB2}
"""
    assert "duplicate key 'connection'" in load_error(write(tmp_path, text))
