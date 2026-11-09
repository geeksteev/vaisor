import pytest

from vaisor.drivers.multimeter.extech_383273 import ProtocolError, decode_frame


def _put(w: int, first_bit: int, width: int, val: int) -> int:
    for i in range(width):
        bit = (val >> (width - 1 - i)) & 1  # MSB goes to the lowest bit
        w |= bit << (first_bit + i)
    return w


def encode(digits: str, dp: int, negative: bool, code: int = 0x00) -> bytes:
    """Build a frame from 4 display digits (first is 0/1), dp code 0-3."""
    assert len(digits) == 4
    w = int(negative)
    w |= int(digits[0]) << 1
    for start, ch in zip((2, 6, 10), digits[1:]):
        w = _put(w, start, 4, int(ch))
    w = _put(w, 14, 2, dp)
    return bytes([0x02, code, w & 0xFF, w >> 8, 0x03])


def _expected(digits: str, dp: int, neg: bool) -> float:
    text = digits if dp == 0 else digits[:-dp] + "." + digits[-dp:]
    return -float(text) if neg else float(text)


def test_manual_example():
    r = decode_frame(bytes.fromhex("020C21B103"))
    assert r.value == -12.3
    assert not r.overrange
    assert r.raw == bytes.fromhex("020C21B103")


def test_encode_matches_manual():
    assert encode("0123", 1, True, 0x0C) == bytes.fromhex("020C21B103")


@pytest.mark.parametrize(
    "dp,expected", [(0, 123.0), (1, 12.3), (2, 1.23), (3, 0.123)]
)
def test_decimal_points_positive(dp, expected):
    assert decode_frame(encode("0123", dp, False)).value == pytest.approx(expected)


def test_leading_digit_one():
    assert decode_frame(encode("1999", 3, False)).value == pytest.approx(1.999)
    assert decode_frame(encode("1999", 3, True)).value == pytest.approx(-1.999)


def test_round_trip():
    for digits in ("0000", "1234", "0987", "1500"):
        for dp in range(4):
            for neg in (False, True):
                v = decode_frame(encode(digits, dp, neg)).value
                assert v == pytest.approx(_expected(digits, dp, neg))


@pytest.mark.parametrize("field_start", [2, 6, 10])
def test_overrange(field_start):
    w = 0xF << field_start
    r = decode_frame(bytes([2, 0, w & 0xFF, w >> 8, 3]))
    assert r.value is None
    assert r.overrange is True


@pytest.mark.parametrize(
    "frame",
    [
        bytes.fromhex("020C21B1"),
        bytes.fromhex("020C21B10300"),
        bytes.fromhex("010C21B103"),
        bytes.fromhex("020C21B104"),
        b"",
    ],
)
def test_bad_frames(frame):
    with pytest.raises(ProtocolError):
        decode_frame(frame)


@pytest.mark.parametrize(
    "code,function,unit",
    [
        (0x00, "voltage_dc", "mV"),
        (0x02, "voltage_dc", "V"),
        (0x21, "current_dc", "A"),
        (0x22, "current_dc", "mA"),
        (0x30, "current_dc", "µA"),
        (0x80, "voltage_ac", "mV"),
        (0x84, "voltage_ac", "V"),
    ],
)
def test_function_codes(code, function, unit):
    r = decode_frame(encode("0123", 1, False, code))
    assert (r.function, r.unit) == (function, unit)


def test_resistance_range_unknown():
    r = decode_frame(encode("0123", 1, False, 0x0C))
    assert r.function == "resistance"
    assert r.range == "code_0x0C"
    assert r.unit is None


def test_unknown_code():
    r = decode_frame(encode("0123", 1, False, 0x7F))
    assert r.function == "unknown"
    assert r.unit is None
    assert r.range == "code_0x7F"
    assert r.value == pytest.approx(12.3)
