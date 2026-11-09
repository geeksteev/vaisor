from __future__ import annotations

import importlib.metadata
from importlib.metadata import EntryPoint

import pytest
from dummy_drivers import DummyMeter, DummyPsu

from vaisor.drivers import DriverError
from vaisor.registry import discover_drivers, get_driver

G = "vaisor.drivers"


def patch(monkeypatch: pytest.MonkeyPatch, *eps: EntryPoint) -> None:
    def fake(**kw: str) -> list[EntryPoint]:
        assert kw == dict(group=G)
        return list(eps)

    monkeypatch.setattr(importlib.metadata, "entry_points", fake)


def test_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(
        monkeypatch,
        EntryPoint("meter", "dummy_drivers:DummyMeter", G),
        EntryPoint("psu", "dummy_drivers:DummyPsu", G),
    )
    assert discover_drivers() == dict(meter=DummyMeter, psu=DummyPsu)


def test_duplicate_names_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(
        monkeypatch,
        EntryPoint("meter", "dummy_drivers:DummyMeter", G),
        EntryPoint("meter", "dummy_drivers:DummyPsu", G),
    )
    with pytest.raises(DriverError, match="duplicate driver name 'meter'"):
        discover_drivers()


def test_bad_entry_points_skipped_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(
        monkeypatch,
        EntryPoint("good", "dummy_drivers:DummyMeter", G),
        EntryPoint("broken", "broken_driver:Nope", G),
        EntryPoint("missing", "dummy_drivers:DoesNotExist", G),
        EntryPoint("notdriver", "dummy_drivers:NotADriver", G),
    )
    warnings: list[str] = []
    assert discover_drivers(warnings) == dict(good=DummyMeter)
    assert len(warnings) == 3
    assert any("'broken'" in w and "failed to import" in w for w in warnings)
    assert any("'missing'" in w for w in warnings)
    assert any("'notdriver'" in w and "not a Driver subclass" in w for w in warnings)


def test_bad_entry_points_without_warning_list(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, EntryPoint("broken", "broken_driver:Nope", G))
    assert discover_drivers() == {}


def test_get_driver_unknown_lists_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(
        monkeypatch,
        EntryPoint("b", "dummy_drivers:DummyMeter", G),
        EntryPoint("a", "dummy_drivers:DummyPsu", G),
    )
    assert get_driver("a") is DummyPsu
    with pytest.raises(DriverError, match=r"unknown driver 'foo' \(installed: a, b\)"):
        get_driver("foo")
