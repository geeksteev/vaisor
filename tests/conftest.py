from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep default state (audit log, lock) out of the repo checkout."""
    monkeypatch.chdir(tmp_path)
