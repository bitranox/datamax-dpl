"""How ``verify`` waits for the printer to finish: ``status_wait`` in all, each poll within the timeout.

The use case runs against a channel whose answer follows a clock that moves only when the code
under test sleeps or a poll waits, so a printer busy for exactly 3 s is staged without real time;
the CLI tests swap the same channel and clock into the testing composition.
"""

from __future__ import annotations

import dataclasses
import json
import math
from typing import TYPE_CHECKING, Any

import pytest

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory import FakeCamera, RecordingPrinter
from datamax_dpl.application.verify import IdleWait, wait_idle
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.errors import PrinterTimeoutError
from datamax_dpl.domain.timing import DEFAULT_TRANSPORT_TUNING, MAX_ANSWER_SECONDS, TransportTuning

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

    from datamax_dpl.application.ports import PrinterTransport
    from datamax_dpl.composition import AppServices

IDLE = b"NNNNNNNN\r"
BUSY = b"NNNNYNNN\r"
PRINTER = "tcp://192.0.2.60:9100"
WAIT_LONGER = "wait longer with [printer] status_wait or --timeout"
LABEL = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "Hello"}]}


class Clock:
    """Monotonic seconds that move only when slept on; it records every sleep."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float, /) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class BusyPrinter:
    """A channel that answers busy until ``busy_for`` seconds on ``clock``, then idle.

    Each poll is answered at once; the answer wait it was given is recorded in :attr:`waits`.
    """

    def __init__(self, clock: Clock, *, busy_for: float) -> None:
        self._clock = clock
        self._busy_for = busy_for
        self.waits: list[float] = []
        self.sent: list[bytes] = []

    @property
    def bidirectional(self) -> bool:
        return True

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def query(
        self,
        request: bytes,
        *,
        timeout: float,
        terminator: bytes = b"\r",
        lines: int = 1,
        accept: Callable[[bytes], bool] | None = None,
    ) -> bytes:
        del request, terminator, lines, accept
        self.waits.append(timeout)
        return BUSY if self._clock.now < self._busy_for else IDLE

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = MAX_ANSWER_SECONDS) -> bytes:
        raise AssertionError("verify reads no idle-terminated answer")

    def close(self) -> None:
        return None


@pytest.mark.os_agnostic
def test_a_printer_busy_for_3_s_is_waited_for_within_a_5_s_wait_though_the_timeout_is_1_s() -> None:
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=3.0)

    status = wait_idle(printer, timeout=1.0, wait=IdleWait(seconds=5.0), sleep=clock.sleep, clock=clock)

    assert status.idle
    assert clock.now == 3.0
    assert max(printer.waits) == 1.0


@pytest.mark.os_agnostic
def test_a_printer_busy_past_the_wait_fails_naming_the_seconds_waited_and_the_setting() -> None:
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=math.inf)
    wait = IdleWait(seconds=5.0, wait_longer=WAIT_LONGER)

    with pytest.raises(PrinterTimeoutError) as caught:
        wait_idle(printer, timeout=1.0, wait=wait, sleep=clock.sleep, clock=clock)

    assert str(caught.value) == f"printer still busy after 5.0 s; {WAIT_LONGER}"


@pytest.mark.os_agnostic
def test_a_poll_never_waits_past_the_end_of_the_whole_wait() -> None:
    # Polls at 0, 0.5, 1.0, 1.5 and 2.0 s of a 2.2 s wait: the last two have 0.7 and 0.2 s left,
    # less than the 1 s timeout, and get only that.
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=math.inf)

    with pytest.raises(PrinterTimeoutError):
        wait_idle(printer, timeout=1.0, wait=IdleWait(seconds=2.2), sleep=clock.sleep, clock=clock)

    assert printer.waits == pytest.approx([1.0, 1.0, 1.0, 0.7, 0.2])


@pytest.mark.os_agnostic
def test_the_polls_are_spaced_by_the_poll_interval() -> None:
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=1.0)

    wait_idle(printer, timeout=1.0, wait=IdleWait(seconds=5.0, poll_interval=0.25), sleep=clock.sleep, clock=clock)

    assert clock.sleeps == [0.25, 0.25, 0.25, 0.25]


def _services(clock: Clock, printer: PrinterTransport) -> Callable[[], AppServices]:
    """The testing composition with ``printer`` behind every URI and ``clock`` for time."""

    def open_printer(
        uri: str, *, timeout: float, tuning: TransportTuning = DEFAULT_TRANSPORT_TUNING
    ) -> PrinterTransport:
        del uri, timeout, tuning
        return printer

    base = build_testing(camera=FakeCamera())
    services = dataclasses.replace(base, open_printer=open_printer, sleep=clock.sleep, clock=clock)
    return lambda: services


def _verify(cli_runner: CliRunner, tmp_path: Path, services: Callable[[], AppServices], *extra: str) -> dict[str, Any]:
    label = tmp_path / "label.json"
    label.write_text(json.dumps(LABEL), encoding="utf-8")
    settings = ["--set", "camera.kind=file", *extra]
    args = ["--json", *settings, "verify", str(label), "--printer", PRINTER, "--output-dir", str(tmp_path)]
    result = cli_runner.invoke(cli_mod.cli, args, obj=services)
    envelope: dict[str, Any] = json.loads(result.stdout)
    return {**envelope, "exit_code": result.exit_code}


@pytest.mark.os_agnostic
def test_verify_waits_status_wait_for_a_printer_busy_longer_than_the_timeout(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=3.0)
    settings = ["--set", "printer.timeout=1", "--set", "printer.status_wait=5"]

    envelope = _verify(cli_runner, tmp_path, _services(clock, printer), *settings)

    assert envelope["exit_code"] == 0, envelope
    assert max(printer.waits) == 1.0


@pytest.mark.os_agnostic
def test_verify_on_a_printer_busy_past_status_wait_names_the_seconds_and_the_setting(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=math.inf)

    envelope = _verify(cli_runner, tmp_path, _services(clock, printer), "--set", "printer.status_wait=5")

    assert envelope["exit_code"] == 2, envelope
    assert envelope["message"] == f"printer still busy after 5.0 s; {WAIT_LONGER}"


@pytest.mark.os_agnostic
def test_a_timeout_given_on_the_command_line_is_the_whole_verify_wait(cli_runner: CliRunner, tmp_path: Path) -> None:
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=math.inf)
    label = tmp_path / "label.json"
    label.write_text(json.dumps(LABEL), encoding="utf-8")
    args = ["--json", "--set", "camera.kind=file", "--set", "printer.status_wait=30"]
    args += ["verify", str(label), "--printer", PRINTER, "--timeout", "2", "--output-dir", str(tmp_path)]

    result = cli_runner.invoke(cli_mod.cli, args, obj=_services(clock, printer))

    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["message"] == f"printer still busy after 2.0 s; {WAIT_LONGER}"


@pytest.mark.os_agnostic
def test_verify_polls_at_the_configured_interval(cli_runner: CliRunner, tmp_path: Path) -> None:
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=1.0)

    envelope = _verify(cli_runner, tmp_path, _services(clock, printer), "--set", "printer.verify_poll_interval=0.25")

    assert envelope["exit_code"] == 0, envelope
    # Four polls apart, then the camera's own settle_seconds (1.0) before the photo.
    assert clock.sleeps == [0.25, 0.25, 0.25, 0.25, 1.0]


@pytest.mark.os_agnostic
def test_verify_on_a_one_way_channel_waits_the_configured_settle(cli_runner: CliRunner, tmp_path: Path) -> None:
    clock = Clock()
    one_way = RecordingPrinter(bidirectional=False)

    envelope = _verify(cli_runner, tmp_path, _services(clock, one_way), "--set", "printer.one_way_settle=2.5")

    assert envelope["exit_code"] == 0, envelope
    assert clock.sleeps == [2.5, 1.0]


class SilentPrinter(BusyPrinter):
    """A channel whose every poll waits out its answer wait and gets nothing."""

    def query(
        self,
        request: bytes,
        *,
        timeout: float,
        terminator: bytes = b"\r",
        lines: int = 1,
        accept: Callable[[bytes], bool] | None = None,
    ) -> bytes:
        del request, terminator, lines, accept
        self.waits.append(timeout)
        self._clock.now += timeout
        raise PrinterTimeoutError(f"silent: no complete answer within {timeout:g} s (0 bytes read)")


@pytest.mark.os_agnostic
def test_a_poll_that_gets_no_answer_ends_the_wait_without_asking_again() -> None:
    """An answer may still be owed on the connection, so a second request is never sent after it."""
    clock = Clock()
    printer = SilentPrinter(clock, busy_for=math.inf)

    with pytest.raises(PrinterTimeoutError, match=r"^silent: no complete answer within 1 s \(0 bytes read\)$"):
        wait_idle(printer, timeout=1.0, wait=IdleWait(seconds=5.0), sleep=clock.sleep, clock=clock)

    assert printer.waits == [1.0]
