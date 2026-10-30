"""Use cases: render labels (one or a batch of data records), print, query and verify printers.

Every use case takes its collaborators (printer opener, camera, clock) as keyword arguments, so
the CLI wires production adapters and the tests wire in-memory ones.
"""

from __future__ import annotations

import time
from contextlib import closing
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple, TypeVar

from pydantic import BaseModel, ConfigDict

from ..domain.batch import cut_across_records
from ..domain.detect import Detection, Evidence, suggest_profiles
from ..domain.enums import Severity
from ..domain.errors import LabelRejectedError, PrinterProtocolError, PrinterTimeoutError, TransportError
from ..domain.findings import Finding
from ..domain.label import Cut
from ..domain.memory import MemoryDirectory, parse_module_directory
from ..domain.placeholders import fill_label
from ..domain.render import RenderResult, build_job
from ..domain.status import (
    CANCEL,
    CUT_NOW,
    FORM_FEED,
    MEMORY_DIRECTORY_REQUESTS,
    MEMORY_REQUEST,
    RESET,
    SERIAL_TEST_REQUEST,
    STATUS_REQUEST,
    TOGGLE_PAUSE,
    VERSION_REQUEST,
    PrinterStatus,
    is_status_line,
    parse_ascii_status,
    parse_memory_report,
    parse_version,
)
from ..domain.timing import check_timeout

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from ..domain.label import Label
    from ..domain.memory import MemoryKind
    from ..domain.render_context import RenderOptions
    from .ports import Clock, OpenPrinter, PrinterTransport, Sleep

_T = TypeVar("_T")


class BatchResult(BaseModel):
    """A label rendered once per data record.

    Attributes:
        data: All jobs back to back, in record order, with any batch cut commands between them.
        results: One render result per record.
        batch_findings: Findings about the batch as a whole (cutting across records).
    """

    # Base64 in JSON for the same reason as RenderResult: the joined jobs are not UTF-8.
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64")

    data: bytes
    results: tuple[RenderResult, ...]
    batch_findings: tuple[Finding, ...] = ()

    @property
    def ok(self) -> bool:
        """True when no record and no batch finding is an error."""
        batch_ok = all(f.severity is not Severity.ERROR for f in self.batch_findings)
        return batch_ok and all(result.ok for result in self.results)

    def findings(self) -> list[tuple[int, Finding]]:
        """Every finding with the index of the record it belongs to."""
        return [(i, f) for i, result in enumerate(self.results) for f in result.findings]


def render_label(label: Label, options: RenderOptions) -> RenderResult:
    """Render one label and report every finding (never raises for a finding)."""
    return build_job(label, options)


def render_batch(label: Label, records: Iterable[Mapping[str, str]], options: RenderOptions) -> BatchResult:
    """Render ``label`` once per record, filling its ``{name}`` placeholders.

    Raises:
        MissingFieldError: A record lacks a field a placeholder names.
        pydantic.ValidationError: A filled value breaks a field limit.
    """
    cut = label.cut
    if cut is None or cut.every == 1 or label.quantity > 1:
        results = tuple(build_job(fill_label(label, record), options, filled=True) for record in records)
        return BatchResult(data=b"".join(result.data for result in results), results=results)
    per_record = label.model_copy(update={"cut": Cut(every=1, at_end=False)})
    results = tuple(build_job(fill_label(per_record, record), options, filled=True) for record in records)
    data, findings = cut_across_records([r.data for r in results], cut=cut, options=options)
    return BatchResult(data=data, results=results, batch_findings=findings)


class PrintReport(BaseModel):
    """What was sent where."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uri: str
    bytes_sent: int


def send_bytes(data: bytes, *, uri: str, timeout: float, open_printer: OpenPrinter) -> PrintReport:
    """Send ``data`` to the printer at ``uri``.

    Raises:
        TransportError: The printer could not be reached or written.
    """
    with closing(open_printer(uri, timeout=timeout)) as printer:
        printer.send(data)
    return PrintReport(uri=uri, bytes_sent=len(data))


def print_result(
    result: RenderResult | BatchResult, *, uri: str, timeout: float, open_printer: OpenPrinter
) -> PrintReport:
    """Send a rendered job, refusing one that has error findings.

    Raises:
        LabelRejectedError: The job has an error finding; nothing was sent.
        TransportError: The printer could not be reached or written.
    """
    if not result.ok:
        msg = "label has errors; nothing was sent"
        raise LabelRejectedError(msg)
    return send_bytes(result.data, uri=uri, timeout=timeout, open_printer=open_printer)


class PrinterCommand(str, Enum):
    """Single printer commands the CLI offers."""

    RESET = "reset"
    CANCEL = "cancel"
    PAUSE = "pause"
    FEED = "feed"
    CUT = "cut"


_COMMAND_BYTES: dict[PrinterCommand, bytes] = {
    PrinterCommand.RESET: RESET,
    PrinterCommand.CANCEL: CANCEL,
    PrinterCommand.PAUSE: TOGGLE_PAUSE,
    PrinterCommand.FEED: FORM_FEED,
    PrinterCommand.CUT: CUT_NOW,
}


def send_command(command: PrinterCommand, *, uri: str, timeout: float, open_printer: OpenPrinter) -> PrintReport:
    """Send one immediate or system command (reset, cancel, pause, feed, cut)."""
    return send_bytes(_COMMAND_BYTES[command], uri=uri, timeout=timeout, open_printer=open_printer)


#: Default of ``[printer] status_wait``: seconds ``status`` and ``memory`` keep trying for the first
#: answer. Measured once on a SerComm print server feeding an Allegro 2 at 9600 baud: after a
#: 13.6 KB image job it refused connections for at least 7 s and kept the next one silent past 10 s.
STATUS_WAIT_SECONDS = 30.0
#: Default of ``[printer] status_retry_interval``: seconds between two connections after one was
#: refused or hung up unanswered, so a print server that takes and drops connections at once is
#: not dialled in a tight loop.
RETRY_INTERVAL_SECONDS = 0.25
#: Why a wait for the first answer ran out, when it did.
BUSY_HINT = "the printer or print server may still be busy with an earlier job"


class AnswerWait(NamedTuple):
    """How ``status`` and ``memory`` keep trying for the printer's first answer.

    Within ``seconds`` a refused connection, or one that hung up before a byte of answer came, is
    tried again on a new connection after ``retry_interval``; a connection that stays open but
    silent is read until the wait ends, never asked a second time, so at most one request is ever
    outstanding.

    Attributes:
        seconds: The whole wait, from the first connect to the first answer.
        retry_interval: Seconds between two connections.
        wait_longer: How the caller lengthens the wait, added to the message when it runs out.
        clock: Monotonic seconds.
        sleep: Pauses between two connections.
    """

    seconds: float
    retry_interval: float = RETRY_INTERVAL_SECONDS
    wait_longer: str = ""
    clock: Clock = time.monotonic
    sleep: Sleep = time.sleep


def query_status(
    *, uri: str, timeout: float, open_printer: OpenPrinter, wait: AnswerWait | None = None
) -> PrinterStatus:
    """Ask the printer for its status (``<SOH>A``), trying again within ``wait`` (see :class:`AnswerWait`).

    Lines that do not end in eight status flags are skipped: a print server can pass on what the
    printer still owed an earlier query (a memory report line) after the status request went out.

    Args:
        uri: The printer.
        timeout: Seconds for each connect and write, never past the wait.
        open_printer: Opens the channel.
        wait: How long to keep trying; None waits ``timeout`` seconds in all.

    Raises:
        UnsupportedOperationError: The channel cannot read answers (file, stdout, device file).
        PrinterTimeoutError: The printer did not answer within the wait.
        PrinterProtocolError: The answer was not a status string; it is not asked again.
        TransportError: The printer could not be reached for another reason.
    """
    printer, answer = _first_answer(
        lambda channel, left: channel.query(STATUS_REQUEST, timeout=left, accept=is_status_line),
        uri=uri,
        timeout=timeout,
        open_printer=open_printer,
        wait=AnswerWait(seconds=timeout) if wait is None else wait,
    )
    printer.close()
    return parse_ascii_status(answer)


def _first_answer(
    ask: Callable[[PrinterTransport, float], _T],
    *,
    uri: str,
    timeout: float,
    open_printer: OpenPrinter,
    wait: AnswerWait,
) -> tuple[PrinterTransport, _T]:
    """Open the printer and ``ask`` it, again on a new connection while nothing answered, within ``wait``.

    ``ask`` gets the open channel and the seconds left. Returns the channel, still open for the
    caller to go on with and close, and what ``ask`` returned.

    Raises:
        PrinterTimeoutError: The wait ran out; the message counts the attempts and quotes the last.
        TransportError: Any failure that is not ``unanswered``, raised as is.
        ValueError: ``timeout`` or a wait value is not a usable number of seconds; nothing was opened.
    """
    check_timeout(timeout)
    check_timeout(wait.seconds, name="wait")
    check_timeout(wait.retry_interval, name="retry interval")
    deadline = wait.clock() + wait.seconds
    attempts = 0
    while True:
        attempts += 1
        try:
            # The first connect gets the plain timeout (the wait has barely begun); a later one
            # never runs past the wait.
            connect = min(timeout, wait.seconds if attempts == 1 else _left(deadline, wait.clock))
            printer = open_printer(uri, timeout=connect)
            return printer, _ask_or_close(ask, printer, deadline=deadline, clock=wait.clock)
        except PrinterTimeoutError as exc:
            raise _ran_out(uri, wait=wait, attempts=attempts, last=exc) from exc
        except TransportError as exc:
            if not exc.unanswered:
                raise
            if wait.clock() + wait.retry_interval >= deadline:
                raise _ran_out(uri, wait=wait, attempts=attempts, last=exc) from exc
        wait.sleep(wait.retry_interval)


def _ask_or_close(
    ask: Callable[[PrinterTransport, float], _T], printer: PrinterTransport, *, deadline: float, clock: Clock
) -> _T:
    """Return ``ask`` of ``printer`` and the seconds left until ``deadline``, closing ``printer`` when it fails."""
    try:
        return ask(printer, _left(deadline, clock))
    except BaseException:
        printer.close()
        raise


def _left(deadline: float, clock: Clock) -> float:
    """Seconds until ``deadline``.

    Raises:
        PrinterTimeoutError: It has passed (a connect can use up the last of the wait).
    """
    left = deadline - clock()
    if left <= 0:
        raise PrinterTimeoutError("the wait ended before this attempt could ask")
    return left


def _ran_out(uri: str, *, wait: AnswerWait, attempts: int, last: TransportError) -> PrinterTimeoutError:
    """The error for a wait that ended without an answer, naming the wait, the attempts and the last failure.

    Example:
        >>> last = TransportError("cannot connect to tcp 192.0.2.60:9100: Connection refused")
        >>> message = str(_ran_out("tcp://192.0.2.60:9100", wait=AnswerWait(seconds=2.0), attempts=3, last=last))
        >>> message.split("; ")[0]
        'tcp://192.0.2.60:9100: no answer within 2 s (3 attempts)'
        >>> message.split(". ")[-1]
        'Last attempt: cannot connect to tcp 192.0.2.60:9100: Connection refused'
    """
    tried = "1 attempt" if attempts == 1 else f"{attempts} attempts"
    advice = f"; {wait.wait_longer}" if wait.wait_longer else ""
    return PrinterTimeoutError(
        f"{uri}: no answer within {wait.seconds:g} s ({tried}); {BUSY_HINT}{advice}. Last attempt: {last}"
    )


#: Seconds of silence that end a ``<STX>W`` directory answer, which has no terminator. A printer
#: sends the listing in one go, so a pause this long means it is done; a serial line at 9600 baud
#: moves about a thousand bytes in that time.
DIRECTORY_IDLE_SECONDS = 1.0


def read_memory_directory(
    *,
    kinds: Sequence[MemoryKind],
    uri: str,
    timeout: float,
    open_printer: OpenPrinter,
    idle: float = DIRECTORY_IDLE_SECONDS,
    wait: AnswerWait | None = None,
) -> tuple[MemoryDirectory, ...]:
    """Ask the printer for the memory module directory of each kind (``<STX>W``), in order.

    The first request is tried again within ``wait`` as :func:`query_status` tries its own; the
    other kinds are asked on the connection that answered, each waiting ``timeout`` for its first
    byte. An answer the parser cannot read is returned with ``parsed`` False and its raw text,
    never raised: the printer did answer, and the text is what a reader needs to judge it.

    Args:
        kinds: The kinds to list, each asked separately over one connection.
        uri: The printer.
        timeout: Seconds for each connect and write, and for the first byte of every answer after
            the first.
        open_printer: Opens the channel.
        idle: Seconds of silence that end an answer.
        wait: How long to keep trying for the first answer; None waits ``timeout`` seconds in all.

    Returns:
        One directory per kind, in the order of ``kinds``; none, and nothing opened, for no kinds.

    Raises:
        UnsupportedOperationError: The channel cannot read answers (file, stdout, device file).
        PrinterTimeoutError: The printer sent nothing in time, or its answer did not end.
        PrinterProtocolError: The answer ran past the transport's size bound.
        TransportError: The printer could not be reached.
        ValueError: ``idle`` is not a usable number of seconds; nothing was opened.
    """
    check_timeout(idle, name="idle")
    if not kinds:
        return ()
    first, *rest = kinds
    printer, answer = _first_answer(
        lambda channel, left: channel.read_answer(MEMORY_DIRECTORY_REQUESTS[first], timeout=left, idle=idle),
        uri=uri,
        timeout=timeout,
        open_printer=open_printer,
        wait=AnswerWait(seconds=timeout) if wait is None else wait,
    )
    with closing(printer):
        answers = [answer] + [
            printer.read_answer(MEMORY_DIRECTORY_REQUESTS[kind], timeout=timeout, idle=idle) for kind in rest
        ]
    return tuple(parse_module_directory(raw, kind=kind) for raw, kind in zip(answers, kinds, strict=True))


#: Lines in the ``<STX>KQ`` memory report (docs/dpl-reference.md section 8).
MEMORY_REPORT_LINES = 8


class DetectReport(BaseModel):
    """Evidence collected from the printer and the profiles that fit it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: Evidence
    detection: Detection


def _ask(printer: PrinterTransport, request: bytes, *, timeout: float, lines: int = 1) -> bytes | None:
    try:
        return printer.query(request, timeout=timeout, lines=lines)
    except (PrinterTimeoutError, PrinterProtocolError):
        return None


def detect_printer(*, uri: str, timeout: float, open_printer: OpenPrinter) -> DetectReport:
    """Ask the printer what it answers and suggest the profiles that fit.

    Raises:
        UnsupportedOperationError: The channel cannot read answers.
        TransportError: The printer could not be reached.
    """
    with closing(open_printer(uri, timeout=timeout)) as printer:
        serial = _ask(printer, SERIAL_TEST_REQUEST, timeout=timeout)
        version = _ask(printer, VERSION_REQUEST, timeout=timeout)
        memory = _ask(printer, MEMORY_REQUEST, timeout=timeout, lines=MEMORY_REPORT_LINES)
    evidence = Evidence(
        serial_test=None if serial is None else serial.strip().startswith(b"Y"),
        version=None if version is None else parse_version(version),
        memory=None if memory is None else parse_memory_report(memory),
    )
    return DetectReport(evidence=evidence, detection=suggest_profiles(evidence))


__all__ = [
    "BUSY_HINT",
    "DIRECTORY_IDLE_SECONDS",
    "RETRY_INTERVAL_SECONDS",
    "STATUS_WAIT_SECONDS",
    "AnswerWait",
    "BatchResult",
    "DetectReport",
    "PrintReport",
    "PrinterCommand",
    "detect_printer",
    "print_result",
    "query_status",
    "read_memory_directory",
    "render_batch",
    "render_label",
    "send_bytes",
    "send_command",
]
