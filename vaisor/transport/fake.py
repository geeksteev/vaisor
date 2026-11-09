"""A scripted transport for testing drivers without hardware.

A script is a list of exchanges ``(expected_tx, rx)``:

* If ``expected_tx`` is bytes, the exchange waits for the driver to write
  exactly those bytes; then ``rx`` becomes readable. A different write raises
  :class:`AssertionError`.
* If ``expected_tx`` is ``None``, ``rx`` is unsolicited data (e.g. a meter
  that streams continuously) and becomes readable as soon as every earlier
  exchange has been consumed.

Fixture files (``tests/fixtures/<vendor>_<model>/*.jsonl``) hold one exchange
per line as hex strings::

    {"tx": "20", "rx": "020c21b103"}
    {"tx": null, "rx": "020c21b103"}
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType

from vaisor.transport.base import TransportError

Exchange = tuple[bytes | None, bytes]


class FakeTransport:
    """A :class:`~vaisor.transport.base.Transport` that replays a script."""

    def __init__(self, script: Iterable[Exchange] = ()) -> None:
        self._script: list[Exchange] = [(tx, rx) for tx, rx in script]
        self._pos = 0
        self._rx = bytearray()
        self._open = False
        self.written: list[bytes] = []
        self.rts_history: list[bool] = []
        self.dtr_history: list[bool] = []
        self._release_unsolicited()

    @classmethod
    def from_fixture(cls, path: str | Path) -> FakeTransport:
        """Load a JSON-lines fixture of ``{"tx": hex | null, "rx": hex}`` exchanges."""
        path = Path(path)
        script: list[Exchange] = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                tx_hex = entry.get("tx")
                tx = None if tx_hex is None else bytes.fromhex(tx_hex)
                rx = bytes.fromhex(entry.get("rx") or "")
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError(f"{path}:{lineno}: invalid fixture line: {exc}") from exc
            script.append((tx, rx))
        return cls(script)

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def remaining(self) -> int:
        """Number of script exchanges not yet consumed."""
        return len(self._script) - self._pos

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def _check_open(self) -> None:
        if not self._open:
            raise TransportError("fake transport is not open")

    def _release_unsolicited(self) -> None:
        while self._pos < len(self._script) and self._script[self._pos][0] is None:
            self._rx += self._script[self._pos][1]
            self._pos += 1

    def write(self, data: bytes) -> None:
        self._check_open()
        data = bytes(data)
        self.written.append(data)
        if self._pos >= len(self._script):
            return
        expected, rx = self._script[self._pos]
        if expected is not None and data != expected:
            raise AssertionError(
                f"exchange {self._pos}: expected write {expected.hex()!r}, got {data.hex()!r}"
            )
        self._rx += rx
        self._pos += 1
        self._release_unsolicited()

    def read(self, n: int, timeout: float | None = None) -> bytes:
        self._check_open()
        chunk = bytes(self._rx[:n])
        del self._rx[:n]
        return chunk

    def reset_input(self) -> None:
        self._check_open()
        self._rx.clear()

    def set_rts(self, level: bool) -> None:
        self.rts_history.append(level)

    def set_dtr(self, level: bool) -> None:
        self.dtr_history.append(level)

    def __enter__(self) -> FakeTransport:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
