"""A reader that goes away is a quiet exit, whatever the output's size: real closed pipes, real processes."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
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
        return subprocess.run(  # noqa: S603 - fixed argv: this interpreter and the package under test
            [sys.executable, "-m", "datamax_dpl", *args],
            stdout=writer,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
            timeout=120,
        )
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
    done = subprocess.run(
        [sys.executable, "-m", "datamax_dpl", "models"],
        capture_output=True,
        env=env,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0
    assert b"dmx-600" in done.stdout
