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
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Generic, NamedTuple, Protocol, TypedDict, TypeVar

from pydantic import BaseModel, ConfigDict

from ..domain.batch import batch_cuts, with_cut
from ..domain.detect import Detection, Evidence, suggest_profiles
from ..domain.enums import ImageModule, MemoryKind, Severity
from ..domain.errors import (
    AnswerNotEndedError,
    LabelRejectedError,
    PrinterProtocolError,
    PrinterTimeoutError,
    TransportError,
    UnsupportedOperationError,
)
from ..domain.findings import Finding
from ..domain.held_images import DEFAULT_MODULE_UNREAD, HeldImages, held_images_from
from ..domain.label import Cut, Label
from ..domain.memory import MemoryDirectory, parse_module_directory
from ..domain.placeholders import fill_label
from ..domain.render import RenderedRecord, RenderResult, build_job, build_jobs, build_record
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
from ..domain.timing import MAX_ANSWER_SECONDS, check_timeout

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

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
    #: Whether a batch cut (``<STX>o``) follows each record; empty when the batch cuts across none.
    cut_after: tuple[bool, ...] = ()

    @classmethod
    def joined(
        cls,
        results: tuple[RenderResult, ...],
        *,
        batch_findings: tuple[Finding, ...] = (),
        cut_after: tuple[bool, ...] = (),
    ) -> BatchResult:
        """Build the batch whose ``data`` is every record's job back to back, each with its batch cut.

        Args:
            results: One render result per record.
            batch_findings: Findings about the batch as a whole.
            cut_after: Whether a batch cut follows each record; empty when the batch cuts across none.
        """
        data = b"".join(_record_jobs(results, cut_after))
        return cls(data=data, results=results, batch_findings=batch_findings, cut_after=cut_after)

    def record_jobs(self) -> tuple[bytes, ...]:
        """Each record's bytes as ``data`` holds them, its batch cut included; joined they are ``data``."""
        return _record_jobs(self.results, self.cut_after)

    @property
    def ok(self) -> bool:
        """True when no record and no batch finding is an error."""
        batch_ok = all(f.severity is not Severity.ERROR for f in self.batch_findings)
        return batch_ok and all(result.ok for result in self.results)


def _record_jobs(results: tuple[RenderResult, ...], cut_after: tuple[bool, ...]) -> tuple[bytes, ...]:
    """Each record's job with the batch cut that follows it, if any."""
    cuts = cut_after or (False,) * len(results)
    return tuple(with_cut(r.data, r.closing, cut=after) for r, after in zip(results, cuts, strict=True))


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
    batch_label = _batch_label(label)
    results = build_jobs(
        (fill_label(batch_label.per_record, record) for record in records), options, filled=True, held=held
    )
    if batch_label.cut is None:
        return BatchResult(data=b"".join(result.data for result in results), results=results)
    cuts = batch_cuts(len(results), cut=batch_label.cut, options=options)
    return BatchResult.joined(results, batch_findings=cuts.findings, cut_after=cuts.cut_after)


class _CutUpdate(TypedDict):
    """The field a batch that cuts across records replaces on each record's label."""

    cut: Cut | None


# model_copy(update=...) takes any key and sets it unchecked, so the key is checked here at import.
if not _CutUpdate.__required_keys__ <= Label.model_fields.keys():  # pragma: no cover
    raise RuntimeError("_CutUpdate names a field Label does not have")


@dataclass(frozen=True)
class _BatchLabel:
    """The label each record of a batch fills, and the cut the batch makes across records.

    Attributes:
        per_record: The label each record fills.
        cut: The cut the batch makes across records with ``<STX>o``; None when it makes none.
    """

    per_record: Label
    cut: Cut | None


def _batch_label(label: Label) -> _BatchLabel:
    """Return the label each record fills, and the cut the batch makes across records.

    DPL's cut-by counts copies of one format, so a batch that cuts every N records cuts with
    ``<STX>o`` itself (:mod:`~datamax_dpl.domain.batch`), and each record's own label does not cut.
    """
    cut = label.cut
    if cut is None or cut.every == 1 or label.quantity > 1:
        return _BatchLabel(per_record=label, cut=None)
    # model_copy, not a new Label: the label is validated already, and only its cut changes.
    return _BatchLabel(per_record=label.model_copy(update=_CutUpdate(cut=Cut(every=1, at_end=False))), cut=cut)


class RecordRender(Protocol):
    """Render one label of a job given what the printer holds before it; see :func:`record_renders`."""

    def __call__(self, held: HeldImages, *, halve: bool) -> RenderedRecord:
        """Return the label's result and what the printer holds after it; ``halve`` halves its images."""
        ...


def record_renders(
    label: Label, records: Sequence[Mapping[str, str]] | None, options: RenderOptions
) -> tuple[RecordRender, ...]:
    """Return how to render each label of a job again, as :func:`render_label` or :func:`render_batch` rendered it.

    A sender that checks each label's image upload renders a label again with what it then knows
    the printer holds (an empty module after a clear) or with its images halved, and carries
    what the printer holds after it to the next label.

    Args:
        label: The label.
        records: The data records of a batch, or None for one label.
        options: How to render.

    Returns:
        One render per label, in the order they are sent.

    Raises:
        MissingFieldError: A record lacks a field a placeholder names.
        pydantic.ValidationError: A filled value breaks a field limit.
    """
    if records is None:
        return (_render_of(label, options, filled=False),)
    per_record = _batch_label(label).per_record
    return tuple(_render_of(fill_label(per_record, record), options, filled=True) for record in records)


def _render_of(label: Label, options: RenderOptions, *, filled: bool) -> RecordRender:
    def render(held: HeldImages, *, halve: bool) -> RenderedRecord:
        return build_record(label, options, filled=filled, held=held, halve=halve)

    return render


def _unread_module(result: RenderResult | BatchResult) -> ImageModule | None:
    """The image module whose stored names a job needs and was not given, or None when it needs none."""
    results = result.results if isinstance(result, BatchResult) else (result,)
    return next((r.image_module for r in results if r.names_unread and r.image_module is not None), None)


#: Why the stored names are unknown on a channel that cannot read the printer's answer.
ONE_WAY_LINK = "the link to the printer is one-way"


def stored_image_names(
    *,
    module: ImageModule,
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
        module: The job's image module.
        uri: The printer.
        timeout: Seconds for each connect and write.
        open_printer: Opens the channel.
        wait: How long to keep trying for the answer, as ``memory`` does, and by when its last
            byte must have arrived: an answer still coming when the wait ends counts as no
            answer. The silence that ends the answer (:data:`DIRECTORY_IDLE_SECONDS`) comes on
            top. None waits ``timeout``.
        one_way: True when ``uri`` names a channel that cannot read answers (a file, stdout, a
            device file), decided from the URI alone.
    """
    if module == ImageModule.C:
        return HeldImages(unread_because=DEFAULT_MODULE_UNREAD)
    if one_way:
        return HeldImages(unread_because=ONE_WAY_LINK)
    read = read_stored_names(None, module=module, uri=uri, timeout=timeout, open_printer=open_printer, wait=wait)
    if read.channel is not None:
        read.channel.close()
    return read.held


@dataclass(frozen=True)
class StoredNamesRead:
    """What :func:`read_stored_names` read, and the channel it read it on.

    Attributes:
        channel: The channel that answered, still open for the caller to go on with and close;
            None when the read failed, every channel it used closed then.
        held: What the printer says the module holds; unknown names with the reason when the
            read failed or its answer could not be read.
    """

    channel: PrinterTransport | None
    held: HeldImages


def read_stored_names(
    printer: PrinterTransport | None,
    *,
    module: ImageModule,
    uri: str,
    timeout: float,
    open_printer: OpenPrinter,
    wait: AnswerWait | None = None,
) -> StoredNamesRead:
    """Read the image names the printer holds in ``module`` (``<STX>W``), asking on ``printer`` first.

    The read :func:`stored_image_names` makes, on a channel the caller may already have open: a
    sender that has just downloaded a job's images asks on the connection they went out on, and
    goes on with the job there. A channel that fails before it takes the request is replaced by a
    new one within ``wait``, as :func:`query_status` replaces its own.

    Args:
        printer: The open channel to ask first, or None to open one with ``open_printer``.
        module: The job's image module.
        uri: The printer.
        timeout: Seconds for each connect and write.
        open_printer: Opens a channel when there is none, or the one asked took no request.
        wait: How long to keep trying, bounding the answer's last byte (see
            :func:`stored_image_names`); None waits ``timeout``.

    Returns:
        The channel that answered and what it says ``module`` holds (see :class:`StoredNamesRead`).
    """
    first = [] if printer is None else [printer]

    def open_first(uri: str, *, timeout: float) -> PrinterTransport:
        # The first attempt is the open channel itself; only a failure that took no request opens another.
        return first.pop() if first else open_printer(uri, timeout=timeout)

    try:
        asked = _ask_directories(
            kinds=(MemoryKind.GRAPHIC,),
            uri=uri,
            timeout=timeout,
            open_printer=open_first,
            idle=DIRECTORY_IDLE_SECONDS,
            wait=wait,
            answer_within_wait=True,
        )
    except UnsupportedOperationError:
        return StoredNamesRead(channel=None, held=HeldImages(unread_because=ONE_WAY_LINK))
    except AnswerNotEndedError:
        unread = _still_arriving(AnswerWait(seconds=timeout) if wait is None else wait)
        return StoredNamesRead(channel=None, held=HeldImages(unread_because=unread))
    except TransportError as exc:
        return StoredNamesRead(channel=None, held=HeldImages(unread_because=f"reading them failed: {exc}"))
    (directory,) = asked.answer
    return StoredNamesRead(channel=asked.channel, held=held_images_from(directory, module=module))


def _still_arriving(wait: AnswerWait) -> str:
    """Say that the names answer did not end within ``wait``, naming the wait as configured.

    The transport's own figure is the wait left plus the ending silence, which no setting holds.

    Example:
        >>> _still_arriving(AnswerWait(seconds=30.0, wait_longer="wait longer with [printer] stored_names_wait"))
        'the answer was still arriving at the end of the 30 s wait; wait longer with [printer] stored_names_wait'
    """
    advice = f"; {wait.wait_longer}" if wait.wait_longer else ""
    return f"the answer was still arriving at the end of the {wait.seconds:g} s wait{advice}"


def with_stored_names(
    render: Callable[[HeldImages | None], _Rendered], *, read: Callable[[ImageModule], HeldImages]
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
    return render_with_stored_names(render, read=read).result


@dataclass(frozen=True)
class RenderedJob(Generic[_Rendered]):
    """A job rendered to be printed, and the stored image names it was rendered with.

    Attributes:
        result: The job to send.
        held: What the printer was read to hold, None when nothing was read: a sender that
            renders a label again on the way starts from the same names.
    """

    result: _Rendered
    held: HeldImages | None


def render_with_stored_names(
    render: Callable[[HeldImages | None], _Rendered], *, read: Callable[[ImageModule], HeldImages]
) -> RenderedJob[_Rendered]:
    """Render as :func:`with_stored_names` does, and also return the names the job was rendered with.

    Returns:
        The job to send and what the printer was read to hold (see :class:`RenderedJob`).
    """
    result = render(None)
    module = _unread_module(result)
    if module is None:
        return RenderedJob(result=result, held=None)
    held = read(module)
    return RenderedJob(result=render(held), held=held)


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


_COMMAND_BYTES: Mapping[PrinterCommand, bytes] = MappingProxyType(
    {
        PrinterCommand.RESET: RESET,
        PrinterCommand.CANCEL: CANCEL,
        PrinterCommand.PAUSE: TOGGLE_PAUSE,
        PrinterCommand.FEED: FORM_FEED,
        PrinterCommand.CUT: CUT_NOW,
    }
)


def send_command(command: PrinterCommand, *, uri: str, timeout: float, open_printer: OpenPrinter) -> PrintReport:
    """Send one immediate or system command (reset, cancel, pause, feed, cut)."""
    return send_bytes(_COMMAND_BYTES[command], uri=uri, timeout=timeout, open_printer=open_printer)


#: Default of ``[printer] status_wait``: seconds ``status`` and ``memory`` keep trying for the first
#: answer. Measured once on a SerComm print server feeding an Allegro 2 at 9600 baud: after a
#: 13.6 KB image job it refused connections for at least 7 s and kept the next one silent past 10 s.
STATUS_WAIT_SECONDS = 30.0
#: Default of ``[printer] stored_names_wait``: seconds ``print`` and ``verify`` keep trying to read
#: the printer's stored image names before a job no clear precedes. Thirty, like status_wait but a
#: key of its own (user decision): one Allegro 2 stayed silent 10-20 s after an image job, so a
#: repeat print right after one still reads the names instead of downloading a held one again.
STORED_NAMES_WAIT_SECONDS = 30.0
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
    answered = _first_answer(
        _ask_status,
        uri=uri,
        timeout=timeout,
        open_printer=open_printer,
        wait=AnswerWait(seconds=timeout) if wait is None else wait,
    )
    answered.channel.close()
    return parse_ascii_status(answered.answer)


def open_within(*, uri: str, timeout: float, open_printer: OpenPrinter, wait: AnswerWait) -> PrinterTransport:
    """Open the printer, again after ``wait.retry_interval`` while a connect takes no request, within ``wait``.

    For a sender whose channel a failed read closed: a print server that got no answer from its
    printer refused the next connection for 7 s and more (one Allegro 2, 2026-11-01), so one
    connect would end the job where waiting as the read does lets it go on.

    Raises:
        PrinterTimeoutError: Every connect was refused (or failed taking no request) until the
            wait ran out; the message counts the attempts and quotes the last failure.
        TransportError: Any failure that is not ``unanswered``, raised as is.
    """
    return _first_answer(
        lambda _channel, _left: None, uri=uri, timeout=timeout, open_printer=open_printer, wait=wait
    ).channel


@dataclass(frozen=True)
class StatusPoll:
    """The status :func:`poll_status` read, and the channel that answered.

    Attributes:
        channel: The channel that answered, the one polled or a new connection, still open.
        status: The printer's status.
    """

    channel: PrinterTransport
    status: PrinterStatus


def poll_status(
    printer: PrinterTransport, *, uri: str, timeout: float, open_printer: OpenPrinter, wait: AnswerWait, started: float
) -> StatusPoll:
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

    answered = _first_answer(_ask_status, uri=uri, timeout=timeout, open_printer=open_again, wait=wait, started=started)
    return StatusPoll(channel=answered.channel, status=parse_ascii_status(answered.answer))


def _ask_status(channel: PrinterTransport, left: float) -> bytes:
    """The status request of every status wait: its answer is waited for as long as the wait has left."""
    return channel.query(STATUS_REQUEST, timeout=left, accept=is_status_line)


@dataclass(frozen=True)
class _Answered(Generic[_T]):
    """What an ask of an open channel returned, and that channel, still open for the caller to close."""

    channel: PrinterTransport
    answer: _T


def _first_answer(
    ask: Callable[[PrinterTransport, float], _T],
    *,
    uri: str,
    timeout: float,
    open_printer: OpenPrinter,
    wait: AnswerWait,
    started: float | None = None,
) -> _Answered[_T]:
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
            return _Answered(channel=printer, answer=_ask_or_close(ask, printer, tries=tries))
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
        if isinstance(error, AnswerNotEndedError):
            # The printer answered: "no answer in N s" would misreport it, and it took the request.
            raise error
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
    answer_within_wait: bool = False,
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
        answer_within_wait: True when the first answer's last byte must also arrive within
            ``wait``, so the wait bounds the read up to the ``idle`` silence that ends it; False
            leaves an answer that began in time the transport's own bound
            (:data:`~datamax_dpl.domain.timing.MAX_ANSWER_SECONDS`).

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
    asked = _ask_directories(
        kinds=kinds,
        uri=uri,
        timeout=timeout,
        open_printer=open_printer,
        idle=idle,
        wait=wait,
        answer_within_wait=answer_within_wait,
    )
    asked.channel.close()
    return asked.answer


def _ask_directories(
    *,
    kinds: Sequence[MemoryKind],
    uri: str,
    timeout: float,
    open_printer: OpenPrinter,
    idle: float,
    wait: AnswerWait | None,
    answer_within_wait: bool,
) -> _Answered[tuple[MemoryDirectory, ...]]:
    """Ask for each directory of ``kinds`` (at least one) as :func:`read_memory_directory` does.

    Returns the channel that answered, still open, and the directories. A failure closes every
    channel it opened and raises as :func:`read_memory_directory` does.
    """
    first, *rest = kinds
    answered = _first_answer(
        lambda channel, left: channel.read_answer(
            MEMORY_DIRECTORY_REQUESTS[first],
            timeout=left,
            idle=idle,
            # The wait bounds the answer's last byte: the silence that ends it comes on top, so a
            # wait no longer than that silence still reads a printer that answers at once.
            total=left + idle if answer_within_wait else MAX_ANSWER_SECONDS,
        ),
        uri=uri,
        timeout=timeout,
        open_printer=open_printer,
        wait=AnswerWait(seconds=timeout) if wait is None else wait,
    )
    printer = answered.channel
    try:
        answers = [answered.answer] + [
            printer.read_answer(MEMORY_DIRECTORY_REQUESTS[kind], timeout=timeout, idle=idle) for kind in rest
        ]
    except BaseException:
        printer.close()
        raise
    directories = tuple(parse_module_directory(raw, kind=kind) for raw, kind in zip(answers, kinds, strict=True))
    return _Answered(channel=printer, answer=directories)


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
    "STORED_NAMES_WAIT_SECONDS",
    "AnswerWait",
    "BatchResult",
    "DetectReport",
    "PrintReport",
    "PrinterCommand",
    "RecordRender",
    "RenderedJob",
    "StatusPoll",
    "StoredNamesRead",
    "detect_printer",
    "open_within",
    "poll_status",
    "print_result",
    "query_status",
    "read_memory_directory",
    "read_stored_names",
    "record_renders",
    "render_batch",
    "render_label",
    "render_with_stored_names",
    "send_bytes",
    "send_command",
    "stored_image_names",
    "with_stored_names",
]
