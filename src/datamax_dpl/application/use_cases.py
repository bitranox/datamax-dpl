"""Use cases: render labels (one or a batch of data records), print, query and verify printers.

Every use case takes its collaborators (printer opener, camera, clock) as keyword arguments, so
the CLI wires production adapters and the tests wire in-memory ones.

A job that no clear precedes must not download an image name the printer already holds (it would
print nothing). Nothing about the printer's images is kept from one label to the next: on a two-way
link the sender reads the printer's graphic directory right before each such label's downloads and
renders that label again with what it lists (:mod:`~datamax_dpl.application.upload_check`). Where
no printer can be asked before each label, a batch of such labels is refused from its second
label on (``batch_image_names_unread``); a single label, and a batch's first, keeps every download
and says so.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from itertools import chain
from types import MappingProxyType
from typing import TYPE_CHECKING, Generic, NamedTuple, Protocol, TypedDict, TypeVar

from pydantic import BaseModel, ConfigDict

from ..domain._invariants import require_update_fields
from ..domain.batch import (
    CUT_NOW as BATCH_CUT_BYTES,
)
from ..domain.batch import (
    MAX_REPORTED_DOWNLOADS,
    MAX_REPORTED_FINDINGS,
    NO_CUTS,
    CutPlan,
    cut_plan,
    with_cut,
)
from ..domain.detect import Detection, Evidence, suggest_profiles
from ..domain.enums import FindingCode, ImageModule, MemoryKind, Severity
from ..domain.errors import (
    AnswerNotEndedError,
    BatchTooLargeError,
    ConfigurationError,
    LabelRejectedError,
    PrinterProtocolError,
    PrinterTimeoutError,
    TransportError,
    UnsupportedOperationError,
)
from ..domain.findings import Finding
from ..domain.frozen_mapping import FrozenMapping
from ..domain.held_images import READ_AT_SEND, HeldImages, held_images_from
from ..domain.label import Cut, Image, Label
from ..domain.memory import MemoryDirectory, parse_module_directory
from ..domain.placeholders import fill_record
from ..domain.profiles import get_profile
from ..domain.render import RenderResult, build_job, build_record, iter_jobs
from ..domain.render_context import clear_may_go, option_name, whole_module_clear
from ..domain.reset_label import calibration_job
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
    parse_serial_test,
    parse_version,
)
from ..domain.timing import MAX_ANSWER_SECONDS, TimeoutName, check_timeout

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from ..domain.render_context import RenderOptions
    from ..domain.units import DplUnit
    from .ports import Clock, JobSpool, OpenPrinter, PrinterTransport, Sleep

_T = TypeVar("_T")


class BatchResult(BaseModel):
    """A label rendered once per data record.

    Attributes:
        results: One render result per record.
        batch_findings: Findings about the batch as a whole (cutting across records).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    results: tuple[RenderResult, ...]
    batch_findings: tuple[Finding, ...] = ()
    #: Whether a batch cut (``<STX>o``) follows each record; empty when the batch cuts across none.
    cut_after: tuple[bool, ...] = ()

    # Joined on the first read and not before, and no field: a caller that sends or dumps this goes
    # through ``results`` and ``cut_after``, and a joined copy would double all of the job's memory.
    @cached_property
    def data(self) -> bytes:
        """All jobs back to back, in record order, with any batch cut commands between them."""
        return b"".join(self.record_jobs())

    def record_jobs(self) -> Iterator[bytes]:
        """Each record's bytes as ``data`` holds them, its batch cut included; joined they are ``data``."""
        cuts = self.cut_after or (False,) * len(self.results)
        return (with_cut(r.data, r.closing, cut=after) for r, after in zip(self.results, cuts, strict=True))

    @property
    def job_bytes(self) -> int:
        """How many bytes ``data`` holds, without joining them."""
        return sum(len(job) for job in self.record_jobs())

    @property
    def ok(self) -> bool:
        """True when no record and no batch finding is an error."""
        batch_ok = all(f.severity is not Severity.ERROR for f in self.batch_findings)
        return batch_ok and all(result.ok for result in self.results)


def error_findings(result: RenderResult | BatchResult | BatchReport) -> tuple[Finding, ...]:
    """Every error finding of a job: each label's, and a batch's own; a report has the ones it kept."""
    if isinstance(result, BatchReport):
        return tuple(f for f in result.all_findings() if f.severity is Severity.ERROR)
    if not isinstance(result, BatchResult):
        return result.errors
    found = (f for r in result.results for f in r.errors)
    return (*found, *(f for f in result.batch_findings if f.severity is Severity.ERROR))


def render_label(label: Label, options: RenderOptions, *, held: HeldImages | None = None) -> RenderResult:
    """Render one label and report every finding (never raises for a finding).

    ``held`` is what the printer holds in the image module, None when it was not asked.
    """
    return build_job(label, options, held=held)


def render_batch(
    label: Label,
    records: Iterable[Mapping[str, str]],
    options: RenderOptions,
    *,
    read_names_at_send: bool = False,
    max_job_bytes: int | None = None,
) -> BatchResult:
    """Render ``label`` once per record and hold them all: the convenience over :func:`iter_batch`.

    A large batch goes through :func:`iter_batch` or :func:`check_batch` instead, whose memory does
    not grow with the records. This one must hold every job: it returns them. An error finding does
    not raise: the returned result's ``ok`` is False and its findings name it.

    Args:
        label: The label holding the placeholders.
        records: One mapping of field name to value per label.
        options: The model, fallback policy and job settings.
        read_names_at_send: True when the batch goes to a two-way printer whose stored image names
            are read right before each label's downloads (``print_checked`` does); False refuses
            each record after the first that downloads an image with no clear before it
            (``batch_image_names_unread``), since nothing is kept from one label to the next; the
            first record, which no label of the batch precedes, gets ``image_names_unread`` as a
            single label does.
        max_job_bytes: Most bytes the records' jobs may come to, batch cuts left out. None sets no
            limit, and memory then grows with records times label size (390 PDF417 fields took
            1.1 MB per record), so a library caller rendering records it did not write sets one;
            the ``render`` command with no ``-o`` takes it from ``[printer] max_batch_job_bytes``.

    Returns:
        One render per record and the job they join to; check ``ok`` before sending it.

    Raises:
        BatchTooLargeError: The jobs passed ``max_job_bytes``; the records after that were not rendered.
        MissingFieldError: A record lacks a field a placeholder names.
        UnknownModelError: ``options.model`` names no profile.
        pydantic.ValidationError: A filled value breaks a field limit.
    """
    plan = _plan_of(_batch_label(label), options)
    results: list[RenderResult] = []
    cuts: list[bool] = []
    total = 0
    for entry in _entries(label, records, options, read_names_at_send=read_names_at_send):
        total += len(entry.result.data)
        if max_job_bytes is not None and total > max_job_bytes:
            msg = f"the batch passed {max_job_bytes} bytes of job at data record {len(results)}"
            raise BatchTooLargeError(msg)
        results.append(entry.result)
        cuts.append(entry.cut_after)
    if plan is NO_CUTS:
        return BatchResult(results=tuple(results))
    return BatchResult(results=tuple(results), batch_findings=plan.findings, cut_after=tuple(cuts))


#: No findings beyond those a result holds.
NO_UNKEPT: Mapping[FindingCode, int] = FrozenMapping[FindingCode, int]({})


class BatchEntry(NamedTuple):
    """One record of a batch as first rendered.

    Attributes:
        result: Its render, the batch cut not in its ``data``. One a check pass kept in its spool
            holds in ``findings`` only those the batch's report kept in full (see ``unkept``).
        cut_after: Whether a batch cut (``<STX>o``) follows the record.
        record: The values the record filled the label with, as they were read when it was
            checked: a label rendered again for what the printer answers fills from these, so
            what goes out is what was checked. None where an entry was made without them.
        unkept: How many findings of each code the record has beyond ``result.findings``, which
            the report counted and kept none of in full: a spool keeps their counts, not them.
    """

    result: RenderResult
    cut_after: bool
    record: Mapping[str, str] | None = None
    unkept: Mapping[FindingCode, int] = NO_UNKEPT

    @property
    def job(self) -> bytes:
        """The record's bytes as the batch sends them, its cut included."""
        return with_cut(self.result.data, self.result.closing, cut=self.cut_after)


def _plan_of(batch_label: _BatchLabel, options: RenderOptions) -> CutPlan:
    return NO_CUTS if batch_label.cut is None else cut_plan(batch_label.cut, options=options)


def _with_last(items: Iterable[_T]) -> Iterator[tuple[_T, bool]]:
    """Yield each of ``items`` and whether it is the last, reading one item ahead and no further."""
    source = iter(items)
    try:
        current = next(source)
    except StopIteration:
        return
    for following in source:
        yield current, False
        current = following
    yield current, True


def _entries(
    label: Label, records: Iterable[Mapping[str, str]], options: RenderOptions, *, read_names_at_send: bool
) -> Iterator[BatchEntry]:
    """Render one record at a time, carrying the batch's cuts across them and nothing about the printer."""
    batch_label = _batch_label(label)
    plan = _plan_of(batch_label, options)
    # The current record's values and whether it is the last; iter_jobs takes one label and
    # yields its result before it asks for the next, so this holds one record at a time.
    current: list[tuple[dict[str, str], bool]] = []

    def labels() -> Iterator[Label]:
        for number, (record, last) in enumerate(_with_last(records)):
            # Read once, here: the render and what the send fills again both take these values.
            values = dict(record)
            current.append((values, last))
            yield fill_record(batch_label.per_record, values, number=number)

    held = READ_AT_SEND if read_names_at_send else None
    for number, result in enumerate(iter_jobs(labels(), options, held=held), start=1):
        # The first record has no label before it in the batch that could have left a name or
        # cleared an image, so it is judged as a single label is.
        checked = (
            result
            if number == 1
            else _judged_after_the_first(result, batch_label.per_record, options, read_names_at_send=read_names_at_send)
        )
        values, last = current.pop()
        yield BatchEntry(result=checked, cut_after=plan.follows(number, last=last), record=values)


def _judged_after_the_first(
    result: RenderResult, label: Label, options: RenderOptions, *, read_names_at_send: bool
) -> RenderResult:
    """``result``, a record after the first, with what an earlier label of the batch makes it refuse.

    Where the printer is read before each label, the names it holds are known at send, so only a
    stored image the clear after an earlier label removed stays a refusal.
    """
    unread = result if read_names_at_send else _refused_unread(result, options)
    return _refused_stored_cleared(unread, label, options)


class _FindingsUpdate(TypedDict):
    """The field a batch record's result changes when the batch refuses it."""

    findings: tuple[Finding, ...]


require_update_fields(_FindingsUpdate, RenderResult)


def _refused_unread(result: RenderResult, options: RenderOptions) -> RenderResult:
    """``result``, a record after the first, with ``batch_image_names_unread`` where its names would have to be known.

    A label with no clear before its downloads must leave out each name the printer holds, and an
    earlier label of the same batch may have left any of them: only a read of the printer right
    before the label can tell, and nothing is kept from one label to the next to stand in for it.
    """
    if not result.names_unread or result.image_module is None:
        return result
    module = result.image_module.value
    refusal = Finding(
        code=FindingCode.BATCH_IMAGE_NAMES_UNREAD,
        severity=Severity.ERROR,
        message=(
            f"the label downloads images into module {module} with no clear before them, and here no printer can "
            "be asked which names it holds before each label: an earlier label of the batch may leave a name this "
            "one downloads, and a download of a held name prints nothing. Send the batch to a two-way printer "
            f"(tcp or serial), which is read before each label, or {_way_to_clear(result.image_module, options)}"
        ),
    )
    return result.model_copy(update=_FindingsUpdate(findings=(*result.findings, refusal)))


def _way_to_clear(module: ImageModule, options: RenderOptions) -> str:
    """How ``module``, where the label's images go, gets the clear before each label that makes the names known.

    Each way it names is one :func:`~datamax_dpl.domain.render_context.clear_may_go` allows, the
    test the clear itself makes, so following the advice cannot meet the same refusal again.
    """
    profile = get_profile(options.model)

    def clears(target: ImageModule) -> bool:
        return clear_may_go(profile, target, firmware=options.firmware, options=options.options)

    before = option_name("clear_graphics_before_label")
    if clears(module):
        return f"turn on the clear before each label ({before})"
    built_in = profile.image_module
    if built_in is not None and built_in != module and clears(built_in):
        return (
            f"leave the image module unset ({option_name('image_module')}), so the images go to module "
            f"{built_in.value}, the model's built-in RAM, which the clear before each label ({before}) empties; "
            f"module {module.value} takes no clear, as it may be flash"
        )
    return "use a model that can clear its image module: this one has no clear for it"


def _refused_stored_cleared(result: RenderResult, label: Label, options: RenderOptions) -> RenderResult:
    """``result``, a record after the first, refusing each stored image the clear after the label before removed.

    The clear after each label empties the whole module (``<STX>q``) once the label printed, so a
    stored image (no bitmap) the first label placed is gone for every label after it: the printer
    then prints the rest of the format with no image and no error (an Allegro 2, measured
    2026-11-01). The plain Prodigy's clear after removes only the names its label downloaded, so
    an image the printer stores survives it. The domain says which images must be stored
    (``image_must_be_stored``); with the clear before on, it refuses them itself.
    """
    if not options.clear_graphics_after_label:
        return result
    profile = get_profile(options.model)
    if not whole_module_clear(profile, result.image_module, firmware=options.firmware, options=options.options):
        return result
    stored = [
        f.element for f in result.findings if f.code is FindingCode.IMAGE_MUST_BE_STORED and f.element is not None
    ]
    if not stored or result.image_module is None:
        return result
    module = result.image_module.value
    after = option_name("clear_graphics_after_label")
    before = option_name("clear_graphics_before_label")
    refusals = tuple(
        Finding(
            code=FindingCode.STORED_IMAGE_CLEARED,
            severity=Severity.ERROR,
            message=(
                f"{_image_name(label, element)} has no bitmap, and the clear after the batch's previous label "
                f"({after}) cleared module {module}, which removes every image stored there; give the bitmap, or "
                f"turn off both clears ({before}, {after}) to print an image the printer stores across a batch"
            ),
            element=element,
        )
        for element in stored
    )
    return result.model_copy(update=_FindingsUpdate(findings=(*result.findings, *refusals)))


def _image_name(label: Label, element: int) -> str:
    """``image NAME`` for the image at ``element`` of ``label``, as the domain's messages name one."""
    image = label.elements[element]
    return f"image {image.name}" if isinstance(image, Image) else f"element {element}"


class _DataAndFindingsUpdate(TypedDict):
    """The fields a record's result changes when :func:`iter_batch` hands it out."""

    data: bytes
    findings: tuple[Finding, ...]


require_update_fields(_DataAndFindingsUpdate, RenderResult)


def iter_batch(
    label: Label, records: Iterable[Mapping[str, str]], options: RenderOptions, *, read_names_at_send: bool = False
) -> Iterator[RenderResult]:
    """Render ``label`` once per record, one at a time: memory does not grow with the records.

    Only the current record and the one read ahead (to know the last, for a cut at the end) are
    held, and any iterable does, a generator included. Each result's ``data`` is what to send for
    that record, its batch cut included. Nothing about the printer carries across records, as in
    :func:`render_batch`, whose jobs and findings these are, except that findings about the batch
    as a whole come on the first record's result: stop at the first result that is not ``ok``.

    To refuse a batch with any bad record before sending any of it, go over it once first:
    :func:`check_batch` does.

    Args:
        label: The label holding the placeholders.
        records: One mapping of field name to value per label.
        options: The model, fallback policy and job settings.
        read_names_at_send: True when a sender reads the printer's stored image names right
            before each label's downloads (see :func:`render_batch`).

    Yields:
        One render per record, in order.

    Raises:
        MissingFieldError: A record lacks a field a placeholder names, when its turn comes.
        UnknownModelError: ``options.model`` names no profile.
        pydantic.ValidationError: A filled value breaks a field limit, when its turn comes.

    Example:
        >>> from datamax_dpl.domain.label import Text
        >>> from datamax_dpl.domain.render_context import RenderOptions as Options
        >>> label = Label(elements=[Text(x=5, y=5, text="{name}")])
        >>> [b"Ann" in r.data for r in iter_batch(label, iter([{"name": "Ann"}]), Options(model="dmx-600"))]
        [True]
    """
    extra = _plan_of(_batch_label(label), options).findings
    for entry in _entries(label, records, options, read_names_at_send=read_names_at_send):
        findings = (*entry.result.findings, *extra)
        extra = ()
        yield entry.result.model_copy(update=_DataAndFindingsUpdate(data=entry.job, findings=findings))


class ReportLimits(NamedTuple):
    """How much a :class:`BatchReport` keeps of what can grow with the records.

    Attributes:
        findings: Findings kept in full; past that the first of each code, and of the rest their
            counts. The first error is always kept. So what is kept is bounded by the codes that exist.
        downloads: Downloaded image names kept; past that only their total.
    """

    findings: int = MAX_REPORTED_FINDINGS
    downloads: int = MAX_REPORTED_DOWNLOADS


DEFAULT_REPORT_LIMITS = ReportLimits()


class ReportedFinding(NamedTuple):
    """A finding a report kept, and the record it belongs to, counted from 0.

    Attributes:
        record: The record's number.
        finding: The finding.
    """

    record: int
    finding: Finding


@dataclass(frozen=True)
class BatchJob:
    """What it takes to send a checked batch: its records are read again, never held.

    Attributes:
        label: The label holding the placeholders.
        records: The records, read again by a send with no spool; never read again with one.
        options: How the records were rendered.
        read_names_at_send: Whether the printer's names are read before each label's downloads.
        limits: How much of what grows with the records the report keeps.
        spool: Every record as the check rendered it, with its values, which the send reads
            instead of the records; None renders each record again from them.
    """

    label: Label
    records: Iterable[Mapping[str, str]]
    options: RenderOptions
    read_names_at_send: bool
    limits: ReportLimits
    spool: JobSpool | None = None


def unlisted_download(result: RenderResult) -> bool:
    """Whether ``result`` downloads into module C, the default module no directory lists by that letter."""
    return bool(result.downloads) and result.image_module == ImageModule.C


@dataclass(frozen=True)
class BatchReport:
    """What a batch comes to, without its jobs: its size is bounded, however many records it has.

    The counterpart of :class:`BatchResult` for a batch too large to hold: :func:`check_batch`
    makes one, and :meth:`entries`, :meth:`record_jobs` and :meth:`renders` give its records one at
    a time: read back from the check's spool where it kept one, else rendered again from the same
    records.

    Attributes:
        job: How to render the records again; None for the report of a batch held whole.
        limits: How much of what grows with the records it keeps.
        model: Profile id the jobs were built for.
        dpl_unit: Distance unit the formats use.
        records: How many records there are.
        fields: How many field records their formats hold together.
        job_bytes: How many bytes the jobs come to, batch cuts included.
        ok: True when no finding is an error, whether or not its text was kept.
        downloads: The image names downloaded, in order, the first ``limits.downloads`` of them.
        downloads_total: How many there are in all.
        findings: The findings kept, in order: the first ``limits.findings``, past those the first
            of each code, and the first error.
        batch_findings: The findings about the batch as a whole; always kept.
        finding_counts: How many findings of each code there are in all.
        findings_total: How many findings there are in all.
        plan: Where the batch cuts across its records.
    """

    job: BatchJob | None
    limits: ReportLimits
    model: str
    dpl_unit: DplUnit
    records: int
    fields: int
    job_bytes: int
    ok: bool
    downloads: tuple[str, ...]
    downloads_total: int
    findings: tuple[ReportedFinding, ...]
    batch_findings: tuple[Finding, ...]
    finding_counts: Mapping[FindingCode, int]
    findings_total: int
    plan: CutPlan

    def _job(self) -> BatchJob:
        if self.job is None:
            msg = "the report of a batch held whole renders nothing again: its result holds the jobs"
            raise ValueError(msg)
        return self.job

    def all_findings(self) -> Iterator[Finding]:
        """The findings kept, a record's first, then the batch's own."""
        return chain((kept.finding for kept in self.findings), self.batch_findings)

    def _sendable(self) -> BatchJob:
        """How to send the batch, which only one that passed its check has.

        Raises:
            LabelRejectedError: The batch was refused when it was checked; its errors are named.
            ValueError: This is the report of a batch held whole.
        """
        job = self._job()
        if not self.ok:
            msg = "the batch was refused when it was checked, so none of it is sent"
            raise LabelRejectedError(msg, error_findings(self))
        return job

    def entries(self) -> Iterator[BatchEntry]:
        """The records one at a time, as they were checked: from the spool, else rendered again.

        Raises:
            LabelRejectedError: The batch was refused when it was checked.
        """
        job = self._sendable()
        if job.spool is not None:
            return job.spool.entries()
        return _entries(job.label, job.records, job.options, read_names_at_send=job.read_names_at_send)

    def record_jobs(self) -> Iterator[bytes]:
        """Each record's bytes in turn, its batch cut included, refusing records that are not the ones checked.

        From the spool these are the bytes checked. Rendered again, nothing else stands between the
        second read of the records and the wire on a one-way link, so a record that has gone bad, or
        one more or fewer than were checked, stops the job here.

        Yields:
            The next record's job.

        Raises:
            LabelRejectedError: The batch was refused when it was checked, before anything is
                yielded; or a record now has an error finding, which :attr:`findings` of the
                exception name, after what came before it has been yielded.
            ConfigurationError: The records now come to another number than were checked.
            MissingFieldError: A record now lacks a field a placeholder names.
            pydantic.ValidationError: A filled value now breaks a field limit.
        """
        count = 0
        for entry in self.entries():
            count += 1
            if count > self.records:
                break
            if not entry.result.ok:
                msg = f"data record {count - 1} has errors it did not have when it was checked"
                raise LabelRejectedError(msg, error_findings(entry.result))
            yield entry.job
        if count != self.records:
            msg = f"the batch was checked with {self.records} records and now reads another number"
            raise ConfigurationError(msg)

    def renders(self) -> Iterator[RecordRender]:
        """How to render each label for a checked send (``CheckedJob.renders``), one at a time.

        With a spool each label's first render is the one checked, read back, and a label rendered
        again (for what a read of the printer lists, a clear, or halved images) fills from the
        values that were checked: the records are not read again. Without one each label is
        rendered from the records, read again.

        Raises:
            LabelRejectedError: The batch was refused when it was checked.
        """
        job = self._sendable()
        per_record = _batch_label(job.label).per_record
        if job.spool is not None:
            return _spooled(per_record, job.options, job.spool.entries())
        return (
            _RecordFillRender(label=per_record, record=record, number=number, options=job.options)
            for number, record in enumerate(job.records)
        )

    def cuts(self) -> Iterator[bool]:
        """For each record in turn, whether a batch cut follows it."""
        return (self.plan.follows(number, last=number == self.records) for number in range(1, self.records + 1))


def _spooled(per_record: Label, options: RenderOptions, entries: Iterator[BatchEntry]) -> Iterator[RecordRender]:
    """Each record's checked render from the spool, which renders it again from the values kept with it.

    Raises:
        ConfigurationError: A spooled record holds no values (a spool that does not keep them).
    """
    for number, entry in enumerate(entries):
        if entry.record is None:
            msg = f"the spool gave back data record {number} without its values, which a send renders it again from"
            raise ConfigurationError(msg)
        fill = _RecordFillRender(label=per_record, record=entry.record, number=number, options=options)
        yield _SpooledRender(fill=fill, checked=entry.result, unkept_counts=entry.unkept)


def report_of(result: BatchResult, *, limits: ReportLimits = DEFAULT_REPORT_LIMITS) -> BatchReport:
    """The report of a batch held whole, so that both kinds of batch report alike.

    Raises:
        ValueError: The batch holds no record.
    """
    tally = BatchTally(limits, plan=CutPlan(every=None, at_end=False, findings=result.batch_findings))
    for record, cut in zip(result.results, result.cut_after or (False,) * len(result.results), strict=True):
        tally.add(record, cut=cut)
    return tally.report(None)


class BatchTally:
    """Adds up a batch record by record into a :class:`BatchReport`, keeping what ``limits`` allows."""

    def __init__(self, limits: ReportLimits, *, plan: CutPlan) -> None:
        """Start empty, with ``plan``'s findings as the batch's own."""
        self.records = 0
        self._limits = limits
        self._plan = plan
        self._first: RenderResult | None = None
        self._fields = 0
        self._bytes = 0
        self._ok = True
        self._downloads: list[str] = []
        self._downloads_total = 0
        self._findings: list[ReportedFinding] = []
        self._error_kept = False
        self._codes_kept: set[FindingCode] = set()
        self._counts: dict[FindingCode, int] = {}
        self._findings_total = 0
        for finding in plan.findings:
            self._count(finding)

    @property
    def ok(self) -> bool:
        """True while no finding added is an error."""
        return self._ok

    def _count(self, finding: Finding) -> None:
        self._findings_total += 1
        self._counts[finding.code] = self._counts.get(finding.code, 0) + 1
        self._ok = self._ok and finding.severity is not Severity.ERROR

    def _keep(self, finding: Finding, *, record: int) -> bool:
        """Keep ``finding`` while there is room, and past that the first of its code.

        Past the room a code seen for the first time is what a reader cannot get from the counts: an
        Allegro 2 gives three infos per record, so the first findings alone would fill any limit before
        the record whose warning says what prints wrong. The first error is kept whatever its code, as
        a refusal's reason.
        """
        error = finding.severity is Severity.ERROR
        room = len(self._findings) < self._limits.findings
        if room or finding.code not in self._codes_kept or (error and not self._error_kept):
            self._findings.append(ReportedFinding(record=record, finding=finding))
            self._codes_kept.add(finding.code)
            self._error_kept = self._error_kept or error
            return True
        return False

    def add(
        self, result: RenderResult, *, cut: bool, unkept: Mapping[FindingCode, int] = NO_UNKEPT
    ) -> tuple[Finding, ...]:
        """Add the next record, ``cut`` when a batch cut follows it.

        Args:
            result: The record's render.
            cut: Whether a batch cut follows it.
            unkept: How many findings of each code it has beyond ``result.findings``, counted and
                not kept (see :attr:`BatchEntry.unkept`); none of them is an error.

        Returns:
            Those of the record's findings this report keeps in full, in order.
        """
        record = self.records
        self.records += 1
        self._first = self._first or result
        self._fields += result.record_count
        self._bytes += len(result.data) + (len(BATCH_CUT_BYTES) if cut else 0)
        kept: list[Finding] = []
        for finding in result.findings:
            self._count(finding)
            if self._keep(finding, record=record):
                kept.append(finding)
        for code, count in unkept.items():
            self._findings_total += count
            self._counts[code] = self._counts.get(code, 0) + count
        self._downloads_total += len(result.downloads)
        self._downloads.extend(result.downloads[: self._limits.downloads - len(self._downloads)])
        return tuple(kept)

    def report(self, job: BatchJob | None) -> BatchReport:
        """Return the report of what was added, ``job`` being how to render it again.

        Raises:
            ValueError: Nothing was added: a batch with no record has no model to report.
        """
        if self._first is None:
            msg = "a batch needs at least one record"
            raise ValueError(msg)
        return BatchReport(
            job=job,
            limits=self._limits,
            model=self._first.model,
            dpl_unit=self._first.dpl_unit,
            records=self.records,
            fields=self._fields,
            job_bytes=self._bytes,
            ok=self._ok,
            downloads=tuple(self._downloads),
            downloads_total=self._downloads_total,
            findings=tuple(self._findings),
            batch_findings=self._plan.findings,
            finding_counts=dict(self._counts),
            findings_total=self._findings_total,
            plan=self._plan,
        )


def check_batch(
    label: Label,
    records: Iterable[Mapping[str, str]],
    options: RenderOptions,
    *,
    read_names_at_send: bool = False,
    limits: ReportLimits = DEFAULT_REPORT_LIMITS,
    each: Callable[[BatchEntry], None] | None = None,
    spool: JobSpool | None = None,
) -> BatchReport:
    """Go over a whole batch once, one record at a time, and report it without keeping its jobs.

    A batch with any bad record is refused before any of it goes out, so a sender calls this first
    and only then sends the records from the report (:meth:`BatchReport.renders`,
    :meth:`BatchReport.record_jobs`): read back from ``spool`` where one is given, else rendered a
    second time.

    Args:
        label: The label holding the placeholders.
        records: One mapping of field name to value per label. With ``spool`` they are read once,
            here, and may be a generator. Without one they must be iterable again (a list, or an
            object whose ``__iter__`` starts over) and yield the same records each time: sending
            reads them again, and what it reads then was never checked. One that has gone bad or
            changed in number is refused when its turn comes, after the records before it went out.
        options: The model, fallback policy and job settings.
        read_names_at_send: True when a sender reads the printer's stored image names right
            before each label's downloads (see :func:`render_batch`).
        limits: How much of what grows with the records the report keeps.
        each: Called with each record as it is rendered, for a caller writing the job as it goes.
        spool: Where each record is kept as it is rendered, with its values, until the first in
            error; sealed at the end, and the report sends from it alone, never reading the
            records again, only when every record passed.

    Returns:
        The report; check ``ok`` before sending. An error finding does not raise.

    Raises:
        MissingFieldError: A record lacks a field a placeholder names.
        UnknownModelError: ``options.model`` names no profile.
        ValueError: ``records`` can be read only once and no ``spool`` is given, or holds no record.
        pydantic.ValidationError: A filled value breaks a field limit.
        OSError: ``spool`` could not keep a record of a batch that passed (a full disk). One that
            fails on a refused batch is not raised: the refusal is the answer, and nothing is sent.
    """
    # Asked of the type: calling iter() on a re-iterable would be one read of its source before the check.
    if spool is None and isinstance(records, Iterator):
        msg = (
            "check_batch with no spool reads the records again, and a generator can be read only once; "
            "give it a spool, or use iter_batch"
        )
        raise ValueError(msg)
    tally = BatchTally(limits, plan=_plan_of(_batch_label(label), options))
    keeping = _Keeping(spool)
    for entry in _entries(label, records, options, read_names_at_send=read_names_at_send):
        kept = tally.add(entry.result, cut=entry.cut_after)
        if each is not None:
            each(entry)
        # A refused batch sends nothing, so nothing past its first error is worth the disk.
        if tally.ok:
            keeping.add(_as_kept(entry, kept))
    keeping.seal(ok=tally.ok)
    job = BatchJob(
        label=label,
        records=records,
        options=options,
        read_names_at_send=read_names_at_send,
        limits=limits,
        spool=spool if tally.ok else None,
    )
    return tally.report(job)


def _as_kept(entry: BatchEntry, kept: tuple[Finding, ...]) -> BatchEntry:
    """``entry`` as a spool keeps it: of its findings ``kept`` in full, as the report keeps them, the rest as counts.

    A warning on every field of every record would otherwise make the spool many times the job,
    for findings the report holds only counts of past its first ones.
    """
    findings = entry.result.findings
    if len(kept) == len(findings):
        return entry
    unkept: dict[FindingCode, int] = {}
    left = iter(kept)
    following = next(left, None)
    for finding in findings:
        if finding is following:
            following = next(left, None)
            continue
        unkept[finding.code] = unkept.get(finding.code, 0) + 1
    result = entry.result.model_copy(update=_FindingsUpdate(findings=kept))
    return entry._replace(result=result, unkept=FrozenMapping(unkept))


class _Keeping:
    """A check pass's spool, which stops at its first failure so that the check can still finish.

    The verdict comes first: a batch with a bad record is refused whether or not its spool filled
    the disk on the way, and only a batch that passed, which would have been sent, needs the spool.
    """

    def __init__(self, spool: JobSpool | None) -> None:
        self._spool = spool
        self._failed: OSError | None = None

    def add(self, entry: BatchEntry) -> None:
        if self._spool is None or self._failed is not None:
            return
        try:
            self._spool.add(entry)
        except OSError as exc:
            self._failed = exc

    def seal(self, *, ok: bool) -> None:
        """Seal the spool, and raise its failure if the batch passed.

        Raises:
            OSError: The spool could not keep every record of a batch that passed.
        """
        if self._spool is not None and self._failed is None:
            try:
                self._spool.seal()
            except OSError as exc:
                self._failed = exc
        if ok and self._failed is not None:
            raise self._failed


class _CutUpdate(TypedDict):
    """The field a batch that cuts across records replaces on each record's label."""

    cut: Cut | None


require_update_fields(_CutUpdate, Label)


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
    """Render one label of a job, as first sent and again for what the printer holds; see :func:`record_renders`."""

    def first(self) -> RenderResult:
        """Return the label as it is first sent: with its stored names to be read before its downloads."""
        ...

    def __call__(self, held: HeldImages, *, halve: bool) -> RenderResult:
        """Return the label rendered for what the printer holds; ``halve`` halves its images."""
        ...

    def calibration(self, text: str) -> RenderResult:
        """Return the calibration label for this label: its settings and size, with ``text`` in the middle."""
        ...


def record_renders(
    label: Label, records: Sequence[Mapping[str, str]] | None, options: RenderOptions
) -> tuple[RecordRender, ...]:
    """Return how to render each label of a job again, as :func:`render_label` or :func:`render_batch` rendered it.

    A sender that checks each label's image upload renders a label again with what a read of the
    printer then lists, with the module known empty after a clear, or with its images halved.

    Every record is filled once here so that a bad one raises before anything goes out, and the
    filled label is dropped: each render fills its label again when it runs. The tuple holds one
    object per record; for a large batch :meth:`BatchReport.renders` yields them one at a time.

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
        return (_LabelRender(label=label, options=options),)
    per_record = _batch_label(label).per_record
    for number, record in enumerate(records):
        fill_record(per_record, record, number=number)
    return tuple(
        _RecordFillRender(label=per_record, record=record, number=number, options=options)
        for number, record in enumerate(records)
    )


@dataclass(frozen=True)
class _LabelRender:
    """How to render one label of a job again, and the calibration label that may go before it."""

    label: Label
    options: RenderOptions

    def first(self) -> RenderResult:
        """Render the label as it is first sent, its stored names to be read before its downloads."""
        return self(READ_AT_SEND, halve=False)

    def __call__(self, held: HeldImages, *, halve: bool) -> RenderResult:
        """Render the label for what the printer holds, its images halved when ``halve``."""
        return build_record(self.label, self.options, held=held, halve=halve)

    def calibration(self, text: str) -> RenderResult:
        """Render the calibration label for this label."""
        return calibration_job(self.label, self.options, text=text)


@dataclass(frozen=True)
class _RecordFillRender:
    """:class:`_LabelRender` for one data record, filling the shared label only when it renders."""

    label: Label
    record: Mapping[str, str]
    number: int
    options: RenderOptions

    def _filled(self) -> Label:
        return fill_record(self.label, self.record, number=self.number)

    def first(self) -> RenderResult:
        """Fill the label and render it as it is first sent, its stored names to be read before its downloads."""
        return self(READ_AT_SEND, halve=False)

    def __call__(self, held: HeldImages, *, halve: bool) -> RenderResult:
        """Fill the label and render it for what the printer holds, its images halved when ``halve``."""
        return build_record(self._filled(), self.options, held=held, halve=halve)

    def calibration(self, text: str) -> RenderResult:
        """Fill the label and render the calibration label for it."""
        return calibration_job(self._filled(), self.options, text=text)


@dataclass(frozen=True)
class _SpooledRender:
    """:class:`_RecordFillRender` whose first render is the one the check kept, read back, not made again."""

    fill: _RecordFillRender
    checked: RenderResult
    unkept_counts: Mapping[FindingCode, int]

    def first(self) -> RenderResult:
        """Return the label as the check rendered it, its stored names to be read before its downloads."""
        return self.checked

    def __call__(self, held: HeldImages, *, halve: bool) -> RenderResult:
        """Fill the label and render it for what the printer holds, its images halved when ``halve``."""
        return self.fill(held, halve=halve)

    def calibration(self, text: str) -> RenderResult:
        """Fill the label and render the calibration label for it."""
        return self.fill.calibration(text)


def unkept_of(render: RecordRender) -> Mapping[FindingCode, int]:
    """The counts of findings ``render``'s first render has beyond its ``findings`` (see :attr:`BatchEntry.unkept`).

    Only a render read back from a spool (:meth:`BatchReport.renders`) can have any: the spool kept
    the rest as counts. Every other one, a caller's own :class:`RecordRender` included, holds all
    its findings, so the counts stay off the protocol a caller implements.
    """
    return render.unkept_counts if isinstance(render, _SpooledRender) else NO_UNKEPT


#: Why the stored names are unknown on a channel that cannot read the printer's answer.
ONE_WAY_LINK = "the link to the printer is one-way"


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

    Never raises for a printer that cannot say: a one-way channel, no answer in time or an answer
    the parser cannot read gives unknown names with the reason. It reads on a channel the caller
    may already have open: a sender asks right before a label's downloads, and again once they went
    out, on the connection the job goes out on, and goes on with the job there. A channel that fails
    before it takes the request is replaced by a new one within ``wait``, as :func:`query_status`
    replaces its own.

    Args:
        printer: The open channel to ask first, or None to open one with ``open_printer``.
        module: The job's image module.
        uri: The printer.
        timeout: Seconds for each connect and write.
        open_printer: Opens a channel when there is none, or the one asked took no request.
        wait: How long to keep trying for the answer, as ``memory`` does, and by when its last
            byte must have arrived: an answer still coming when the wait ends counts as no
            answer. The silence that ends the answer (:data:`DIRECTORY_IDLE_SECONDS`) comes on
            top. None waits ``timeout``.

    Returns:
        The channel that answered and what it says ``module`` holds (see :class:`StoredNamesRead`).
    """
    try:
        asked = _ask_directories(
            kinds=(MemoryKind.GRAPHIC,),
            uri=uri,
            timeout=timeout,
            open_printer=open_printer if printer is None else _open_channel_first(printer, open_printer),
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
    result: RenderResult | BatchResult | BatchReport, *, uri: str, timeout: float, open_printer: OpenPrinter
) -> PrintReport:
    """Send a rendered job, refusing one that has error findings; a batch goes one record's bytes at a time.

    Raises:
        LabelRejectedError: The job has an error finding; nothing was sent.
        TransportError: The printer could not be reached or written.
    """
    if not result.ok:
        msg = "label has errors; nothing was sent"
        raise LabelRejectedError(msg, error_findings(result))
    if isinstance(result, RenderResult):
        return send_bytes(result.data, uri=uri, timeout=timeout, open_printer=open_printer)
    sent = 0
    with closing(open_printer(uri, timeout=timeout)) as printer:
        for job in result.record_jobs():
            printer.send(job)
            sent += len(job)
    return PrintReport(uri=uri, bytes_sent=sent)


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


def read_reset_report(printer: PrinterTransport, *, wait: float) -> bool:
    """Ask the open ``printer`` for its status once and say whether the answer carried a reset report.

    Runs before every job on a two-way link, so it is one try on the channel the job goes out on: a
    new connection could find a print server that refuses it after a silent printer (see
    :func:`open_within`), which would end a job this read has no say in. No answer, or one that
    cannot be read, means nothing and is no failure, on a channel that cannot answer at all too; an
    answer that comes later is one the transports drop before their next request.

    Args:
        printer: The open channel.
        wait: Seconds to wait for the whole answer (``[printer] reset_check_wait``).

    Returns:
        True when ``R`` or ``T`` stood in front of a status answer; False in every other case.
    """
    try:
        return parse_ascii_status(_ask_status(printer, wait)).reset_reported
    except TransportError:
        return False


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
        PrinterProtocolError: The answer was not a status string; a new connection is then closed,
            ``printer`` stays the caller's.
        TransportError: Any other failure, raised as is.
    """
    answered = _first_answer(
        _ask_status,
        uri=uri,
        timeout=timeout,
        open_printer=_open_channel_first(printer, open_printer),
        wait=wait,
        started=started,
    )
    try:
        return StatusPoll(channel=answered.channel, status=parse_ascii_status(answered.answer))
    except PrinterProtocolError:
        # The caller gets no channel from a raise, so nobody else can close the one opened here.
        if answered.channel is not printer:
            answered.channel.close()
        raise


def _open_channel_first(printer: PrinterTransport, open_printer: OpenPrinter) -> OpenPrinter:
    """An opener whose first attempt is the channel already open, as a retried request needs one."""
    first = [printer]

    def open_once(uri: str, *, timeout: float) -> PrinterTransport:
        # Only a failure that took no request opens another.
        return first.pop() if first else open_printer(uri, timeout=timeout)

    return open_once


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
    check_timeout(wait.seconds, name=TimeoutName.WAIT)
    check_timeout(wait.retry_interval, name=TimeoutName.RETRY_INTERVAL)
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
            An answer that began in time has the transport's own bound
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
    check_timeout(idle, name=TimeoutName.IDLE)
    if not kinds:
        return ()
    asked = _ask_directories(
        kinds=kinds,
        uri=uri,
        timeout=timeout,
        open_printer=open_printer,
        idle=idle,
        wait=wait,
        answer_within_wait=False,
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

    ``answer_within_wait`` True makes the first answer's last byte arrive within ``wait`` too, so
    the wait bounds the read up to the ``idle`` silence that ends it.

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
        serial_test=None if serial is None else parse_serial_test(serial),
        version=None if version is None else parse_version(version),
        memory=None if memory is None else parse_memory_report(memory),
    )
    return DetectReport(evidence=evidence, detection=suggest_profiles(evidence))


__all__ = [
    "BUSY_HINT",
    "DIRECTORY_IDLE_SECONDS",
    "NO_UNKEPT",
    "ONE_WAY_LINK",
    "RETRY_INTERVAL_SECONDS",
    "STATUS_WAIT_SECONDS",
    "STORED_NAMES_WAIT_SECONDS",
    "AnswerWait",
    "BatchEntry",
    "BatchJob",
    "BatchReport",
    "BatchResult",
    "BatchTally",
    "DetectReport",
    "PrintReport",
    "PrinterCommand",
    "RecordRender",
    "ReportLimits",
    "ReportedFinding",
    "StatusPoll",
    "StoredNamesRead",
    "check_batch",
    "detect_printer",
    "error_findings",
    "iter_batch",
    "open_within",
    "poll_status",
    "print_result",
    "query_status",
    "read_memory_directory",
    "read_reset_report",
    "read_stored_names",
    "record_renders",
    "render_batch",
    "render_label",
    "report_of",
    "send_bytes",
    "send_command",
    "unkept_of",
    "unlisted_download",
]
