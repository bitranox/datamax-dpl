"""A reader that goes away is a quiet exit, whatever the output's size: real closed pipes, real processes."""

from __future__ import annotations

import os
import socket
from typing import TYPE_CHECKING

import pytest
from cli_process import run_cli, start_cli

if TYPE_CHECKING:
    import subprocess
    from pathlib import Path

#: Exit code of a process killed by SIGPIPE, which shells and pipelines read as "the reader left".
BROKEN_PIPE_EXIT = 141

#: What stays quiet: anything saying more than the printed output's fate is noise after ``| head``.
_NOISE = ("SystemExit", "Exception ignored", "BrokenPipeError", "Traceback")


def _run_with_closed_stdout(args: list[str], *, tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    """Run the CLI with its stdout a pipe whose reader closed before it wrote a byte."""
    reader, writer = os.pipe()
    os.close(reader)
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path), "HOME": str(tmp_path)}
    try:
        return run_cli(args, stdout=writer, env=env)
    finally:
        os.close(writer)


LARGE_AND_SMALL = [
    pytest.param(["schema"], id="large"),
    pytest.param(["--json", "schema"], id="large-envelope"),
    pytest.param(["--json-bare", "schema"], id="large-bare"),
    pytest.param(["config"], id="config"),
    pytest.param(["models"], id="small"),
    pytest.param(["--json", "models"], id="small-envelope"),
    pytest.param(["--version"], id="version"),
    pytest.param(["info"], id="info"),
    pytest.param(["--help"], id="help"),
]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("args", LARGE_AND_SMALL)
def test_a_reader_gone_before_the_first_byte_is_a_quiet_exit(args: list[str], tmp_path: Path) -> None:
    done = _run_with_closed_stdout(args, tmp_path=tmp_path)
    stderr = done.stderr.decode(errors="replace")
    assert done.returncode == BROKEN_PIPE_EXIT, stderr
    assert not [word for word in _NOISE if word in stderr], stderr


@pytest.mark.os_agnostic
def test_a_usage_error_with_the_reader_gone_still_reports_on_stderr(tmp_path: Path) -> None:
    """The usage text goes to stderr, which still has a reader: only stdout's envelope is lost."""
    done = _run_with_closed_stdout(["--json", "validate", "--bogus"], tmp_path=tmp_path)
    stderr = done.stderr.decode(errors="replace")
    assert done.returncode == 2, stderr
    assert "--bogus" in stderr
    assert not [word for word in _NOISE if word in stderr], stderr


@pytest.mark.os_agnostic
def test_a_reader_that_stays_gets_the_output_and_the_command_s_own_exit_code(tmp_path: Path) -> None:
    """The control: nothing above may hide a normal run."""
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path), "HOME": str(tmp_path)}
    done = run_cli(["models"], env=env)
    assert done.returncode == 0
    assert b"dmx-600" in done.stdout


def _label_and_data(tmp_path: Path, *, rows: int = 2) -> tuple[str, str]:
    label = tmp_path / "label.json"
    label.write_text('{"elements": [{"type": "text", "x": 1, "y": 1, "text": "{a}"}]}', encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_bytes(b"a\n" + b"".join(b"r%d\n" % number for number in range(rows)))
    return str(label), str(data)


#: Both commands whose job owns stdout, single and over ``--data``.
_JOB_ON_STDOUT = [
    pytest.param(("render", "{label}", "--model", "dmx-600", "-o", "-"), id="render-single"),
    pytest.param(("render", "{label}", "--data", "{data}", "--model", "dmx-600", "-o", "-"), id="render-batch"),
    pytest.param(("print", "{label}", "--model", "dmx-600", "--printer", "-"), id="print-single"),
    pytest.param(("print", "{label}", "--data", "{data}", "--model", "dmx-600", "--printer", "-"), id="print-batch"),
]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", _JOB_ON_STDOUT)
def test_a_job_on_stdout_whose_reader_left_first_is_a_quiet_exit(command: tuple[str, ...], tmp_path: Path) -> None:
    label, data = _label_and_data(tmp_path)
    done = _run_with_closed_stdout([word.format(label=label, data=data) for word in command], tmp_path=tmp_path)
    stderr = done.stderr.decode(errors="replace")
    assert done.returncode == BROKEN_PIPE_EXIT, stderr
    assert not [word for word in (*_NOISE, "Broken pipe", "Error:") if word in stderr], stderr


#: Enough records that the job is far past what a pipe holds, so the writer is mid-stream when it breaks.
_ROWS_PAST_A_PIPE = 5000
_READ_BEFORE_LEAVING = 10


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", _JOB_ON_STDOUT[1::2])
def test_a_reader_that_leaves_mid_job_is_a_quiet_exit(command: tuple[str, ...], tmp_path: Path) -> None:
    """The route a batch normally takes: ``... -o - | head -c 10``."""
    label, data = _label_and_data(tmp_path, rows=_ROWS_PAST_A_PIPE)
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path), "HOME": str(tmp_path)}
    args = [word.format(label=label, data=data) for word in command]
    with start_cli(args, env=env) as running:
        assert running.stdout is not None
        assert running.stderr is not None
        assert len(running.stdout.read(_READ_BEFORE_LEAVING)) == _READ_BEFORE_LEAVING
        running.stdout.close()
        stderr = running.stderr.read().decode(errors="replace")
        code = running.wait(timeout=120)
    assert code == BROKEN_PIPE_EXIT, stderr
    assert not [word for word in (*_NOISE, "Broken pipe", "Error:") if word in stderr], stderr


@pytest.mark.os_agnostic
def test_a_printer_that_cannot_be_reached_is_not_a_reader_that_left(tmp_path: Path) -> None:
    """The control: a transport failure with stdout not holding a job says so with exit 2."""
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.close()
    label, _ = _label_and_data(tmp_path)
    done = run_cli(
        ["print", label, "--model", "dmx-600", "--printer", f"tcp://127.0.0.1:{port}"],
        env={**os.environ, "XDG_CONFIG_HOME": str(tmp_path), "HOME": str(tmp_path)},
    )
    assert done.returncode == 2, done.stderr
