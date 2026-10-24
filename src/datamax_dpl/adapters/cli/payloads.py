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
from ...domain.enums import FindingCode, Severity
from ...domain.units import DplUnit

if TYPE_CHECKING:
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
    """Result of ``render``, ``validate`` and ``print``."""

    model: str
    dpl_unit: DplUnit
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
        dpl_unit=first.dpl_unit,
        records=len(results),
        fields=sum(r.record_count for r in results),
        bytes=len(result.data),
        downloads=[name for r in results for name in r.downloads],
        findings=findings,
        dpl=readable(result.data) if include_dpl else None,
        dpl_base64=base64.b64encode(result.data).decode("ascii") if include_dpl else None,
    )


#: Sort rank of each severity in human output: the most severe first.
_SEVERITY_ORDER: dict[Severity, int] = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
# A new Severity without a rank fails here at import, not as a KeyError in human output only.
if set(_SEVERITY_ORDER) != set(Severity):  # pragma: no cover - import-time guard
    raise RuntimeError("every Severity needs a rank in _SEVERITY_ORDER")


def findings_text(findings: list[FindingOut]) -> str:
    """Human lines for findings, most severe first."""
    lines: list[str] = []
    for f in sorted(findings, key=lambda item: _SEVERITY_ORDER[item.severity]):
        where = "" if f.element is None else f" [element {f.element}]"
        record = "" if f.record is None else f" [record {f.record}]"
        fallback = f" -> {f.fallback}" if f.fallback else ""
        lines.append(f"{f.severity.value.upper():7} {f.code.value}{record}{where}: {f.message}{fallback}")
    return "\n".join(lines)


__all__ = ["FindingOut", "RenderPayload", "findings_text", "render_payload"]
