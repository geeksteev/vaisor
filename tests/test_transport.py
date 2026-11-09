from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import serial

from vaisor.config import SerialConnection
from vaisor.transport import (
    FakeTransport,
    SerialTransport,
    Transport,
    TransportError,
    make_transport,
)


def conn(**kwargs: Any) -> SerialConnection:
    data: dict[str, Any] = {"type": "serial", "port": "loop://", "timeout": 0.05}
    data.update(kwargs)
    return SerialConnection.model_validate(data)


# --- FakeTransport ---------------------------------------------------------


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeTransport(), Transport)


def test_fake_scripted_exchanges() -> None:
    fake = FakeTransport([(b"\x20", b"\x02\x0c"), (b"A", b"OK\r\n")])
    with fake:
        assert fake.read(10) == b""  # nothing until the first write
        fake.write(b"\x20")
        assert fake.read(1) == b"\x02"
        assert fake.read(10) == b"\x0c"
        fake.write(b"A")
        assert fake.read(10) == b"OK\r\n"
    assert fake.written == [b"\x20", b"A"]
    assert fake.remaining == 0
    assert not fake.is_open


def test_fake_mismatched_write_raises() -> None:
    fake = FakeTransport([(b"\x20", b"\x02")])
    fake.open()
    with pytest.raises(AssertionError, match="expected write '20', got '21'"):
        fake.write(b"\x21")
    assert fake.written == [b"\x21"]


def test_fake_short_read_at_end_of_script() -> None:
    fake = FakeTransport([(b"Q", b"abc")])
    fake.open()
    fake.write(b"Q")
    assert fake.read(10, timeout=0.5) == b"abc"
    assert fake.read(10) == b""
    fake.write(b"extra")  # past the end of the script: recorded, no reply
    assert fake.read(10) == b""
    assert fake.written == [b"Q", b"extra"]


def test_fake_unsolicited_rx_is_readable_without_writes() -> None:
    fake = FakeTransport([(None, b"12"), (None, b"34"), (b"X", b"y"), (None, b"z")])
    fake.open()
    assert fake.read(10) == b"1234"
    fake.write(b"X")
    assert fake.read(10) == b"yz"


def test_fake_reset_input_discards_pending() -> None:
    fake = FakeTransport([(None, b"stale")])
    fake.open()
    fake.reset_input()
    assert fake.read(10) == b""


def test_fake_requires_open() -> None:
    fake = FakeTransport([(None, b"x")])
    with pytest.raises(TransportError):
        fake.read(1)
    with pytest.raises(TransportError):
        fake.write(b"x")


def test_fake_rts_and_dtr_history() -> None:
    fake = FakeTransport()
    fake.set_rts(False)
    fake.set_rts(True)
    fake.set_dtr(True)
    assert fake.rts_history == [False, True]
    assert fake.dtr_history == [True]


def test_fake_from_fixture(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        '{"tx": "20", "rx": "020c21b103"}\n'
        "\n"
        '{"tx": null, "rx": "0a0B"}\n',
        encoding="utf-8",
    )
    fake = FakeTransport.from_fixture(path)
    with fake:
        assert fake.read(10) == b""
        fake.write(b"\x20")
        assert fake.read(10) == bytes.fromhex("020c21b1030a0b")


def test_fake_from_fixture_bad_line(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"tx": "20", "rx": "02"}\n{"tx": "zz", "rx": ""}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"bad\.jsonl:2"):
        FakeTransport.from_fixture(path)


# --- SerialTransport ---------------------------------------------------------


def test_serial_loopback_round_trip() -> None:
    t = SerialTransport(conn(baudrate=19200, parity="E", stopbits=2, bytesize=7))
    assert isinstance(t, Transport)
    assert not t.is_open
    with t:
        assert t.is_open
        assert t._ser.baudrate == 19200
        assert t._ser.parity == "E"
        assert t._ser.stopbits == 2
        assert t._ser.bytesize == 7
        t.write(b"\x20hello")
        assert t.read(6) == b"\x20hello"
        assert t.read(4, timeout=0.01) == b""  # short read on timeout
        assert t._ser.timeout == 0.05  # per-call timeout restored
        t.write(b"junk")
        t.reset_input()
        assert t.read(4, timeout=0.01) == b""
        t.set_rts(True)
        t.set_dtr(False)
    assert not t.is_open


def test_make_transport_returns_serial_transport() -> None:
    t = make_transport(conn())
    assert isinstance(t, SerialTransport)
    assert not t.is_open


def test_serial_rts_false_set_before_open(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, Any]] = []

    class RecordingPort:
        is_open = False
        timeout = 1.0

        def __setattr__(self, name: str, value: Any) -> None:
            events.append(("set", (name, value)))
            object.__setattr__(self, name, value)

        def open(self) -> None:
            events.append(("open", None))
            object.__setattr__(self, "is_open", True)

    created: dict[str, Any] = {}

    def fake_serial_for_url(url: str, *args: Any, **kwargs: Any) -> RecordingPort:
        created["url"] = url
        created["kwargs"] = kwargs
        return RecordingPort()

    monkeypatch.setattr(serial, "serial_for_url", fake_serial_for_url)

    t = SerialTransport(conn(port="/dev/ttyFAKE", rts=False))
    assert created["url"] == "/dev/ttyFAKE"
    assert created["kwargs"]["do_not_open"] is True
    t.open()
    assert events.index(("set", ("rts", False))) < events.index(("open", None))


def test_serial_rts_false_survives_open_on_loopback() -> None:
    t = SerialTransport(conn(rts=False))
    with t:
        assert t._ser.rts is False


def test_serial_open_error_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    t = SerialTransport(conn())

    def boom() -> None:
        raise serial.SerialException("no such port")

    monkeypatch.setattr(t._ser, "open", boom)
    with pytest.raises(TransportError, match="no such port"):
        t.open()


def test_serial_bad_url_wrapped() -> None:
    with pytest.raises(TransportError):
        SerialTransport(conn(port="nosuchscheme://x"))
