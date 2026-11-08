import pytest

from vaisor import __version__
from vaisor.cli import main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_args_returns_zero() -> None:
    assert main([]) == 0
