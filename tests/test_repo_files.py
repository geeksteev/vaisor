"""Checks that the committed example/doc files stay consistent with the code."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vaisor.cli import build_parser
from vaisor.config import load_bench

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = [ROOT / "bench.example.yaml", *sorted((ROOT / "examples/devices").glob("*.yaml"))]


def test_env_example_keys_are_empty() -> None:
    path = ROOT / ".env.example"
    assert path.exists()
    key_lines = [
        line for line in path.read_text().splitlines() if re.match(r"\s*\w*_KEY=", line)
    ]
    assert key_lines
    for line in key_lines:
        assert line.split("=", 1)[1].strip() == "", line


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_loads(path: Path) -> None:
    load_bench(path)


def _subcommands() -> set[str]:
    for action in build_parser()._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            return set(choices)
    return set()


def test_readme_commands_are_real_subcommands() -> None:
    text = (ROOT / "README.md").read_text()
    blocks = re.findall(r"```[^\n]*\n(.*?)```", text, re.S)
    valid = _subcommands()
    parser = build_parser()
    found: list[str] = []
    for block in blocks:
        for line in block.splitlines():
            m = re.match(r"\s*vaisor\s+(\S+)", line)
            if not m:
                continue
            found.append(m.group(1))
            if m.group(1) in valid:
                # the flags used in the README must be accepted too
                parser.parse_args(line.split("#")[0].split()[1:])
    assert {"devices", "read"} <= set(found)
    assert set(found) <= valid, set(found) - valid


def test_gitignore_keeps_secrets_out() -> None:
    lines = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in lines and "bench.yaml" in lines
