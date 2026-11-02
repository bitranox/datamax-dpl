"""Run the package's CLI in a process of its own: the one place the suite starts one.

A test that needs the real process (a broken pipe, a file size limit, the configuration layers the
process reads, a signal) starts it here, so the subprocess call and its one security note live in
a single place. Imported by bare name: ``pythonpath`` in pyproject puts ``tests/`` on ``sys.path``.

The argv is always this interpreter, an entry into this package (``-m datamax_dpl``, or ``-c``
with a script the test wrote) and the test's own arguments; no shell is involved.
"""

from __future__ import annotations

import subprocess
import sys
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["MODULE", "run_cli", "run_cli_text", "start_cli"]

#: The entry ``python -m`` takes for the package's CLI.
MODULE: tuple[str, ...] = ("-m", "datamax_dpl")
#: Seconds a run may take before it is killed: far past any command the suite runs.
_TIMEOUT = 120.0

_Stream = int | IO[bytes] | None


def start_cli(
    args: Sequence[str],
    *,
    entry: Sequence[str] = MODULE,
    stdout: _Stream = subprocess.PIPE,
    stderr: _Stream = subprocess.PIPE,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    pass_fds: Sequence[int] = (),
) -> subprocess.Popen[bytes]:
    """Start the CLI with ``args``, its streams as given; the caller waits for it (a ``with`` block does).

    Args:
        args: The command line after the entry.
        entry: What this interpreter runs: the package as a module, or ``("-c", script)``.
        stdout: Where its stdout goes: a pipe by default, or a descriptor the test holds.
        stderr: Where its stderr goes: a pipe by default.
        env: Its whole environment; None inherits this process's.
        cwd: Its working directory; None inherits this one's.
        pass_fds: Descriptors it inherits beyond the standard three.
    """
    # The one subprocess start of the suite's CLI runs: no shell, and every argv word is this
    # interpreter, the package's own entry or a word the test itself wrote.
    return subprocess.Popen(  # noqa: S603 - argv is this interpreter, this package's entry and test-written words
        [sys.executable, *entry, *args],
        stdout=stdout,
        stderr=stderr,
        env=None if env is None else dict(env),
        cwd=cwd,
        pass_fds=tuple(pass_fds),
    )


def run_cli(
    args: Sequence[str],
    *,
    entry: Sequence[str] = MODULE,
    stdout: _Stream = subprocess.PIPE,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    pass_fds: Sequence[int] = (),
    timeout: float = _TIMEOUT,
) -> subprocess.CompletedProcess[bytes]:
    """Run the CLI to its end and return what it wrote, as bytes, as ``subprocess.run`` would.

    A run past ``timeout`` is killed and raises ``subprocess.TimeoutExpired``.
    """
    with start_cli(args, entry=entry, stdout=stdout, env=env, cwd=cwd, pass_fds=pass_fds) as running:
        try:
            out, err = running.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            running.kill()
            running.communicate()
            raise
    return subprocess.CompletedProcess(running.args, running.returncode, out, err)


def run_cli_text(
    args: Sequence[str],
    *,
    entry: Sequence[str] = MODULE,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    timeout: float = _TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run the CLI as :func:`run_cli` does and return its output as text.

    Decoded as UTF-8 with undecodable bytes replaced, and with ``\\r\\n`` and ``\\r`` read as ``\\n``,
    which is what text mode makes of a Windows child's line endings.
    """
    done = run_cli(args, entry=entry, env=env, cwd=cwd, timeout=timeout)
    return subprocess.CompletedProcess(done.args, done.returncode, _text(done.stdout), _text(done.stderr))


def _text(data: bytes | None) -> str:
    """``data`` as text mode reads it: UTF-8, every line ending a ``\\n``."""
    if data is None:
        return ""
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
