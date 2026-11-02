"""How ``verify`` waits for the printer to finish: exactly as ``status`` waits, polled while busy.

The use case runs against channels whose answers follow a clock that moves only when the code
under test sleeps or an answer wait runs, so a printer busy or silent for exactly 3 s is staged
without real time; the CLI tests swap the same channels and clock into the testing composition.
"""

from __future__ import annotations

import dataclasses
import json
import math
from typing import TYPE_CHECKING, Any

import pytest

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory import FakeCamera, RecordingPrinter, open_recording_printer
from datamax_dpl.application.use_cases import BUSY_HINT, AnswerWait, query_status
from datamax_dpl.application.verify import IdleWait, verify_print, wait_idle
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.camera import CameraKind, CameraSettings
from datamax_dpl.domain.errors import CameraError, PrinterTimeoutError, TransportError
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.render import render
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.timing import DEFAULT_TRANSPORT_TUNING, MAX_ANSWER_SECONDS, TransportTuning

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

    from datamax_dpl.application.ports import PrinterTransport
    from datamax_dpl.composition import AppServices
    from datamax_dpl.domain.status import PrinterStatus

IDLE = b"NNNNNNNN\r"
BUSY = b"NNNNYNNN\r"
PRINTER = "tcp://192.0.2.60:9100"
WAIT_LONGER = "wait longer with [printer] status_wait or --timeout"
LABEL = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "Hello"}]}


class Clock:
    """Monotonic seconds that move only when slept on or waited out; it records every sleep."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float, /) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class ScriptedPrinter:
    """A channel answering each status poll from :meth:`answer`; the answer wait each poll got is in :attr:`waits`."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.waits: list[float] = []
        self.closed = False

    @property
    def bidirectional(self) -> bool:
        return True

    def send(self, data: bytes) -> None:
        del data

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
        return self.answer(timeout)

    def answer(self, timeout: float) -> bytes:
        raise NotImplementedError

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = MAX_ANSWER_SECONDS) -> bytes:
        raise AssertionError("a status wait reads no idle-terminated answer")

    def peer_closed(self) -> bool:
        return False

    def close(self) -> None:
        self.closed = True


class BusyPrinter(ScriptedPrinter):
    """Answers at once: busy until ``busy_for`` seconds, then idle."""

    def __init__(self, clock: Clock, *, busy_for: float) -> None:
        super().__init__(clock)
        self._busy_for = busy_for

    def answer(self, timeout: float) -> bytes:
        return BUSY if self.clock.now < self._busy_for else IDLE


class SilentPrinter(ScriptedPrinter):
    """Says nothing until ``silent_for`` seconds, then answers idle; a wait ending sooner runs out in full."""

    def __init__(self, clock: Clock, *, silent_for: float) -> None:
        super().__init__(clock)
        self._silent_for = silent_for

    def answer(self, timeout: float) -> bytes:
        if self.clock.now + timeout >= self._silent_for:
            self.clock.now = max(self.clock.now, self._silent_for)
            return IDLE
        self.clock.now += timeout
        raise PrinterTimeoutError(f"silent: no complete answer within {timeout:g} s (0 bytes read)")


class HungUpPrinter(ScriptedPrinter):
    """A print server that closed the job's connection: the poll goes nowhere and takes no request."""

    def answer(self, timeout: float) -> bytes:
        raise TransportError("hung up: the printer closed the connection (0 bytes read)", unanswered=True)


def _opener(*channels: PrinterTransport) -> Callable[..., PrinterTransport]:
    """An opener handing out ``channels`` in turn, as connections to the same printer."""
    later = iter(channels)

    def open_printer(uri: str, *, timeout: float) -> PrinterTransport:
        del uri, timeout
        return next(later)

    return open_printer


def _never_opens(uri: str, *, timeout: float) -> PrinterTransport:
    raise AssertionError("no second connection is opened while the first can still answer")


def _wait(clock: Clock, *, seconds: float = 5.0, poll_interval: float = 0.5) -> IdleWait:
    answer = AnswerWait(seconds=seconds, wait_longer=WAIT_LONGER, clock=clock, sleep=clock.sleep)
    return IdleWait(answer=answer, poll_interval=poll_interval)


def _idle(printer: PrinterTransport, clock: Clock, **wait: float) -> PrinterStatus:
    return wait_idle(printer, uri=PRINTER, timeout=1.0, open_printer=_never_opens, wait=_wait(clock, **wait))


@pytest.mark.os_agnostic
def test_a_printer_silent_for_3_s_passes_a_5_s_wait_though_the_timeout_is_1_s() -> None:
    """The one poll is read until the wait ends, as status reads its request, never asked again."""
    clock = Clock()
    printer = SilentPrinter(clock, silent_for=3.0)

    status = _idle(printer, clock)

    assert status.idle
    assert (clock.now, printer.waits) == (3.0, [5.0])


@pytest.mark.os_agnostic
def test_a_printer_silent_past_the_wait_fails_as_status_does_naming_the_seconds_and_the_setting() -> None:
    clock = Clock()
    printer = SilentPrinter(clock, silent_for=math.inf)

    with pytest.raises(PrinterTimeoutError) as caught:
        _idle(printer, clock)

    assert str(caught.value) == (
        f"{PRINTER}: no answer in 5.0 s (1 attempt); {BUSY_HINT}; {WAIT_LONGER}. "
        "Last failure: silent: no complete answer within 5 s (0 bytes read)"
    )
    assert printer.waits == [5.0]


@pytest.mark.os_agnostic
def test_a_printer_busy_for_3_s_is_polled_until_idle_within_a_5_s_wait() -> None:
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=3.0)

    status = _idle(printer, clock)

    assert status.idle
    assert clock.now == 3.0


@pytest.mark.os_agnostic
def test_a_printer_busy_past_the_wait_fails_naming_the_seconds_waited_and_the_setting() -> None:
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=math.inf)

    with pytest.raises(PrinterTimeoutError) as caught:
        _idle(printer, clock)

    assert str(caught.value) == f"printer still busy after 5.0 s; {WAIT_LONGER}"


@pytest.mark.os_agnostic
def test_each_poll_waits_for_its_answer_as_long_as_the_whole_wait_has_left() -> None:
    # Polls at 0, 0.5, 1.0, 1.5 and 2.0 s of a 2.2 s wait, as status asks with the time left.
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=math.inf)

    with pytest.raises(PrinterTimeoutError):
        _idle(printer, clock, seconds=2.2)

    assert printer.waits == pytest.approx([2.2, 1.7, 1.2, 0.7, 0.2])


class BusyThenSilentPrinter(SilentPrinter):
    """Answers busy at once until ``busy_for`` seconds, then says nothing for good."""

    def __init__(self, clock: Clock, *, busy_for: float) -> None:
        super().__init__(clock, silent_for=math.inf)
        self._busy_for = busy_for

    def answer(self, timeout: float) -> bytes:
        return BUSY if self.clock.now < self._busy_for else super().answer(timeout)


@pytest.mark.os_agnostic
def test_a_printer_falling_silent_after_busy_polls_ends_the_one_wait_begun_at_the_first_poll() -> None:
    """The polls share one wait: the silent poll gets only what the busy ones left of it.

    The silent poll is read as ``status`` reads one, but the printer was last heard busy, so that
    is what the message says; the poll's own failure is chained to it.
    """
    clock = Clock()
    printer = BusyThenSilentPrinter(clock, busy_for=2.0)

    with pytest.raises(PrinterTimeoutError) as caught:
        _idle(printer, clock)

    assert str(caught.value) == f"printer still busy after 5.0 s (the last poll got no answer); {WAIT_LONGER}"
    assert isinstance(caught.value.__cause__, PrinterTimeoutError)
    assert ": no answer in 5.0 s (1 attempt); " in str(caught.value.__cause__)
    assert clock.now == 5.0
    assert printer.waits[-1] == 3.0


class SlowBusyPrinter(ScriptedPrinter):
    """Answers busy ``answer_after`` seconds after each poll; a poll given less time gets no answer."""

    def __init__(self, clock: Clock, *, answer_after: float) -> None:
        super().__init__(clock)
        self._answer_after = answer_after

    def answer(self, timeout: float) -> bytes:
        if timeout < self._answer_after:
            self.clock.now += timeout
            raise PrinterTimeoutError(f"slow: no complete answer within {timeout:g} s (0 bytes read)")
        self.clock.now += self._answer_after
        return BUSY


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("seconds", "message"),
    [
        # Polls answered at 0.3 and 1.1 s; the third, at 1.6 s, has 0.2 s left and gets no answer.
        (1.8, "printer still busy after 1.8 s (the last poll got no answer)"),
        # Polls answered at 0.3, 1.1 and 1.9 s; the pause after the third is cut to the 0.1 s left.
        (2.0, "printer still busy after 2.0 s"),
    ],
)
def test_a_printer_answering_busy_slowly_is_reported_still_busy_however_the_wait_ends(
    seconds: float, message: str
) -> None:
    clock = Clock()
    printer = SlowBusyPrinter(clock, answer_after=0.3)

    with pytest.raises(PrinterTimeoutError) as caught:
        _idle(printer, clock, seconds=seconds)

    assert str(caught.value) == f"{message}; {WAIT_LONGER}"
    assert clock.now == pytest.approx(seconds)


@pytest.mark.os_agnostic
def test_the_pause_between_two_polls_never_runs_past_the_wait() -> None:
    """A 20 s interval in a 30 s wait polls at 0, 20 and 30 s, never at 40."""
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=math.inf)

    with pytest.raises(PrinterTimeoutError) as caught:
        _idle(printer, clock, seconds=30.0, poll_interval=20.0)

    assert str(caught.value) == f"printer still busy after 30.0 s; {WAIT_LONGER}"
    assert clock.sleeps == [20.0, 10.0]


@pytest.mark.os_agnostic
def test_the_polls_are_spaced_by_the_poll_interval() -> None:
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=1.0)

    _idle(printer, clock, poll_interval=0.25)

    assert clock.sleeps == [0.25, 0.25, 0.25, 0.25]


@pytest.mark.os_agnostic
def test_a_job_connection_that_took_no_poll_is_asked_again_on_a_new_one_which_is_closed() -> None:
    clock = Clock()
    job_channel = HungUpPrinter(clock)
    fresh = BusyPrinter(clock, busy_for=0.0)
    wait = _wait(clock)

    status = wait_idle(job_channel, uri=PRINTER, timeout=1.0, open_printer=_opener(fresh), wait=wait)

    assert status.idle
    # The job's connection is the caller's to close; the one opened here is closed here.
    assert (job_channel.closed, fresh.closed) == (True, True)


@pytest.mark.os_agnostic
def test_after_a_new_connection_answered_busy_the_next_poll_goes_to_it() -> None:
    clock = Clock()
    job_channel = HungUpPrinter(clock)
    fresh = BusyPrinter(clock, busy_for=1.0)

    status = wait_idle(job_channel, uri=PRINTER, timeout=1.0, open_printer=_opener(fresh), wait=_wait(clock))

    assert status.idle
    # Busy at 0 and 0.5 s, idle at 1.0 s: three polls, all on the one new connection.
    assert (len(job_channel.waits), len(fresh.waits)) == (1, 3)


def _status_outcome(
    printer: ScriptedPrinter, *, opens: tuple[PrinterTransport, ...] = ()
) -> tuple[str, float, list[float]]:
    """What ``status`` does with ``printer`` as its first connection: the answer or error, when, the waits."""
    wait = AnswerWait(seconds=5.0, wait_longer=WAIT_LONGER, clock=printer.clock, sleep=printer.clock.sleep)
    try:
        outcome = str(query_status(uri=PRINTER, timeout=1.0, open_printer=_opener(printer, *opens), wait=wait).idle)
    except TransportError as exc:
        outcome = str(exc)
    return outcome, printer.clock.now, printer.waits


def _verify_outcome(
    printer: ScriptedPrinter, *, opens: tuple[PrinterTransport, ...] = ()
) -> tuple[str, float, list[float]]:
    """What ``verify``'s idle wait does with ``printer`` as the job's connection, in the same terms."""
    try:
        status = wait_idle(printer, uri=PRINTER, timeout=1.0, open_printer=_opener(*opens), wait=_wait(printer.clock))
        outcome = str(status.idle)
    except TransportError as exc:
        outcome = str(exc)
    return outcome, printer.clock.now, printer.waits


_SCENARIOS: dict[str, Callable[[Clock], tuple[ScriptedPrinter, tuple[PrinterTransport, ...]]]] = {
    "silent for 3 s": lambda clock: (SilentPrinter(clock, silent_for=3.0), ()),
    "silent past the wait": lambda clock: (SilentPrinter(clock, silent_for=math.inf), ()),
    "hung up, then answering": lambda clock: (HungUpPrinter(clock), (BusyPrinter(clock, busy_for=0.0),)),
    "hung up for good": lambda clock: (HungUpPrinter(clock), tuple(HungUpPrinter(clock) for _ in range(30))),
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_verify_and_status_end_a_wait_on_the_same_printer_alike(scenario: str) -> None:
    status_clock, verify_clock = Clock(), Clock()
    status_printer, status_opens = _SCENARIOS[scenario](status_clock)
    verify_printer, verify_opens = _SCENARIOS[scenario](verify_clock)

    assert _verify_outcome(verify_printer, opens=verify_opens) == _status_outcome(status_printer, opens=status_opens)


_BAD_WAITS: dict[str, Callable[[Clock], IdleWait]] = {
    "wait 0": lambda clock: _wait(clock, seconds=0.0),
    "wait NaN": lambda clock: _wait(clock, seconds=math.nan),
    "poll interval 0": lambda clock: _wait(clock, poll_interval=0.0),
    "poll interval NaN": lambda clock: _wait(clock, poll_interval=math.nan),
    "one-way settle -1": lambda clock: _wait(clock)._replace(one_way_settle=-1.0),
    "one-way settle NaN": lambda clock: _wait(clock)._replace(one_way_settle=math.nan),
    "retry interval 0": lambda clock: _wait(clock)._replace(answer=_wait(clock).answer._replace(retry_interval=0.0)),
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize("case", sorted(_BAD_WAITS))
def test_verify_refuses_an_unusable_wait_before_the_label_is_sent(case: str, tmp_path: Path) -> None:
    """A wait refused only after the job went out would leave a printed label nobody photographed."""
    clock = Clock()
    printer = RecordingPrinter(answers={b"\x01A": IDLE})
    label = Label.model_validate(LABEL)
    options = RenderOptions(model="generic")

    with pytest.raises(ValueError, match=r"must be (above|at least) 0"):
        verify_print(
            render(label, options),
            label,
            get_profile("generic"),
            options=options,
            uri=PRINTER,
            timeout=1.0,
            camera=CameraSettings(kind=CameraKind.FILE),
            output_dir=tmp_path,
            open_printer=open_recording_printer(printer),
            capture=FakeCamera(),
            sleep=clock.sleep,
            clock=clock,
            wait=_BAD_WAITS[case](clock),
        )

    assert (printer.opened, printer.sent) == ([], [])


@pytest.mark.os_agnostic
def test_verify_takes_a_one_way_settle_of_0() -> None:
    clock = Clock()
    wait = _wait(clock)._replace(one_way_settle=0.0)

    assert wait_idle(
        BusyPrinter(clock, busy_for=0.0), uri=PRINTER, timeout=1.0, open_printer=_never_opens, wait=wait
    ).idle


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
def test_verify_waits_status_wait_for_a_printer_silent_longer_than_the_timeout(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    clock = Clock()
    printer = SilentPrinter(clock, silent_for=3.0)
    settings = ["--set", "printer.timeout=1", "--set", "printer.status_wait=5"]

    envelope = _verify(cli_runner, tmp_path, _services(clock, printer), *settings)

    assert envelope["exit_code"] == 0, envelope
    assert printer.waits == [5.0]


@pytest.mark.os_agnostic
def test_verify_waits_status_wait_for_a_printer_busy_longer_than_the_timeout(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    clock = Clock()
    printer = BusyPrinter(clock, busy_for=3.0)
    settings = ["--set", "printer.timeout=1", "--set", "printer.status_wait=5"]

    envelope = _verify(cli_runner, tmp_path, _services(clock, printer), *settings)

    assert envelope["exit_code"] == 0, envelope
    assert printer.waits[0] == 5.0


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


@pytest.mark.os_agnostic
def test_a_library_caller_without_a_camera_is_not_told_about_a_config_file(tmp_path: Path) -> None:
    """verify_print takes CameraSettings directly, so the refusal names that, not a [camera] section."""
    clock = Clock()
    label = Label.model_validate(LABEL)
    options = RenderOptions(model="generic")
    with pytest.raises(CameraError) as caught:
        verify_print(
            render(label, options),
            label,
            get_profile("generic"),
            options=options,
            uri=PRINTER,
            timeout=1.0,
            camera=CameraSettings(),
            output_dir=tmp_path,
            open_printer=open_recording_printer(RecordingPrinter()),
            capture=FakeCamera(),
            sleep=clock.sleep,
            clock=clock,
        )
    assert "[camera]" not in str(caught.value)
    assert "CameraSettings.kind" in str(caught.value)
