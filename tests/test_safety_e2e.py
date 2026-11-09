"""End-to-end safety: refusals, lock, e-stop, audit and startup over full Bench instances."""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from fake_actuators import FakePSU, FakeRelay

from vaisor import safety
from vaisor.audit import read_entries
from vaisor.bench import Bench
from vaisor.config import BenchConfig, SerialConnection
from vaisor.lock import BenchLock
from vaisor.safety import SafetyRefused
from vaisor.transport import FakeTransport, Transport

CONN = {"type": "serial", "port": "loop://"}
LIMITS = {"max_voltage": 12, "max_current": 1}
DRIVERS = {"psu": FakePSU, "relay": FakeRelay}


def factory(conn: SerialConnection) -> Transport:
    return FakeTransport()


def make(state: Path, *, limits: Any = LIMITS, timeout: float = 0.2) -> Bench:
    psu: dict[str, Any] = {"class": "psu", "driver": "psu", "connection": CONN}
    if limits is not None:
        psu["limits"] = limits
    cfg = BenchConfig.model_validate(
        {
            "state_dir": str(state),
            "estop_relay": "rly",
            "devices": {
                "psu1": psu,
                "rly": {"class": "relay", "driver": "relay", "connection": CONN},
            },
        }
    )
    return Bench.from_config(cfg, DRIVERS, factory, lock_timeout=timeout)  # type: ignore[arg-type]


def log(state: Path) -> list[dict]:
    return read_entries(state / "audit.jsonl")


# -- refusals ----------------------------------------------------------------
@pytest.mark.parametrize(
    "limits,cap,args,code",
    [
        (None, "set_voltage", {"voltage": 1.0}, "no_limits"),
        (None, "output_on", {}, "no_limits"),
        (LIMITS, "set_voltage", {"voltage": 13.0}, "out_of_range"),
        (LIMITS, "set_current", {"current": 2.0}, "out_of_range"),
        (LIMITS, "set_voltage", {"voltage": float("nan")}, "invalid_argument"),
    ],
)
def test_refusals_never_reach_driver(tmp_path, limits, cap, args, code):
    with make(tmp_path, limits=limits) as bench:
        with pytest.raises(SafetyRefused) as ei:
            bench.run("t", "psu1", cap, **args)
        assert ei.value.code == code
        # Only reach the driver via the startup output_off; the command itself never ran.
        assert cap not in bench.device("psu1").names()  # type: ignore[attr-defined]
    last = log(tmp_path)[-1]
    assert last["outcome"] == "refused" and last["refusal_code"] == code


def test_lock_timeout_refusal(tmp_path):
    with make(tmp_path) as bench, BenchLock(tmp_path / "bench.lock").exclusive("other", 0):
        with pytest.raises(SafetyRefused) as ei:
            bench.run("t", "psu1", "set_voltage", voltage=1.0)
    assert ei.value.code == "lock_timeout"
    assert log(tmp_path)[-1]["refusal_code"] == "lock_timeout"


# -- lock --------------------------------------------------------------------
def test_two_benches_contend(tmp_path):
    a, b = make(tmp_path), make(tmp_path)
    with a, b:
        with a.lock.exclusive("a", 0):
            with pytest.raises(SafetyRefused) as ei:
                b.run("b", "psu1", "set_voltage", voltage=1.0)
            assert ei.value.code == "lock_timeout"
        assert b.run("b", "psu1", "set_voltage", voltage=1.0) == {"voltage": 1.0}


def test_shared_reads_coexist_but_block_writes(tmp_path):
    a, b = make(tmp_path), make(tmp_path)
    with a, b:
        with a.lock.shared("a", 0):
            assert b.run("b", "psu1", "read_measurement")["output"] is False
            with pytest.raises(SafetyRefused) as ei:
                b.run("b", "psu1", "output_on")
            assert ei.value.code == "lock_timeout"


def test_stale_lock_recovered_and_audited(tmp_path):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    lock_path = tmp_path / "bench.lock"
    lock_path.write_text(
        json.dumps(
            {"pid": proc.pid, "host": socket.gethostname(), "caller": "ghost",
             "acquired_at": time.time(), "token": "t0"}
        )
    )
    with make(tmp_path, timeout=1.0) as bench:
        assert bench.run("me", "psu1", "set_voltage", voltage=2.0) == {"voltage": 2.0}
    takeover = [e for e in log(tmp_path) if e["command"] == "lock_takeover"]
    assert len(takeover) == 1
    assert takeover[0]["caller"] == "me"
    assert takeover[0]["args"]["stale_owner"]["pid"] == proc.pid
    assert not lock_path.exists()


# -- e-stop ------------------------------------------------------------------
def test_estop_turns_off_opens_relay_and_refuses_later(tmp_path):
    with make(tmp_path) as bench:
        bench.run("t", "psu1", "set_voltage", voltage=5.0)
        bench.run("t", "psu1", "output_on")
        psu, rly = bench.device("psu1"), bench.device("rly")
        assert psu.output  # type: ignore[attr-defined]
        rly.closed = True  # type: ignore[attr-defined]
        res = bench.estop("t", "test")
        assert res["ok"] and res["latched"]
        assert not psu.output and not rly.closed  # type: ignore[attr-defined]
        for cap, args in [("output_on", {}), ("set_voltage", {"voltage": 1.0})]:
            with pytest.raises(SafetyRefused) as ei:
                bench.run("t", "psu1", cap, **args)
            assert ei.value.code == "estopped"
        assert not psu.output  # type: ignore[attr-defined]
        assert bench.run("t", "psu1", "read_measurement")["output"] is False
        assert bench.clear_estop("t") is True
        bench.run("t", "psu1", "output_on")


def test_estop_latch_seen_by_other_bench(tmp_path):
    a, b = make(tmp_path), make(tmp_path)
    with a, b:
        a.estop("a")
        with pytest.raises(SafetyRefused) as ei:
            b.run("b", "psu1", "output_on")
        assert ei.value.code == "estopped"


# -- audit -------------------------------------------------------------------
def test_audit_attempt_then_one_final_record(tmp_path):
    with make(tmp_path) as bench:
        bench.run("c1", "psu1", "set_voltage", voltage=3.0)  # ok
        with pytest.raises(SafetyRefused):
            bench.run("c2", "psu1", "set_voltage", voltage=99.0)  # refused after attempt
        with pytest.raises(SafetyRefused):
            bench.run("c3", "nope", "set_voltage", voltage=1.0)  # refused before attempt
    by_caller: dict[str, list[dict]] = {}
    for e in log(tmp_path):
        by_caller.setdefault(e["caller"], []).append(e)
    assert [e["outcome"] for e in by_caller["c1"]] == ["attempt", "ok"]
    assert [e["outcome"] for e in by_caller["c2"]] == ["attempt", "refused"]
    assert [e["outcome"] for e in by_caller["c3"]] == ["refused"]
    for entries in by_caller.values():
        for e in entries:
            assert ("refusal_code" in e) == (e["outcome"] == "refused")


# -- startup -----------------------------------------------------------------
def test_outputs_off_before_first_command(tmp_path):
    with make(tmp_path) as bench:
        bench.run("t", "psu1", "read_measurement")
        psu = bench.device("psu1")
        assert psu.names()[:2] == ["output_off", "read_measurement"]  # type: ignore[attr-defined]
        assert psu.output is False  # type: ignore[attr-defined]


# -- docs --------------------------------------------------------------------
def test_every_refusal_code_documented():
    doc = (Path(__file__).resolve().parent.parent / "docs" / "safety.md").read_text()
    codes = {
        v
        for k, v in vars(safety).items()
        if k.isupper() and isinstance(v, str) and re.fullmatch(r"[a-z_]+", v)
    }
    codes |= {"lock_timeout", "unknown_device"}  # raised in bench.py
    assert {"no_limits", "out_of_range", "invalid_argument", "estopped"} <= codes
    for code in sorted(codes):
        assert f"`{code}`" in doc, code
