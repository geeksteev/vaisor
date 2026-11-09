"""Exclusive per-bench lock: one controller at a time, with stale-holder recovery.

The lock is a file created with ``O_CREAT|O_EXCL`` (atomic on Linux, macOS and
Windows) holding JSON ``{pid, host, caller, acquired_at, token}``.

A lock is *stale* when its holder's pid is gone (same host only), when
``acquired_at`` is older than ``stale_after`` seconds, or when the file is
corrupt and its mtime is older than ``stale_after``. A stale file is removed by
unlinking it in place under a separate recovery guard file that serialises all
removals (token re-verified under the guard), so a live lock is never moved or lost.

Shared (read-only) access
-------------------------
Readers register a file ``<lock>.readers/<token>.json`` (same JSON, same
staleness rules; stale reader files are simply unlinked). Protocol:

* Reader: (1) register its file, (2) check that no exclusive lock file exists.
  If one does, deregister and wait/retry.
* Writer: (1) create the exclusive lock file, (2) wait until the readers
  directory has no live entries. On timeout it removes its lock file and raises
  ``LockTimeout``.

Race argument: each side writes its own marker *before* looking at the other's.
Either the reader's check (2) comes after the writer's create (the reader sees
the lock and backs off), or it comes before it, in which case the reader's
registration precedes the writer's scan (the writer sees the reader and
waits). Both cannot miss each other, so a writer and a reader are never active
together. Readers that back off never wait while registered, and the writer
only waits for readers that were already registered, so there is no deadlock.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

POLL_INTERVAL = 0.05
GUARD_STALE_AFTER = 5.0


class LockTimeout(Exception):
    """Raised when the bench lock could not be acquired in time."""

    def __init__(self, path: Path, owner: dict[str, Any] | None, timeout: float) -> None:
        self.path = path
        self.owner = owner or {}
        self.caller = self.owner.get("caller")
        self.pid = self.owner.get("pid")
        who = f"caller {self.caller!r} (pid {self.pid})" if owner else "an unknown owner"
        super().__init__(f"bench lock {path} is held by {who}; timed out after {timeout:g}s")


def pid_alive(pid: int) -> bool:
    """Return whether process ``pid`` exists on this host."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return bool(ctypes.GetLastError() == 5)  # access denied: exists
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return bool(code.value == 259)  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class BenchLock:
    def __init__(
        self,
        path: str | os.PathLike[str],
        stale_after: float = 300.0,
        on_stale: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.stale_after = stale_after
        self.on_stale = on_stale
        self._token: str | None = None

    # -- helpers ---------------------------------------------------------
    @property
    def readers_dir(self) -> Path:
        return self.path.with_name(f"{self.path.name}.readers")

    def _read(self, path: Path | None = None) -> dict[str, Any] | None:
        """Return the parsed owner info, or None if corrupt (or missing)."""
        try:
            data = json.loads((path or self.path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        if not isinstance(data.get("token"), str) or not isinstance(data.get("pid"), int):
            return None
        if not isinstance(data.get("acquired_at"), (int, float)):
            return None
        return data

    def _is_stale(self, info: dict[str, Any] | None, path: Path | None = None) -> bool:
        now = time.time()
        if info is None:  # corrupt: judge by mtime
            try:
                mtime = (path or self.path).stat().st_mtime
            except OSError:
                return False
            return now - mtime > self.stale_after
        if now - info["acquired_at"] > self.stale_after:
            return True
        return info.get("host") == socket.gethostname() and not pid_alive(info["pid"])

    def _remove_if(self, token: str | None) -> bool:
        """Atomically remove the lock file if it still holds ``token``.

        ``token`` None means the file is expected to be corrupt. The file is
        renamed to a unique name first so that only one caller can win; if what
        we grabbed turns out to be a different owner's lock, it is put back.
        """
        with self._guard() as held:
            if not held:
                return False
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                found = data.get("token") if isinstance(data, dict) else None
            except FileNotFoundError:
                return False
            except (OSError, ValueError):
                found = None
            if found != token:
                return False
            try:
                self.path.unlink()
            except FileNotFoundError:
                return False
            return True

    @property
    def _guard_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.recover")

    @contextlib.contextmanager
    def _guard(self) -> Iterator[bool]:
        """Serialise every removal of the lock file (stale recovery and release).

        The guard is a separate ``O_EXCL`` file. All removals happen under it, so
        check-token-then-unlink cannot interleave with another removal, and the
        lock file is never renamed away: a live owner's lock never disappears.
        A guard left by a crashed process is broken after ``GUARD_STALE_AFTER``
        seconds. Yields False if the guard could not be taken in time.
        """
        deadline = time.monotonic() + GUARD_STALE_AFTER * 2
        held = False
        while True:
            try:
                fd = os.open(self._guard_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                try:
                    age = time.time() - self._guard_path.stat().st_mtime
                except OSError:
                    continue  # vanished; retry
                if age > GUARD_STALE_AFTER:
                    with contextlib.suppress(OSError):
                        self._guard_path.unlink()
                    continue
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.001)
                continue
            os.close(fd)
            held = True
            break
        try:
            yield held
        finally:
            if held:
                with contextlib.suppress(OSError):
                    self._guard_path.unlink()

    # -- API -------------------------------------------------------------
    def acquire(self, caller: str, timeout: float = 0.0) -> None:
        """Take the lock, polling until ``timeout`` seconds pass, else raise LockTimeout."""
        if self._token is not None:
            raise RuntimeError("BenchLock is already held by this object")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        while True:
            token = uuid.uuid4().hex
            payload = {
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "caller": caller,
                "acquired_at": time.time(),
                "token": token,
            }
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                pass
            else:
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        fh.write(json.dumps(payload))
                except BaseException:
                    with contextlib.suppress(OSError):
                        self.path.unlink()
                    raise
                self._token = token
                break

            info = self._read()
            if self._is_stale(info) and self._remove_if(info["token"] if info else None):
                if self.on_stale is not None:
                    self.on_stale(info if info is not None else {"corrupt": True})
                continue
            if time.monotonic() >= deadline:
                raise LockTimeout(self.path, info, timeout)
            time.sleep(min(POLL_INTERVAL, max(0.0, deadline - time.monotonic())))

        # The lock file is ours; now wait for registered readers to leave.
        try:
            while True:
                live = self._live_readers()
                if not live:
                    return
                if time.monotonic() >= deadline:
                    raise LockTimeout(self.path, live[0], timeout)
                time.sleep(min(POLL_INTERVAL, max(0.0, deadline - time.monotonic())))
        except BaseException:
            self.release()
            raise

    def inspect(self) -> dict[str, Any] | None:
        """Read-only view of the exclusive holder; never acquires, removes or writes.

        Returns ``{caller, pid, host, acquired_at, stale}`` or ``None`` when no
        lock file exists. A corrupt file is reported as ``{"corrupt": True, "stale": ...}``.
        """
        if not self.path.exists():
            return None
        info = self._read()
        stale = self._is_stale(info)
        if info is None:
            return {"corrupt": True, "stale": stale}
        return {
            "caller": info.get("caller"),
            "pid": info["pid"],
            "host": info.get("host"),
            "acquired_at": info["acquired_at"],
            "stale": stale,
        }

    def _live_readers(self) -> list[dict[str, Any]]:
        """Return info of live readers, unlinking stale reader files."""
        live: list[dict[str, Any]] = []
        for f in self.readers_dir.glob("*.json"):
            if not f.exists():
                continue
            info = self._read(f)
            if self._is_stale(info, f):
                with contextlib.suppress(OSError):
                    f.unlink()
                if self.on_stale is not None:
                    self.on_stale(info if info is not None else {"corrupt": True})
                continue
            live.append(info if info is not None else {"corrupt": True})
        return live

    def release(self) -> None:
        """Remove the lock file only if it is still ours."""
        token, self._token = self._token, None
        if token is not None:
            self._remove_if(token)

    @contextlib.contextmanager
    def exclusive(self, caller: str, timeout: float = 0.0) -> Iterator[None]:
        self.acquire(caller, timeout)
        try:
            yield
        finally:
            self.release()

    @contextlib.contextmanager
    def shared(self, caller: str, timeout: float = 0.0) -> Iterator[None]:
        """Hold the bench for shared (read-only) access; many readers may overlap."""
        token = self._register_reader(caller, timeout)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                (self.readers_dir / f"{token}.json").unlink()

    def _register_reader(self, caller: str, timeout: float) -> str:
        self.readers_dir.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        token = uuid.uuid4().hex
        rfile = self.readers_dir / f"{token}.json"
        payload = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "caller": caller,
            "acquired_at": time.time(),
            "token": token,
        }
        while True:
            # Register first (atomically via rename), then look for a writer.
            tmp = self.readers_dir / f"{token}.tmp"
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, rfile)
            if not self.path.exists():
                return token
            info = self._read()
            if self._is_stale(info) and self._remove_if(info["token"] if info else None):
                if self.on_stale is not None:
                    self.on_stale(info if info is not None else {"corrupt": True})
                continue  # still registered; re-check
            with contextlib.suppress(OSError):
                rfile.unlink()
            if time.monotonic() >= deadline:
                raise LockTimeout(self.path, info, timeout)
            time.sleep(min(POLL_INTERVAL, max(0.0, deadline - time.monotonic())))
