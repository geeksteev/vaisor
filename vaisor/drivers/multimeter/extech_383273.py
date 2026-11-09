"""Extech 383273 multimeter: protocol frame decoder (pure, no I/O).

Protocol
--------
The meter replies with a 5-byte frame::

    02 | function/range | lo | hi | 03

The digits word is ``w = hi << 8 | lo``. Bit numbers below are bits of ``w``.

* bit 0: polarity, 1 = positive, 0 = negative. For frequency functions,
  0 = kHz and 1 = MHz instead.
* bit 1: leading digit (0 or 1).
* bits 2-5, 6-9, 10-13: the next three digits.
* bits 14-15: decimal point code: 00 = ``0000``, 01 = ``000.0``,
  10 = ``00.00``, 11 = ``0.000``.

Every multi-bit field is stored MSB-first: the lowest-numbered bit of a field
is its most significant bit. E.g. the digit in bits 2-5 is
``(b2<<3)|(b3<<2)|(b4<<1)|b5`` and the decimal point code is ``(b14<<1)|b15``.

Worked example from the manual: ``02 0C 21 B1 03`` -> ``w = 0xB121``: positive,
leading digit 0, digits 1, 2, 3, dp code 01 -> display ``012.3``; function code
``0x0C`` is OHM 200 kΩ, so the reading is +12.3 kΩ.

A digit field decoding to 10-15 means overrange or a special display; the
reading then has ``value=None`` and ``overrange=True``.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from vaisor.drivers.base import DriverError
from vaisor.drivers.multimeter import Multimeter
from vaisor.measurement import Reading

log = logging.getLogger(__name__)

POLL = b" "
# The meter treats these ASCII characters as commands; never transmit them.
RESERVED_BYTES = b"9421EGNRWUSTX"
MAX_DISCARD = 16
TIMEOUT_MSG = "timeout: no reply from meter (is RTS low / meter on / RS232 enabled?)"
STX = 0x02
ETX = 0x03
FRAME_LEN = 5


class ProtocolError(DriverError):
    """The meter sent a malformed frame."""


# code -> (function, range, unit)
# verify against manual
FUNCTION_TABLE: dict[int, tuple[str, str | None, str | None]] = {
    0x00: ("voltage_dc", "200mV", "mV"),
    0x01: ("voltage_dc", "2V", "V"),
    0x02: ("voltage_dc", "20V", "V"),
    0x03: ("voltage_dc", "200V", "V"),
    0x04: ("voltage_dc", "1000V", "V"),
    0x21: ("current_dc", "20A", "A"),
    0x22: ("current_dc", "200mA", "mA"),
    0x24: ("current_dc", "20mA", "mA"),
    0x28: ("current_dc", "2mA", "mA"),
    0x30: ("current_dc", "200µA", "µA"),
    0x80: ("voltage_ac", "200mV", "mV"),
    0x81: ("voltage_ac", "2V", "V"),
    0x82: ("voltage_ac", "20V", "V"),
    0x83: ("voltage_ac", "200V", "V"),
    0x84: ("voltage_ac", "1000V", "V"),
}
# Resistance ranges are not known yet: keep the raw code in the range label.
for _code in range(0x08, 0x12):
    FUNCTION_TABLE[_code] = ("resistance", f"code_0x{_code:02X}", None)
del _code
# Only 0x0C (200 kΩ) is confirmed by the manual's example.
FUNCTION_TABLE[0x0C] = ("resistance", "200kΩ", "kΩ")


def _field(w: int, first_bit: int, width: int) -> int:
    """Extract a field whose lowest-numbered bit is its MSB."""
    out = 0
    for i in range(width):
        out = (out << 1) | ((w >> (first_bit + i)) & 1)
    return out


def decode_frame(frame: bytes, device: str = "") -> Reading:
    """Decode one 5-byte reply into a :class:`Reading`."""
    if len(frame) != FRAME_LEN:
        raise ProtocolError(f"expected {FRAME_LEN} bytes, got {len(frame)}")
    if frame[0] != STX:
        raise ProtocolError(f"bad start byte 0x{frame[0]:02X}, expected 0x02")
    if frame[4] != ETX:
        raise ProtocolError(f"bad end byte 0x{frame[4]:02X}, expected 0x03")

    code = frame[1]
    w = (frame[3] << 8) | frame[2]
    function, rng, unit = FUNCTION_TABLE.get(
        code, ("unknown", f"code_0x{code:02X}", None)
    )

    bit0 = w & 1
    negative = bit0 == 0
    if function.startswith("frequency"):
        negative = False
        if bit0:
            unit = "MHz"

    digits = [(w >> 1) & 1] + [_field(w, s, 4) for s in (2, 6, 10)]
    dp = _field(w, 14, 2)

    if any(d > 9 for d in digits):
        value = None
        overrange = True
    else:
        text = "".join(str(d) for d in digits)
        if dp:
            text = text[:-dp] + "." + text[-dp:]
        value = float(text)
        if negative:
            value = -value
        overrange = False

    return Reading(
        device=device,
        value=value,
        unit=unit,
        function=function,
        range=rng,
        overrange=overrange,
        raw=bytes(frame),
    )


class Extech383273(Multimeter):
    """Extech 383273 over RS232: RTS held low, one SPACE polls one frame."""

    capabilities: ClassVar[frozenset[str]] = frozenset({"read_measurement"})

    def open(self) -> None:
        conn = self.config.connection
        wanted = (9600, 8, "N", 1)
        actual = (conn.baudrate, conn.bytesize, conn.parity, conn.stopbits)
        if actual != wanted:
            log.warning(
                "%s: Extech 383273 needs 9600 8N1 but config has %d %d%s%d; "
                "the meter will probably not answer",
                self.name,
                *actual,
            )
        super().open()
        self.transport.set_rts(False)

    def _send(self, data: bytes) -> None:
        bad = sorted(set(data) & set(RESERVED_BYTES))
        if bad:
            names = ", ".join("0x%02X" % b for b in bad)
            raise DriverError("refusing to send reserved command byte(s) " + names)
        self.transport.write(data)

    def read_measurement(self) -> Reading:
        timeout = self.config.connection.timeout
        self.transport.reset_input()
        self._send(POLL)
        buf = self.transport.read(FRAME_LEN, timeout)
        if len(buf) < FRAME_LEN:
            raise DriverError(TIMEOUT_MSG)
        discarded = 0
        while buf[0] != STX:
            idx = buf.find(bytes([STX]))
            skip = len(buf) if idx < 0 else idx
            discarded += skip
            if discarded > MAX_DISCARD:
                raise DriverError(
                    "no frame start from meter within %d bytes" % MAX_DISCARD
                )
            buf = buf[skip:]
            buf += self.transport.read(FRAME_LEN - len(buf), timeout)
            if len(buf) < FRAME_LEN:
                raise DriverError(TIMEOUT_MSG)
        return decode_frame(buf, device=self.name)
