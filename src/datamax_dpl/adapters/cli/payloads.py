"""Envelope payloads: what each label and printer command reports in ``--json`` mode.

DPL bytes travel twice: ``dpl`` shows them readably (``<STX>L<CR>``) for a person or an LLM to
read, ``dpl_base64`` carries them exactly for a caller that wants to send them itself.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ...application.use_cases import BatchResult
from ...domain.decoder import readable

if TYPE_CHECKING:
    from ...domain.findings import Finding
    from ...domain.render import RenderResult


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FindingOut(_Payload):
    """A finding, with the data record it belongs to in a batch (None for a single label)."""

    code: str
    severity: str
    message: str
    element: int | None
    fallback: str | None
    record: int | None = None

    @classmethod
    def of(cls, finding: Finding, *, record: int | None = None) -> FindingOut:
        """Copy ``finding`` and tag it with its record."""
        return cls(record=record, **finding.model_dump(mode="json"))


class RenderPayload(_Payload):
    """Result of ``render``, ``validate`` and ``print``."""

    model: str
    dpl_unit: str
    records: int
    fields: int
    bytes: int
    downloads: list[str]
    findings: list[FindingOut]
    dpl: str | None = None
    dpl_base64: str | None = None
    output: str | None = None
    printer: str | None = None
    bytes_sent: int | None = None


def render_payload(result: RenderResult | BatchResult, *, include_dpl: bool) -> RenderPayload:
    """Build the payload for one render result or a batch of them."""
    results = result.results if isinstance(result, BatchResult) else (result,)
    batch = isinstance(result, BatchResult)
    findings = [FindingOut.of(f, record=i if batch else None) for i, r in enumerate(results) for f in r.findings]
    if isinstance(result, BatchResult):
        findings += [FindingOut.of(f) for f in result.batch_findings]
    first = results[0]
    return RenderPayload(
        model=first.model,
        dpl_unit=first.dpl_unit.value,
        records=len(results),
        fields=sum(r.record_count for r in results),
        bytes=len(result.data),
        downloads=[name for r in results for name in r.downloads],
        findings=findings,
        dpl=readable(result.data) if include_dpl else None,
        dpl_base64=base64.b64encode(result.data).decode("ascii") if include_dpl else None,
    )


def findings_text(findings: list[FindingOut]) -> str:
    """Human lines for findings, most severe first."""
    order = {"error": 0, "warning": 1, "info": 2}
    lines: list[str] = []
    for f in sorted(findings, key=lambda item: order.get(item.severity, 3)):
        where = "" if f.element is None else f" [element {f.element}]"
        record = "" if f.record is None else f" [record {f.record}]"
        fallback = f" -> {f.fallback}" if f.fallback else ""
        lines.append(f"{f.severity.upper():7} {f.code}{record}{where}: {f.message}{fallback}")
    return "\n".join(lines)


__all__ = ["FindingOut", "RenderPayload", "findings_text", "render_payload"]
