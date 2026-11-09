"""Transports: the only way drivers reach hardware."""

from __future__ import annotations

from vaisor.config import SerialConnection
from vaisor.transport.base import Transport, TransportError
from vaisor.transport.fake import FakeTransport
from vaisor.transport.serial import SerialTransport

__all__ = [
    "FakeTransport",
    "SerialTransport",
    "Transport",
    "TransportError",
    "make_transport",
]


def make_transport(conn: SerialConnection) -> Transport:
    """Build the transport for a device's ``connection`` block (not opened yet)."""
    return SerialTransport(conn)
