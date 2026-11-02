"""Envelope payloads: what each label and printer command reports in ``--json`` mode.

DPL bytes travel twice: ``dpl`` shows them readably (``<STX>L<CR>``) for a person or an LLM to
read, ``dpl_base64`` carries them exactly for a caller that wants to send them itself.
"""

from __future__ import annotations

import base64
from collections import Counter
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

from pydantic import BaseModel, ConfigDict

from ...application.use_cases import BatchReport
from ...domain._invariants import require_same_keys
from ...domain.decoder import readable
from ...domain.enums import FindingCode, Severity
from ...domain.units import DplUnit
from ..config.sections import printer_section
from .settings import PrinterSettings

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ...domain.findings import Finding
    from ...domain.render import RenderResult


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FindingOut(_Payload):
    """A finding, with the data record it belongs to in a batch (None for a single label)."""

    code: FindingCode
    severity: Severity
    message: str
    element: int | None
    fallback: str | None
    record: int | None = None

    @classmethod
    def of(cls, finding: Finding, *, record: int | None = None) -> FindingOut:
        """Copy ``finding`` and tag it with its record."""
        return cls(
            code=finding.code,
            severity=finding.severity,
            message=finding.message,
            element=finding.element,
            fallback=finding.fallback,
            record=record,
        )


class RenderPayload(_Payload):
    """Result of ``render``, ``validate`` and ``print``.

    ``findings`` and ``downloads`` hold the first ones of a batch; the totals count them all, and
    ``report_note`` says so when something was left out, naming the key that keeps more.
    """

    model: str
    dpl_unit: DplUnit
    records: int
    fields: int
    bytes: int
    downloads: list[str]
    downloads_total: int
    findings: list[FindingOut]
    findings_total: int
    findings_by_code: dict[FindingCode, int]
    report_note: str | None = None
    dpl: str | None = None
    dpl_base64: str | None = None
    output: str | None = None
    printer: str | None = None
    bytes_sent: int | None = None


class SentTo(NamedTuple):
    """Where a job went, and how many bytes reached it."""

    printer: str
    bytes_sent: int


def _report_note(report: BatchReport, *, settings: PrinterSettings) -> str | None:
    """Say what a report keeps and which key keeps more; None when it left out nothing.

    It speaks of the answer, not of a view: ``print`` shows only part of what the answer keeps, and
    the envelope all of it, and the sentence is true in both.
    """
    section = printer_section(settings.name)
    # The first error stays past the limit, and the batch's own findings always stay.
    kept = len(report.findings) + len(report.batch_findings)
    notes: list[str] = []
    if kept < report.findings_total:
        keep_more = section.setting("max_reported_findings")
        notes.append(f"the answer keeps {kept} of {report.findings_total} findings; {keep_more} keeps more")
    if len(report.downloads) < report.downloads_total:
        keep_more = section.setting("max_reported_downloads")
        kept_names = len(report.downloads)
        notes.append(f"the answer keeps {kept_names} of {report.downloads_total} image names; {keep_more} keeps more")
    return "; ".join(notes) or None


def _figures(result: RenderResult | BatchReport, *, settings: PrinterSettings) -> _Figures:
    """What a payload reports of a single label, whose findings all stay, or of a batch's report."""
    if isinstance(result, BatchReport):
        findings = [FindingOut.of(kept.finding, record=kept.record) for kept in result.findings]
        return _Figures(
            model=result.model,
            dpl_unit=result.dpl_unit,
            records=result.records,
            fields=result.fields,
            job_bytes=result.job_bytes,
            downloads=list(result.downloads),
            downloads_total=result.downloads_total,
            findings=[*findings, *(FindingOut.of(f) for f in result.batch_findings)],
            findings_total=result.findings_total,
            findings_by_code=dict(result.finding_counts),
            note=_report_note(result, settings=settings),
        )
    return _Figures(
        model=result.model,
        dpl_unit=result.dpl_unit,
        records=1,
        fields=result.record_count,
        job_bytes=len(result.data),
        downloads=list(result.downloads),
        downloads_total=len(result.downloads),
        findings=[FindingOut.of(f) for f in result.findings],
        findings_total=len(result.findings),
        findings_by_code=dict(Counter(f.code for f in result.findings)),
        note=None,
    )


def render_payload(
    result: RenderResult | BatchReport,
    *,
    job: bytes | bytearray | None = None,
    with_base64: bool = False,
    output: str | None = None,
    sent: SentTo | None = None,
    settings: PrinterSettings | None = None,
) -> RenderPayload:
    """Build the payload for one render result or a batch of them.

    Args:
        result: What was rendered.
        job: The DPL to put in the payload as text; None shows none.
        with_base64: Whether it goes in as base64 too, which only an envelope shows: on a large
            job that is a third more than the job, for no reader.
        output: The file the DPL was written to, if any.
        sent: The printer the job went to and how many bytes reached it, if anything was sent.
        settings: The merged settings, for the names of the keys that keep more of a batch.
    """
    figures = _figures(result, settings=settings or PrinterSettings())
    return RenderPayload(
        model=figures.model,
        dpl_unit=figures.dpl_unit,
        records=figures.records,
        fields=figures.fields,
        bytes=figures.job_bytes,
        downloads=figures.downloads,
        downloads_total=figures.downloads_total,
        findings=figures.findings,
        findings_total=figures.findings_total,
        findings_by_code=figures.findings_by_code,
        report_note=figures.note,
        dpl=None if job is None else readable(job),
        dpl_base64=base64.b64encode(job).decode("ascii") if job is not None and with_base64 else None,
        output=output,
        printer=None if sent is None else sent.printer,
        bytes_sent=None if sent is None else sent.bytes_sent,
    )


class _Figures(NamedTuple):
    """What a payload reports of a job, however it was rendered."""

    model: str
    dpl_unit: DplUnit
    records: int
    fields: int
    job_bytes: int
    downloads: list[str]
    downloads_total: int
    findings: list[FindingOut]
    findings_total: int
    findings_by_code: dict[FindingCode, int]
    note: str | None


#: Sort rank of each severity in human output: the most severe first.
_SEVERITY_ORDER: Mapping[Severity, int] = MappingProxyType({Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2})
# A new Severity without a rank fails here at import, not as a KeyError in human output only.
require_same_keys(_SEVERITY_ORDER, Severity, what="a rank in _SEVERITY_ORDER")


def payload_findings_text(payload: RenderPayload, findings: list[FindingOut] | None = None) -> str:
    """Human lines for a payload's findings (or just ``findings`` of them), then what the payload left out."""
    text = findings_text(payload.findings if findings is None else findings)
    return "\n".join(part for part in (text, payload.report_note, _counts_of_a_cut(payload)) if part)


def _counts_of_a_cut(payload: RenderPayload) -> str:
    """How many of each code there are, once the answer has cut some: what the lines above cannot say."""
    if len(payload.findings) >= payload.findings_total:
        return ""
    counts = ", ".join(f"{code.value} {count}" for code, count in payload.findings_by_code.items())
    return f"findings by code: {counts}"


#: The infos a person is told about all the same: an extra label, or a shift to expect after a reset.
_TOLD_INFOS = (FindingCode.PRINTER_RESET_CALIBRATED, FindingCode.PRINTER_RESET_NOT_CALIBRATED)


def _worth_telling(finding: FindingOut) -> bool:
    return finding.severity is not Severity.INFO or finding.code in _TOLD_INFOS


def went_out_text(payload: RenderPayload) -> str:
    """The human view of a job that went out: what a reader would otherwise find only on the paper."""
    return payload_findings_text(payload, [f for f in payload.findings if _worth_telling(f)])


def refused_text(payload: RenderPayload) -> str:
    """The human view of a job refused: why."""
    return payload_findings_text(payload, [f for f in payload.findings if f.severity is Severity.ERROR])


def findings_text(findings: list[FindingOut]) -> str:
    """Human lines for findings, most severe first."""
    lines: list[str] = []
    for f in sorted(findings, key=lambda item: _SEVERITY_ORDER[item.severity]):
        where = "" if f.element is None else f" [element {f.element}]"
        record = "" if f.record is None else f" [record {f.record}]"
        fallback = f" -> {f.fallback}" if f.fallback else ""
        lines.append(f"{f.severity.value.upper():7} {f.code.value}{record}{where}: {f.message}{fallback}")
    return "\n".join(lines)


__all__ = [
    "FindingOut",
    "RenderPayload",
    "SentTo",
    "findings_text",
    "payload_findings_text",
    "refused_text",
    "render_payload",
    "went_out_text",
]
