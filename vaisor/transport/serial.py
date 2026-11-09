"""Serial port transport backed by pyserial.

The port is created with :func:`serial.serial_for_url`, so besides device paths
(``/dev/ttyUSB0``, ``COM3``) it accepts pyserial URLs such as ``loop://``,
which tests use instead of real hardware.
"""

from __future__ import annotations

from types import TracebackType

import serial

from vaisor.config import SerialConnection
from vaisor.transport.base import TransportError


class SerialTransport:
    """A :class:`~vaisor.transport.base.Transport` over a pyserial port."""

    def __init__(self, config: SerialConnection) -> None:
        self.config = config
        try:
            self._ser = serial.serial_for_url(
                config.port,
                do_not_open=True,
                baudrate=config.baudrate,
                bytesize=config.bytesize,
                parity=config.parity,
                stopbits=config.stopbits,
                timeout=config.timeout,
            )
        except (serial.SerialException, ValueError) as exc:
            raise TransportError(f"{config.port}: cannot configure port: {exc}") from exc

    @property
    def is_open(self) -> bool:
        return bool(self._ser.is_open)

    def open(self) -> None:
        if self.is_open:
            return
        try:
            # Opening a port asserts RTS by default and some meters go silent
            # while it is asserted, so the level must be set before open().
            if self.config.rts is not None:
                self._ser.rts = self.config.rts
            self._ser.open()
        except serial.SerialException as exc:
            raise TransportError(f"{self.config.port}: cannot open port: {exc}") from exc

    def close(self) -> None:
        try:
            self._ser.close()
        except serial.SerialException as exc:
            raise TransportError(f"{self.config.port}: cannot close port: {exc}") from exc

    def write(self, data: bytes) -> None:
        try:
            self._ser.write(data)
            self._ser.flush()
        except serial.SerialException as exc:
            raise TransportError(f"{self.config.port}: write failed: {exc}") from exc

    def read(self, n: int, timeout: float | None = None) -> bytes:
        try:
            if timeout is None:
                return bytes(self._ser.read(n))
            previous = self._ser.timeout
            self._ser.timeout = timeout
            try:
                return bytes(self._ser.read(n))
            finally:
                self._ser.timeout = previous
        except serial.SerialException as exc:
            raise TransportError(f"{self.config.port}: read failed: {exc}") from exc

    def reset_input(self) -> None:
        try:
            self._ser.reset_input_buffer()
        except serial.SerialException as exc:
            raise TransportError(f"{self.config.port}: reset failed: {exc}") from exc

    def set_rts(self, level: bool) -> None:
        try:
            self._ser.rts = level
        except serial.SerialException as exc:
            raise TransportError(f"{self.config.port}: cannot set RTS: {exc}") from exc

    def set_dtr(self, level: bool) -> None:
        try:
            self._ser.dtr = level
        except serial.SerialException as exc:
            raise TransportError(f"{self.config.port}: cannot set DTR: {exc}") from exc

    def __enter__(self) -> SerialTransport:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
