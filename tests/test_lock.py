import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

from vaisor.config import load_bench
from vaisor.lock import BenchLock, LockTimeout


def write_lock(path, **kw):
    info = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "caller": "other",
        "acquired_at": time.time(),
        "token": "tok",
    }
    info.update(kw)
    path.write_text(json.dumps(info))
    return info


def test_acquire_release_and_timeout(tmp_path):
    p = tmp_path / "lock"
    a, b = BenchLock(p), BenchLock(p)
    a.acquire("cli", 0)
    info = json.loads(p.read_text())
    assert set(info) == {"pid", "host", "caller", "acquired_at", "token"}
    with pytest.raises(LockTimeout) as e:
        b.acquire("mcp", 0.15)
    assert e.value.caller == "cli" and e.value.pid == os.getpid()
    assert "cli" in str(e.value) and str(os.getpid()) in str(e.value)
    a.release()
    assert not p.exists()
    b.acquire("mcp", 0)
    b.release()


def test_release_wrong_token_keeps_file(tmp_path):
    p = tmp_path / "lock"
    a = BenchLock(p)
    a.acquire("cli", 0)
    info = json.loads(p.read_text())
    info["token"] = "someone-else"
    p.write_text(json.dumps(info))
    a.release()
    assert p.exists()
    assert json.loads(p.read_text())["token"] == "someone-else"


def test_dead_pid_recovered(tmp_path):
    p = tmp_path / "lock"
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    old = write_lock(p, pid=proc.pid)
    seen = []
    lock = BenchLock(p, on_stale=seen.append)
    lock.acquire("me", 1)
    assert seen == [old]
    assert json.loads(p.read_text())["caller"] == "me"
    lock.release()


def test_live_young_not_stolen(tmp_path):
    p = tmp_path / "lock"
    write_lock(p)
    seen = []
    with pytest.raises(LockTimeout):
        BenchLock(p, on_stale=seen.append).acquire("me", 0.15)
    assert seen == [] and p.exists()


def test_old_lock_recovered_even_if_alive(tmp_path):
    p = tmp_path / "lock"
    write_lock(p, acquired_at=time.time() - 1000)
    seen = []
    lock = BenchLock(p, stale_after=60, on_stale=seen.append)
    lock.acquire("me", 1)
    assert len(seen) == 1
    lock.release()


def test_corrupt_needs_old_mtime(tmp_path):
    p = tmp_path / "lock"
    p.write_text("{not json")
    seen = []
    lock = BenchLock(p, stale_after=60, on_stale=seen.append)
    with pytest.raises(LockTimeout):
        lock.acquire("me", 0.15)
    assert p.exists() and seen == []
    old = time.time() - 1000
    os.utime(p, (old, old))
    lock.acquire("me", 1)
    assert len(seen) == 1
    lock.release()


def test_threads_never_hold_together(tmp_path):
    p = tmp_path / "lock"
    state = {"inside": 0, "bad": 0}
    guard = threading.Lock()

    def work(i):
        for _ in range(5):
            with BenchLock(p).exclusive(f"t{i}", 30):
                with guard:
                    state["inside"] += 1
                    if state["inside"] > 1:
                        state["bad"] += 1
                time.sleep(0.005)
                with guard:
                    state["inside"] -= 1

    ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert state["bad"] == 0 and not p.exists()


def test_exclusive_releases_on_error(tmp_path):
    p = tmp_path / "lock"
    with pytest.raises(ValueError):
        with BenchLock(p).exclusive("x", 0):
            raise ValueError
    assert not p.exists()


def test_state_dir_config(tmp_path):
    f = tmp_path / "bench.yaml"
    f.write_text("devices: {}\n")
    assert load_bench(f).state_dir == ".vaisor"
    f.write_text("state_dir: st\ndevices: {}\n")
    assert load_bench(f).state_dir == "st"


def test_gitignore_has_state_dir():
    root = os.path.dirname(os.path.dirname(__file__))
    with open(os.path.join(root, ".gitignore")) as fh:
        assert ".vaisor/" in fh.read().split()
