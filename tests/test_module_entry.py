"""Module entry stories ensuring `python -m` mirrors the CLI."""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamax_dpl import __init__conf__, entry
from datamax_dpl.adapters import cli as cli_mod

if TYPE_CHECKING:
    from collections.abc import Callable

# Ensure subprocess can find the package even without editable install.
_SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")


def _subprocess_env() -> dict[str, str]:
    """Build env dict with src/ on PYTHONPATH for subprocess tests."""
    existing = os.environ.get("PYTHONPATH", "")
    pythonpath = f"{_SRC_DIR}{os.pathsep}{existing}" if existing else _SRC_DIR
    return {**os.environ, "PYTHONPATH": pythonpath}


@pytest.mark.os_agnostic
def test_module_entry_executes_cli_and_shows_help(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """python -m invocation with no args shows help and exits 0."""
    monkeypatch.setattr(sys, "argv", ["datamax_dpl"], raising=False)

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("datamax_dpl.__main__", run_name="__main__")

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "Usage:" in captured.out
    assert __init__conf__.shell_command in captured.out


@pytest.mark.os_agnostic
def test_module_entry_exits_with_the_cli_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    strip_ansi: Callable[[str], str],
) -> None:
    """python -m hands the CLI's own exit code to SystemExit (a usage error is 2).

    The generic crash handler itself is covered through ``main`` in test_cli_core.py, where a
    crashing port can be injected; ``__main__`` only wires the production services into it.
    """
    monkeypatch.setattr(sys, "argv", ["datamax_dpl", "does-not-exist"], raising=False)

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("datamax_dpl.__main__", run_name="__main__")

    plain_err = strip_ansi(capsys.readouterr().err)
    assert exc.value.code == 2
    assert "No such command" in plain_err


@pytest.mark.os_agnostic
def test_module_entry_cli_exports_all_registered_commands() -> None:
    """CLI facade exports all registered commands."""
    expected_commands = {
        "cli_config",
        "cli_config_deploy",
        "cli_config_generate_examples",
        "cli_info",
    }
    exported = {name for name in dir(cli_mod) if name.startswith("cli_")}
    assert exported == expected_commands


@pytest.mark.os_agnostic
def test_module_entry_subprocess_help() -> None:
    """Verify `python -m datamax_dpl --help` works via subprocess.

    This tests the true CLI invocation path that end-users would experience,
    complementing the runpy-based tests that run in-process.
    """
    result = subprocess.run(
        [sys.executable, "-m", "datamax_dpl", "--help"],
        capture_output=True,
        timeout=30,
        check=False,
        encoding="utf-8",
        errors="replace",
        env=_subprocess_env(),
    )
    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert __init__conf__.shell_command in result.stdout


@pytest.mark.os_agnostic
def test_module_entry_subprocess_version() -> None:
    """Verify `python -m datamax_dpl --version` outputs version."""
    result = subprocess.run(
        [sys.executable, "-m", "datamax_dpl", "--version"],
        capture_output=True,
        timeout=30,
        check=False,
        encoding="utf-8",
        errors="replace",
        env=_subprocess_env(),
    )
    assert result.returncode == 0
    assert __init__conf__.version in result.stdout


@pytest.mark.os_agnostic
def test_entry_main_invokes_cli_with_help(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """entry.main() wires production services and invokes CLI.

    This tests the console script entry point used by pip-installed commands
    (datamax_dpl, datamax-dpl).
    """
    monkeypatch.setattr(sys, "argv", ["datamax_dpl", "--help"])

    exit_code = entry.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Usage:" in captured.out
    assert __init__conf__.shell_command in captured.out


@pytest.mark.os_agnostic
def test_entry_main_returns_nonzero_on_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    strip_ansi: Callable[[str], str],
) -> None:
    """entry.main() returns the CLI's non-zero exit code on a CLI error."""
    monkeypatch.setattr(sys, "argv", ["datamax_dpl", "does-not-exist"])

    exit_code = entry.main()

    plain_err = strip_ansi(capsys.readouterr().err)
    assert exit_code == 2
    assert "No such command" in plain_err
