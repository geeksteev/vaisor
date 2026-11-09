"""Exclusive per-bench lock: one controller at a time, with stale-holder recovery.

The lock is a file created with ``O_CREAT|O_EXCL`` (atomic on Linux, macOS and
Windows) holding JSON ``{pid, host, caller, acquired_at, token}``.

A lock is *stale* when its holder's pid is gone (same host only), when
``acquired_at`` is older than ``stale_after`` seconds, or when the file is
corrupt and its mtime is older than ``stale_after``. A stale file is removed by
renaming it to a unique name and unlinking that, so only one recoverer wins.
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
    def _read(self) -> dict[str, Any] | None:
        """Return the parsed owner info, or None if corrupt (or missing)."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        if not isinstance(data.get("token"), str) or not isinstance(data.get("pid"), int):
            return None
        if not isinstance(data.get("acquired_at"), (int, float)):
            return None
        return data

    def _is_stale(self, info: dict[str, Any] | None) -> bool:
        now = time.time()
        if info is None:  # corrupt: judge by mtime
            try:
                mtime = self.path.stat().st_mtime
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
        grabbed = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.rm")
        try:
            os.rename(self.path, grabbed)
        except OSError:
            return False
        try:
            try:
                data = json.loads(grabbed.read_text(encoding="utf-8"))
                found = data.get("token") if isinstance(data, dict) else None
            except (OSError, ValueError):
                found = None
            if found == token:
                return True
            with contextlib.suppress(OSError):  # restore; fails if re-created meanwhile
                os.link(grabbed, self.path)
            return False
        finally:
            with contextlib.suppress(OSError):
                grabbed.unlink()

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
                return

            info = self._read()
            if self._is_stale(info) and self._remove_if(info["token"] if info else None):
                if self.on_stale is not None:
                    self.on_stale(info if info is not None else {"corrupt": True})
                continue
            if time.monotonic() >= deadline:
                raise LockTimeout(self.path, info, timeout)
            time.sleep(min(POLL_INTERVAL, max(0.0, deadline - time.monotonic())))

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
