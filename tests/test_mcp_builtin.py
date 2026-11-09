"""Built-in MCP tool tests: no hardware, ports or network."""

from __future__ import annotations

import json
import time

import pytest
from test_cli import Factory, bench_file  # noqa: F401
from test_mcp_catalogue import make

from vaisor.bench import Bench
from vaisor.cli import main
from vaisor.config import load_bench
from vaisor.lock import BenchLock
from vaisor.mcp.builtin import BUILTIN_TOOLS, call_builtin
from vaisor.safety import ESTOPPED, SafetyRefused


def test_schemas():
    by = {t.name: t for t in BUILTIN_TOOLS}
    assert set(by) == {"list_devices", "bench_status", "estop"}
    assert by["estop"].input_schema["properties"]["reason"]["maxLength"] == 200
    json.dumps([t.input_schema for t in BUILTIN_TOOLS])


def test_list_devices_matches_cli(bench_file, capsys):  # noqa: F811
    f = Factory("ohm_12_3k")
    assert main(["--config", bench_file, "devices", "--json"], transport_factory=f) == 0
    cli_rows = json.loads(capsys.readouterr().out)
    with Bench.from_config(load_bench(bench_file), transport_factory=f) as bench:
        assert call_builtin(bench, "list_devices", {}) == cli_rows
    assert f.created == []


def test_estop_then_status_and_refusal(tmp_path):
    with make(tmp_path) as bench:
        psu = bench.device("psu_a")
        psu.output = True  # type: ignore[attr-defined]
        assert call_builtin(bench, "bench_status", None)["estop"] is None
        res = call_builtin(bench, "estop", {"reason": "smoke"})
        assert res["latched"] is True
        assert (tmp_path / "estop.json").exists()
        assert not psu.output  # type: ignore[attr-defined]
        assert call_builtin(bench, "estop", {})["latched"] is True  # works while latched
        status = call_builtin(bench, "bench_status", None)
        assert status["estop"]["caller"] == "mcp:stdio"
        with pytest.raises(SafetyRefused) as exc:
            bench.run("t", "psu_a", "set_voltage", voltage=1.0)
        assert exc.value.code == ESTOPPED


def test_estop_reason_too_long(tmp_path):
    with make(tmp_path) as bench:
        with pytest.raises(ValueError):
            call_builtin(bench, "estop", {"reason": "x" * 201})
        assert bench.estop_state() is None


def test_bench_status_does_not_take_lock(tmp_path):
    with make(tmp_path) as bench:
        other = BenchLock(bench.lock.path)
        other.acquire("someone-else")
        try:
            start = time.monotonic()
            status = call_builtin(bench, "bench_status", None)
            assert time.monotonic() - start < 1.0
            assert status["lock_holder"]["caller"] == "someone-else"
            assert status["bench"] == bench.config.name
        finally:
            other.release()
        assert call_builtin(bench, "bench_status", None)["lock_holder"] is None
