"""The printer URI ``-``: the job owns standard output, in machine mode it is refused like ``render -o -``."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.composition import build_production

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner, Result

#: A label every model takes; its job starts with the system commands and ends with the format.
_LABEL = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "HELLO"}]}
_SINGLE_COMMANDS = ("feed", "cut", "pause", "cancel", "reset")


def _label(tmp_path: Path) -> str:
    path = tmp_path / "label.json"
    path.write_text(json.dumps(_LABEL), encoding="utf-8")
    return str(path)


def _command(name: str, tmp_path: Path) -> list[str]:
    """The command line of one sending command, with printer ``-`` and what it needs to run."""
    if name == "print":
        return ["print", _label(tmp_path), "--printer", "-"]
    if name == "verify":
        return ["--set", "camera.kind=file", "verify", _label(tmp_path), "--printer", "-", "--output-dir", str(tmp_path)]
    if name == "calibrate print":
        return ["calibrate", "print", "--width", "50", "--height", "50", "--axis", "across", "--printer", "-"]
    return [name, "--printer", "-"]


def _invoke(cli_runner: CliRunner, args: list[str]) -> Result:
    return cli_runner.invoke(cli_mod.cli, args, obj=build_production)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("flag", ["--json", "--json-bare"])
@pytest.mark.parametrize("name", ["print", "verify", "calibrate print", *_SINGLE_COMMANDS])
def test_machine_mode_refuses_a_job_on_stdout(cli_runner: CliRunner, tmp_path: Path, name: str, flag: str) -> None:
    """Stdout parses as the one envelope, and not a byte of the job goes out."""
    result = _invoke(cli_runner, [flag, *_command(name, tmp_path)])
    assert result.exit_code == 2, result.output
    document = json.loads(result.stdout)
    assert document["ok"] is False
    assert document["error"] == "ConfigurationError"
    assert "stdout carries the envelope" in document["message"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["print", "calibrate print", *_SINGLE_COMMANDS])
def test_human_mode_keeps_stdout_to_the_job(cli_runner: CliRunner, tmp_path: Path, name: str) -> None:
    """A pipe to a printer or a file gets the job alone; what was done is said on stderr."""
    result = _invoke(cli_runner, _command(name, tmp_path))
    assert result.exit_code == 0, result.output
    assert result.stdout_bytes
    assert b"sent" not in result.stdout_bytes
    assert b" to -" not in result.stdout_bytes
    assert "sent" in result.stderr


@pytest.mark.os_agnostic
def test_a_refused_label_with_a_job_on_stdout_leaves_stdout_empty(cli_runner: CliRunner, tmp_path: Path) -> None:
    """``print ... --printer - > job.dpl`` must not leave the refusal text as the job."""
    path = tmp_path / "wide.json"
    path.write_text(json.dumps({"elements": [{"type": "text", "x": 9000, "y": 5, "text": "A"}]}), encoding="utf-8")
    result = _invoke(cli_runner, ["print", str(path), "--model", "dmx-600", "--printer", "-"])
    assert result.exit_code == 1, result.output
    assert result.stdout_bytes == b""
    assert "REFUSED" in result.stderr


@pytest.mark.os_agnostic
@pytest.mark.parametrize("flag", ["--json", "--json-bare"])
def test_a_label_command_still_takes_a_configured_stdout_printer(
    cli_runner: CliRunner, tmp_path: Path, flag: str
) -> None:
    """``render`` sends nothing: a ``[printer] uri = "-"`` does not stop it answering in JSON."""
    result = _invoke(cli_runner, [flag, "--set", "printer.uri=-", "validate", _label(tmp_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["ok"] is True


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", ["print", "status", "detect", "memory", "feed", "calibrate print"])
def test_a_malformed_uri_is_a_configuration_error_on_every_command(
    cli_runner: CliRunner, tmp_path: Path, command: str
) -> None:
    """One fault, one name: the library raises the same type for the same URI."""
    words = {"print": ["print", _label(tmp_path)], "calibrate print": ["calibrate", "print", "--width", "50", "--height", "50"]}.get(command, [command])
    result = _invoke(cli_runner, ["--json-bare", *words, "--printer", "tcp://a b"])
    assert result.exit_code == 2, result.output
    document = json.loads(result.stdout)
    assert document["error"] == "ConfigurationError"
    assert "accepted forms" in document["message"]


@pytest.mark.os_agnostic
def test_preview_refuses_dash_as_a_file_name(cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``render -o -`` means stdout, so a file literally named ``-`` here would surprise."""
    monkeypatch.chdir(tmp_path)
    result = _invoke(cli_runner, ["--json-bare", "preview", _label(tmp_path), "-o", "-"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["error"] == "LabelInputError"
    assert not (tmp_path / "-").exists()
