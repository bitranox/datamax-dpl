"""Print a label, ask for status and probe the port, cycle after cycle, logging every event.

Each cycle sends the label with ``datamax-dpl --json-bare print``, asks ``datamax-dpl --json-bare
status`` three times straight after, then opens ``--connects`` bare TCP connections (handshake,
then close, nothing sent) ``--interval`` seconds apart. Every event is one JSON line in ``--log``
(and on stdout)::

    {"t": "2026-10-31T09:12:03.123Z", "elapsed": 1.234, "cycle": 1, "step": "print",
     "rc": 0, "bytes": 812, "answer": {...}, "error": null}

``step`` is ``print``, ``status``, ``connect`` or ``stop``; ``rc`` is the command's exit code (0 or
1 for a connect); ``bytes`` what a print sent; ``answer`` the command's JSON ``data`` (for a
connect, the seconds the handshake took); ``error`` what went wrong, for a connect one of
``refused`` (nothing accepts on the port), ``timeout`` (no answer to the handshake within
``--timeout``), ``reset``, or the exception's name and errno.

A port that refuses or times out on every connect for longer than ``--give-up`` seconds (30) is
taken as wedged: after the cycle's connects the script keeps probing at the same interval until a
connect succeeds again or that time has passed, and then writes a ``stop`` row, says so on stderr
and ends with exit code 3.

The commands run as ``<this Python> -m datamax_dpl``, so the script runs from the environment the
package is installed in, and the configuration (``[printer] model`` and the rest) is the one that
command reads; ``--model`` passes a model on.

Usage::

    .venv/bin/python scripts/status_cycles.py --printer tcp://192.0.2.60:9100 \\
        --label examples/hello.json --cycles 5 --log cycles.jsonl --model allegro-2
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast

from datamax_dpl.adapters.transport.uri import TcpUri, parse_printer_uri

if TYPE_CHECKING:
    from typing import TextIO

__all__ = [
    "ALL_DONE",
    "BAD_ARGUMENTS",
    "SOME_FAILED",
    "WEDGED",
    "connect_error",
    "main",
]

#: Exit code when every cycle ran and every step succeeded.
ALL_DONE = 0
#: Exit code when every cycle ran but a step failed (its row says how).
SOME_FAILED = 1
#: Exit code for a printer that is not a ``tcp://`` URI.
BAD_ARGUMENTS = 2
#: Exit code when the port refused or timed out for longer than ``--give-up``.
WEDGED = 3

_STATUS_ASKS = 3
# Longest a single datamax-dpl command may take before it counts as hung; the CLI's own timeout
# is far shorter, so this only ends a command that stopped answering altogether.
_COMMAND_SECONDS = 300.0


def connect_error(error: OSError) -> str:
    """Name a failed connect: ``refused``, ``timeout``, ``reset`` or the exception and its errno.

    Example:
        >>> connect_error(ConnectionRefusedError(111, "Connection refused"))
        'refused'
        >>> connect_error(TimeoutError("timed out"))
        'timeout'
        >>> connect_error(OSError(113, "No route to host"))
        'OSError errno 113'
    """
    if isinstance(error, ConnectionRefusedError):
        return "refused"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ConnectionResetError):
        return "reset"
    return f"{type(error).__name__} errno {error.errno}"


@dataclass
class _Log:
    """Writes one JSON line per event to the log and to stdout, and remembers whether one failed."""

    stream: TextIO
    started: float = field(default_factory=time.monotonic)
    failed: bool = False

    def event(self, *, cycle: int, step: str, rc: int | None, **fields: object) -> None:
        now = datetime.now(timezone.utc)
        row = {
            "t": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
            "elapsed": round(time.monotonic() - self.started, 3),
            "cycle": cycle,
            "step": step,
            "rc": rc,
            "bytes": fields.get("bytes"),
            "answer": fields.get("answer"),
            "error": fields.get("error"),
        }
        line = json.dumps(row)
        self.stream.write(line + "\n")
        self.stream.flush()
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        self.failed = self.failed or (rc not in (0, None))


def _envelope(stdout: str) -> dict[str, object] | None:
    """The JSON envelope a ``--json-bare`` command printed last, or None when there is none."""
    for line in reversed(stdout.splitlines()):
        try:
            parsed: object = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return cast("dict[str, object]", parsed)
    return None


def _run_command(args: argparse.Namespace, words: list[str], *, cycle: int, step: str, log: _Log) -> None:
    """Run one ``datamax-dpl --json-bare`` command and log its exit code, envelope and error."""
    argv = [sys.executable, "-m", "datamax_dpl", "--json-bare", *words, "--printer", args.printer]
    # The envelope is JSON; UTF-8 keeps a non-ASCII message intact on a Windows console code page.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        done = subprocess.run(  # noqa: S603 - argv is this interpreter and the script's own arguments
            argv,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=_COMMAND_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.event(cycle=cycle, step=step, rc=None, error=f"no exit within {_COMMAND_SECONDS:g} s")
        log.failed = True
        return
    envelope = _envelope(done.stdout) or {}
    raw = envelope.get("data")
    data = cast("dict[str, object]", raw) if isinstance(raw, dict) else None
    sent = data.get("bytes_sent") if data is not None else None
    error = None
    if done.returncode != 0:
        message = envelope.get("message")
        error = str(message) if message else (done.stderr.strip().splitlines() or ["no output"])[-1]
    log.event(cycle=cycle, step=step, rc=done.returncode, bytes=sent, answer=data, error=error)


def _connect(target: TcpUri, *, timeout: float) -> tuple[float, str | None]:
    """Open and close one TCP connection; return the seconds it took and what failed, if anything."""
    started = time.monotonic()
    try:
        with socket.create_connection((target.host, target.port), timeout=timeout):
            pass
    except OSError as error:
        return time.monotonic() - started, connect_error(error)
    return time.monotonic() - started, None


@dataclass
class _Probe:
    """Bare connects at a fixed interval, and how long the port has refused them without a break."""

    target: TcpUri
    args: argparse.Namespace
    log: _Log
    next_at: float = 0.0
    failing_since: float | None = None

    def once(self, cycle: int) -> bool:
        """Wait for the next slot, connect once and log it; return False when the port counts as wedged."""
        time.sleep(max(0.0, self.next_at - time.monotonic()))
        started = time.monotonic()
        self.next_at = started + self.args.interval
        seconds, error = _connect(self.target, timeout=self.args.timeout)
        self.log.event(
            cycle=cycle,
            step="connect",
            rc=0 if error is None else 1,
            answer={"seconds": round(seconds, 3)},
            error=error,
        )
        if error is None:
            self.failing_since = None
            return True
        self.failing_since = started if self.failing_since is None else self.failing_since
        return time.monotonic() - self.failing_since <= self.args.give_up

    def cycle(self, cycle: int) -> bool:
        """Run the cycle's connects, then keep probing while the port fails; False when it is wedged."""
        self.next_at = time.monotonic()
        for _ in range(self.args.connects):
            if not self.once(cycle):
                return False
        while self.failing_since is not None:
            if not self.once(cycle):
                return False
        return True


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print, ask for status and probe the port, cycle after cycle.")
    parser.add_argument("--printer", required=True, help="printer URI, tcp://HOST:PORT")
    parser.add_argument("--label", type=Path, required=True, help="label JSON file to print each cycle")
    parser.add_argument("--cycles", type=int, default=1, help="number of cycles (default: 1)")
    parser.add_argument("--log", type=Path, required=True, help="JSON lines file the events are appended to")
    parser.add_argument("--model", default=None, help="model passed to print (default: the configured one)")
    parser.add_argument("--connects", type=int, default=10, help="bare connects per cycle (default: 10)")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between connects (default: 1)")
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds a connect may take (default: 5)")
    parser.add_argument("--give-up", type=float, default=30.0, help="seconds of failing connects to stop at (30)")
    return parser.parse_args(argv)


def _tcp_target(printer: str) -> TcpUri | str:
    """The TCP address of ``printer``, or why it has none."""
    try:
        uri = parse_printer_uri(printer)
    except ValueError as error:
        return str(error)
    if not isinstance(uri, TcpUri):
        return f"--printer must be a tcp:// URI for the bare connects, got {printer}"
    return uri


def main(argv: list[str] | None = None) -> int:
    """Run the cycles and log every event.

    Returns:
        0 when every step of every cycle succeeded, 1 when one failed, 2 for a printer that is not
        a ``tcp://`` URI, 3 when the port refused or timed out for longer than ``--give-up``.
    """
    args = _parse(argv)
    target = _tcp_target(args.printer)
    if isinstance(target, str):
        sys.stderr.write(f"error: {target}\n")
        return BAD_ARGUMENTS
    model = ["--model", args.model] if args.model else []
    with args.log.open("a", encoding="utf-8") as stream:
        log = _Log(stream)
        probe = _Probe(target=target, args=args, log=log)
        for cycle in range(1, args.cycles + 1):
            _run_command(args, ["print", str(args.label), *model], cycle=cycle, step="print", log=log)
            for _ in range(_STATUS_ASKS):
                _run_command(args, ["status"], cycle=cycle, step="status", log=log)
            if not probe.cycle(cycle):
                since = time.monotonic() - (probe.failing_since or 0.0)
                message = f"{target.target} did not accept a connection for {since:.1f} s; the port looks wedged"
                log.event(cycle=cycle, step="stop", rc=WEDGED, error=message)
                sys.stderr.write(f"stopped: {message}\n")
                return WEDGED
    return SOME_FAILED if log.failed else ALL_DONE


if __name__ == "__main__":
    raise SystemExit(main())
