"""The transport interface every driver talks through.

Drivers never open serial ports, USB devices or sockets themselves; they are
handed a :class:`Transport`. In tests that transport is a
:class:`~vaisor.transport.fake.FakeTransport` replaying recorded bytes, so no
hardware is needed.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, runtime_checkable


class TransportError(Exception):
    """Raised when a transport cannot be opened, read or written."""


@runtime_checkable
class Transport(Protocol):
    """A byte stream to one instrument, plus the modem control lines."""

    @property
    def is_open(self) -> bool: ...

    def open(self) -> None: ...

    def close(self) -> None: ...

    def write(self, data: bytes) -> None: ...

    def read(self, n: int, timeout: float | None = None) -> bytes:
        """Return up to ``n`` bytes; fewer if ``timeout`` (seconds) expires first.

        ``timeout=None`` uses the transport's configured default.
        """
        ...

    def reset_input(self) -> None:
        """Discard any bytes received but not yet read."""
        ...

    def set_rts(self, level: bool) -> None: ...

    def set_dtr(self, level: bool) -> None: ...

    def __enter__(self) -> Transport: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
