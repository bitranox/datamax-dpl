"""Use cases: render labels (one or a batch of data records), print, query and verify printers.

Every use case takes its collaborators (printer opener, camera, clock) as keyword arguments, so
the CLI wires production adapters and the tests wire in-memory ones.

A job that no clear precedes must not download an image name the printer already holds (it would
print nothing), so before such a job is printed :func:`with_stored_names` reads the printer's
graphic directory and renders again; every other render, and every offline one, reads nothing.
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
from ..domain.errors import (
    LabelRejectedError,
    PrinterProtocolError,
    PrinterTimeoutError,
    TransportError,
    UnsupportedOperationError,
)
from ..domain.findings import Finding
from ..domain.held_images import DEFAULT_MODULE, DEFAULT_MODULE_UNREAD, HeldImages, held_images_from
from ..domain.label import Cut
from ..domain.memory import MemoryDirectory, parse_module_directory
from ..domain.placeholders import fill_label
from ..domain.render import RenderResult, build_job, build_jobs
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
_Rendered = TypeVar("_Rendered", bound="RenderResult | BatchResult")


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


def render_label(label: Label, options: RenderOptions, *, held: HeldImages | None = None) -> RenderResult:
    """Render one label and report every finding (never raises for a finding).

    ``held`` is what the printer holds in the image module, None when it was not asked.
    """
    return build_job(label, options, held=held)


def render_batch(
    label: Label, records: Iterable[Mapping[str, str]], options: RenderOptions, *, held: HeldImages | None = None
) -> BatchResult:
    """Render ``label`` once per record, filling its ``{name}`` placeholders.

    ``held`` is what the printer holds in the image module before the first record, None when it
    was not asked; with no clear, a name an earlier record downloaded counts as held too.

    Raises:
        MissingFieldError: A record lacks a field a placeholder names.
        pydantic.ValidationError: A filled value breaks a field limit.
    """
    cut = label.cut
    if cut is None or cut.every == 1 or label.quantity > 1:
        results = build_jobs((fill_label(label, record) for record in records), options, filled=True, held=held)
        return BatchResult(data=b"".join(result.data for result in results), results=results)
    per_record = label.model_copy(update={"cut": Cut(every=1, at_end=False)})
    results = build_jobs((fill_label(per_record, record) for record in records), options, filled=True, held=held)
    data, findings = cut_across_records([(r.data, r.closing) for r in results], cut=cut, options=options)
    return BatchResult(data=data, results=results, batch_findings=findings)


def _unread_module(result: RenderResult | BatchResult) -> str | None:
    """The image module whose stored names a job needs and was not given, or None when it needs none."""
    results = result.results if isinstance(result, BatchResult) else (result,)
    return next((r.image_module for r in results if r.names_unread and r.image_module is not None), None)


#: Why the stored names are unknown on a channel that cannot read the printer's answer.
ONE_WAY_LINK = "the link to the printer is one-way"


def stored_image_names(
    *,
    module: str,
    uri: str,
    timeout: float,
    open_printer: OpenPrinter,
    wait: AnswerWait | None = None,
    one_way: bool = False,
) -> HeldImages:
    """Return the image names the printer holds in ``module``, read from its graphic directory (``<STX>W``).

    Never raises for a printer that cannot say: a one-way channel, no answer in time or an answer
    the parser cannot read gives unknown names with the reason, and the job is then rendered with
    every download kept and ``image_names_unread`` quoting that reason. A printer that cannot be
    reached at all is left to the send that follows, which reports it. Nothing is opened for the
    default module, whose letter no directory lists, nor for a link the caller already knows to be
    one-way: opening a file or a device to find that out would create it for a job that may yet be
    refused.

    Args:
        module: The letter of the job's image module.
        uri: The printer.
        timeout: Seconds for each connect and write.
        open_printer: Opens the channel.
        wait: How long to keep trying for the answer, as ``memory`` does; None waits ``timeout``.
        one_way: True when ``uri`` names a channel that cannot read answers (a file, stdout, a
            device file), decided from the URI alone.
    """
    if module == DEFAULT_MODULE:
        return HeldImages(unread_because=DEFAULT_MODULE_UNREAD)
    if one_way:
        return HeldImages(unread_because=ONE_WAY_LINK)
    try:
        (directory,) = read_memory_directory(
            kinds=("graphic",), uri=uri, timeout=timeout, open_printer=open_printer, wait=wait
        )
    except UnsupportedOperationError:
        return HeldImages(unread_because=ONE_WAY_LINK)
    except TransportError as exc:
        return HeldImages(unread_because=f"reading them failed: {exc}")
    return held_images_from(directory, module=module)


def with_stored_names(
    render: Callable[[HeldImages | None], _Rendered], *, read: Callable[[str], HeldImages]
) -> _Rendered:
    """Render a job to be printed, reading the printer's stored image names first where it needs them.

    The job is rendered once without them. Only when no clear precedes its downloads and it
    downloads an image (``RenderResult.names_unread``) are the names of its module read with
    ``read`` and the job rendered again with them; a job that clears first reads nothing.

    Args:
        render: Renders the job with the given names (None: not asked).
        read: Returns what the printer holds in the module it is given, e.g. via
            :func:`stored_image_names`.

    Returns:
        The job to send.
    """
    result = render(None)
    module = _unread_module(result)
    if module is None:
        return result
    return render(read(module))


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
#: Default of ``[printer] status_retry_interval``: seconds between two connections after one took
#: no request, so a print server that takes and drops connections at once is
#: not dialled in a tight loop.
RETRY_INTERVAL_SECONDS = 0.25
#: Why a wait for the first answer ran out, when it did.
BUSY_HINT = "the printer or print server may still be busy with an earlier job"


class AnswerWait(NamedTuple):
    """How ``status`` and ``memory`` keep trying for the printer's first answer.

    Within ``seconds`` a connection that took no request (``TransportError.unanswered``: refused,
    timed out while connecting, or closed or reset before the request or a byte of answer) is
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
        _ask_status,
        uri=uri,
        timeout=timeout,
        open_printer=open_printer,
        wait=AnswerWait(seconds=timeout) if wait is None else wait,
    )
    printer.close()
    return parse_ascii_status(answer)


def poll_status(
    printer: PrinterTransport, *, uri: str, timeout: float, open_printer: OpenPrinter, wait: AnswerWait, started: float
) -> tuple[PrinterTransport, PrinterStatus]:
    """Ask the already open ``printer`` for its status exactly as :func:`query_status` asks a new one.

    The same :class:`AnswerWait` applies, measured from ``started`` so that several polls share
    one wait: a silent channel is read until the wait ends and never asked a second time, and a
    failure that took no request is asked again on a new connection opened with ``open_printer``.

    Returns:
        The channel that answered, ``printer`` or the new connection, still open, and the status.

    Raises:
        PrinterTimeoutError: The wait ran out without an answer; ``printer`` is then closed.
        PrinterProtocolError: The answer was not a status string.
        TransportError: Any other failure, raised as is.
    """
    first = [printer]

    def open_again(uri: str, *, timeout: float) -> PrinterTransport:
        # The first attempt is the open channel itself; only a failure that took no request opens another.
        return first.pop() if first else open_printer(uri, timeout=timeout)

    channel, answer = _first_answer(
        _ask_status, uri=uri, timeout=timeout, open_printer=open_again, wait=wait, started=started
    )
    return channel, parse_ascii_status(answer)


def _ask_status(channel: PrinterTransport, left: float) -> bytes:
    """The status request of every status wait: its answer is waited for as long as the wait has left."""
    return channel.query(STATUS_REQUEST, timeout=left, accept=is_status_line)


def _first_answer(
    ask: Callable[[PrinterTransport, float], _T],
    *,
    uri: str,
    timeout: float,
    open_printer: OpenPrinter,
    wait: AnswerWait,
    started: float | None = None,
) -> tuple[PrinterTransport, _T]:
    """Open the printer and ``ask`` it, again on a new connection while nothing answered, within ``wait``.

    ``ask`` gets the open channel and the seconds left. Returns the channel, still open for the
    caller to go on with and close, and what ``ask`` returned. An attempt is one opening of the
    channel; the TCP transport itself retries a refused connect within one opening for up to 3 s.
    ``started`` is when the wait began, when an earlier ask already used part of it; by default
    it begins now.

    Raises:
        PrinterTimeoutError: The wait ran out; the message gives the seconds waited, counts the
            attempts and quotes the last failure.
        TransportError: Any failure that is not ``unanswered``, raised as is.
        ValueError: ``timeout`` or a wait value is not a usable number of seconds; nothing was opened.
    """
    check_timeout(timeout)
    check_timeout(wait.seconds, name="wait")
    check_timeout(wait.retry_interval, name="retry interval")
    tries = _Tries(uri, wait, started=started)
    while True:
        # No attempt starts once the wait is over: the pause before it can overrun the end.
        if tries.attempts and tries.left() <= 0:
            raise tries.ran_out() from tries.last
        # The first connect gets the plain timeout (the wait has barely begun); a later one
        # never runs past the wait.
        connect = min(timeout, tries.left() if tries.attempts else wait.seconds)
        tries.attempts += 1
        try:
            printer = open_printer(uri, timeout=connect)
            return printer, _ask_or_close(ask, printer, tries=tries)
        except _WaitEndedError:
            raise tries.ran_out() from tries.last
        except TransportError as exc:
            tries.settle(exc)
        wait.sleep(wait.retry_interval)


class _WaitEndedError(Exception):
    """A connect used up the rest of the wait, so nothing was asked on it."""


class _Tries:
    """The attempts of one :class:`AnswerWait`: when it started and ends, how many ran, the last failure."""

    def __init__(self, uri: str, wait: AnswerWait, *, started: float | None = None) -> None:
        self.uri = uri
        self.wait = wait
        self.started = wait.clock() if started is None else started
        self.deadline = self.started + wait.seconds
        self.attempts = 0
        self.last: TransportError | None = None

    def left(self) -> float:
        """Seconds until the wait ends; zero or less once it has."""
        return self.deadline - self.wait.clock()

    def settle(self, error: TransportError) -> None:
        """Return when ``error`` may be asked again before the wait ends; raise otherwise.

        Raises:
            PrinterTimeoutError: ``error`` was a timeout, or ``unanswered`` with no time left for
                the pause and another attempt.
            TransportError: ``error`` itself, any other failure.
        """
        self.last = error
        if error.unanswered and self.left() > self.wait.retry_interval:
            return
        if error.unanswered or isinstance(error, PrinterTimeoutError):
            raise self.ran_out() from error
        raise error

    def ran_out(self) -> PrinterTimeoutError:
        """The error for a wait that ended without an answer.

        It gives the seconds actually waited (a capped answer wait or an overrunning pause makes
        them differ from the wait asked for), the attempts that ran, and the last failure without
        the transport's own advice, since the wait is what a reader can lengthen here.

        Example:
            >>> ticks = iter([0.0, 2.3])
            >>> tries = _Tries("tcp://192.0.2.60:9100", AnswerWait(seconds=2.0, clock=lambda: next(ticks)))
            >>> tries.attempts = 3
            >>> tries.last = TransportError("cannot connect to tcp 192.0.2.60:9100: Connection refused")
            >>> message = str(tries.ran_out())
            >>> message.split("; ")[0]
            'tcp://192.0.2.60:9100: no answer in 2.3 s (3 attempts)'
            >>> message.split(". ")[-1]
            'Last failure: cannot connect to tcp 192.0.2.60:9100: Connection refused'
        """
        waited = self.wait.clock() - self.started
        tried = "1 attempt" if self.attempts == 1 else f"{self.attempts} attempts"
        advice = f"; {self.wait.wait_longer}" if self.wait.wait_longer else ""
        last = f". Last failure: {self.last.reason}" if self.last is not None else ""
        return PrinterTimeoutError(f"{self.uri}: no answer in {waited:.1f} s ({tried}); {BUSY_HINT}{advice}{last}")


def _ask_or_close(ask: Callable[[PrinterTransport, float], _T], printer: PrinterTransport, *, tries: _Tries) -> _T:
    """Return ``ask`` of ``printer`` and the seconds left, closing ``printer`` when it fails.

    Raises:
        _WaitEndedError: No time is left to ask.
    """
    try:
        left = tries.left()
        if left <= 0:
            raise _WaitEndedError
        return ask(printer, left)
    except BaseException:
        printer.close()
        raise


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
    "ONE_WAY_LINK",
    "RETRY_INTERVAL_SECONDS",
    "STATUS_WAIT_SECONDS",
    "AnswerWait",
    "BatchResult",
    "DetectReport",
    "PrintReport",
    "PrinterCommand",
    "detect_printer",
    "poll_status",
    "print_result",
    "query_status",
    "read_memory_directory",
    "render_batch",
    "render_label",
    "send_bytes",
    "send_command",
    "stored_image_names",
    "with_stored_names",
]
