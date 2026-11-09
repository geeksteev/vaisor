import threading
from datetime import datetime

import pytest

from vaisor.audit import AuditError, AuditLog, read_entries


def ev(**kw):
    return {"caller": "t", "command": "set_voltage", "outcome": "ok", **kw}


def test_fields_and_creates_parent(tmp_path):
    p = tmp_path / "a" / "b" / "audit.jsonl"
    rec = AuditLog(p).record(ev(device="psu1", args={"v": 3.3}, result={"ok": True}))
    (e,) = read_entries(p)
    assert e == rec
    assert datetime.fromisoformat(e["ts"]).utcoffset() is not None
    assert e["caller"] == "t" and e["outcome"] == "ok"


def test_refused_roundtrip(tmp_path):
    p = tmp_path / "audit.jsonl"
    AuditLog(p).record(
        ev(outcome="refused", refusal_code="over_limit", error="too high", device=None)
    )
    (e,) = read_entries(p)
    assert e["refusal_code"] == "over_limit"
    assert e["outcome"] == "refused" and e["device"] is None


def test_two_instances_append(tmp_path):
    p = tmp_path / "audit.jsonl"
    a, b = AuditLog(p), AuditLog(p)
    for _ in range(5):
        a.record(ev())
        b.record(ev())
    assert len(read_entries(p)) == 10


def test_threads(tmp_path):
    p = tmp_path / "audit.jsonl"
    log = AuditLog(p)

    def work(n):
        for i in range(200):
            log.record(ev(args={"n": n, "i": i}))

    ts = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    entries = read_entries(p)
    assert len(entries) == 1600
    assert all({"ts", "caller", "command", "outcome"} <= set(e) for e in entries)


def test_unwritable(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(AuditError):
        AuditLog(blocker / "sub" / "audit.jsonl")
    d = tmp_path / "dir"
    d.mkdir()
    with pytest.raises(AuditError):
        AuditLog(d).record(ev())  # path is a directory


def test_redaction(tmp_path):
    p = tmp_path / "audit.jsonl"
    args = {
        "API_Key": "x",
        "Token": "y",
        "pw_password": "z",
        "secret1": 1,
        "nested": {"token": "t", "v": 1},
        "volts": 5,
    }
    rec = AuditLog(p).record(ev(args=args))
    a = rec["args"]
    assert a["API_Key"] == a["Token"] == a["pw_password"] == a["secret1"] == "***"
    assert a["nested"] == {"token": "***", "v": 1} and a["volts"] == 5


@pytest.mark.parametrize(
    "bad",
    [
        ev(args={"x": object()}),
        ev(args={"x": {1, 2}}),
        ev(args={"x": float("nan")}),
        ev(args={1: "a"}),
        ev(result=b"bytes"),
    ],
)
def test_not_json_safe(tmp_path, bad):
    p = tmp_path / "audit.jsonl"
    with pytest.raises(ValueError):
        AuditLog(p).record(bad)
    assert not p.exists()


@pytest.mark.parametrize(
    "bad",
    [
        {"command": "c", "outcome": "ok"},
        ev(caller=""),
        ev(command=1),
        ev(outcome="nope"),
        ev(device=3),
        ev(args=[1]),
        ev(extra=1),
    ],
)
def test_validation(tmp_path, bad):
    with pytest.raises(ValueError):
        AuditLog(tmp_path / "a.jsonl").record(bad)
