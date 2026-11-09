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


def test_two_shared_holders_overlap(tmp_path):
    lock = BenchLock(tmp_path / "lock")
    with lock.shared("a", 0), BenchLock(tmp_path / "lock").shared("b", 0):
        assert len(list(lock.readers_dir.glob("*.json"))) == 2


def test_exclusive_times_out_while_shared_and_cleans_up(tmp_path):
    p = tmp_path / "lock"
    with BenchLock(p).shared("r", 0):
        with pytest.raises(LockTimeout):
            BenchLock(p).acquire("w", 0.15)
        assert not p.exists()
    BenchLock(p).acquire("w", 0)


def test_shared_times_out_while_exclusive(tmp_path):
    p = tmp_path / "lock"
    w = BenchLock(p)
    w.acquire("w", 0)
    with pytest.raises(LockTimeout):
        with BenchLock(p).shared("r", 0.15):
            pass
    assert not list(w.readers_dir.glob("*.json"))
    assert p.exists()
    w.release()


def test_stale_reader_does_not_block_exclusive(tmp_path):
    p = tmp_path / "lock"
    lock = BenchLock(p)
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    lock.readers_dir.mkdir()
    stale = lock.readers_dir / "dead.json"
    write_lock(stale, pid=proc.pid, token="dead")
    lock.acquire("w", 0.5)
    assert not stale.exists()
    lock.release()


def test_shared_release_removes_only_own_file(tmp_path):
    lock = BenchLock(tmp_path / "lock")
    with lock.shared("a", 0):
        with BenchLock(tmp_path / "lock").shared("b", 0):
            pass
        assert len(list(lock.readers_dir.glob("*.json"))) == 1
    assert not list(lock.readers_dir.glob("*.json"))


def test_mixed_readers_writers_never_overlap(tmp_path):
    p = tmp_path / "lock"
    state = {"readers": 0, "writers": 0, "bad": 0, "ops": 0}
    mu = threading.Lock()

    def enter(kind):
        with mu:
            state[kind] += 1
            state["ops"] += 1
            if state["writers"] > 1 or (state["writers"] and state["readers"]):
                state["bad"] += 1

    def leave(kind):
        with mu:
            state[kind] -= 1

    def reader():
        for _ in range(15):
            with BenchLock(p).shared("r", 20):
                enter("readers")
                time.sleep(0.002)
                leave("readers")

    def writer():
        for _ in range(8):
            with BenchLock(p).exclusive("w", 20):
                enter("writers")
                time.sleep(0.002)
                leave("writers")

    ts = [threading.Thread(target=reader) for _ in range(4)] + [
        threading.Thread(target=writer) for _ in range(2)
    ]
    for th in ts:
        th.start()
    for th in ts:
        th.join()
    assert state["ops"] == 4 * 15 + 2 * 8
    assert state["bad"] == 0


def test_stale_recovery_concurrent_with_acquires_keeps_mutual_exclusion(tmp_path):
    p = tmp_path / "lock"
    state = {"inside": 0}
    violations = []
    mu = threading.Lock()
    stop = threading.Event()
    done = []

    def seeder():
        # Simulate crashed holders: dead-pid lock files appear whenever the path is free.
        n = 0
        while not stop.is_set():
            n += 1
            try:
                fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                time.sleep(0.0002)
                continue
            info = {
                "pid": 2**22 + 12345,
                "host": socket.gethostname(),
                "caller": "dead",
                "acquired_at": time.time(),
                "token": f"dead{n}",
            }
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps(info))

    def worker():
        lock = BenchLock(p, stale_after=300)
        for _ in range(40):
            lock.acquire("w", timeout=60)
            with mu:
                state["inside"] += 1
                if state["inside"] != 1:
                    violations.append(state["inside"])
            time.sleep(0.0005)
            with mu:
                state["inside"] -= 1
            lock.release()
        done.append(1)

    s = threading.Thread(target=seeder)
    workers = [threading.Thread(target=worker) for _ in range(6)]
    s.start()
    for t in workers:
        t.start()
    for t in workers:
        t.join(120)
    stop.set()
    s.join(10)
    assert len(done) == 6
    assert not violations


def test_inspect_absent_live_corrupt_and_stale(tmp_path):
    p = tmp_path / "lock"
    lock = BenchLock(p)
    assert lock.inspect() is None

    write_lock(p)
    info = lock.inspect()
    assert info["caller"] == "other" and info["stale"] is False

    p.write_text("{not json")
    assert lock.inspect()["corrupt"] is True

    write_lock(p, pid=2**22 + 12345)
    assert lock.inspect()["stale"] is True
    assert p.exists()  # inspect never removes
