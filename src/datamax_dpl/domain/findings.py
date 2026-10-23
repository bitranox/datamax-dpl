"""Findings: everything rendering noticed about a label, and how the fallback policy weighs it.

A finding has a stable ``code`` (for scripts and LLMs), a ``severity`` and a sentence for people.
:class:`FindingCollector` applies the :class:`~datamax_dpl.domain.enums.FallbackPolicy`: a
deviation (a feature replaced by a fallback) is a warning under ``degrade`` and an error under
``strict``; a refusal (no fallback exists) is an error under both.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .enums import FallbackPolicy, Severity


class Finding(BaseModel):
    """One observation about a label.

    Attributes:
        code: Stable identifier, e.g. ``circle_rasterized`` or ``barcode_bad_check_digit``.
        severity: ``error`` stops rendering; ``warning`` and ``info`` are reported.
        message: One sentence for people.
        element: Index into ``Label.elements``, or None for job-level findings.
        fallback: What was done instead, when a fallback was applied.

    Example:
        >>> Finding(code="x", severity=Severity.INFO, message="m").element is None
        True
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    severity: Severity
    message: str
    element: int | None = None
    fallback: str | None = None


class FindingCollector:
    """Collect findings, weighing deviations by the fallback policy.

    Example:
        >>> collector = FindingCollector(FallbackPolicy.STRICT)
        >>> collector.deviate("mirror_ignored", "no mirror", fallback="printed unmirrored")
        >>> collector.has_errors
        True
    """

    def __init__(self, policy: FallbackPolicy) -> None:
        """Start an empty collection for ``policy``."""
        self.policy = policy
        self._findings: list[Finding] = []

    def info(self, code: str, message: str, *, element: int | None = None) -> None:
        """Record a neutral observation."""
        self._findings.append(Finding(code=code, severity=Severity.INFO, message=message, element=element))

    def warn(self, code: str, message: str, *, element: int | None = None) -> None:
        """Record something the caller should look at; never an error."""
        self._findings.append(Finding(code=code, severity=Severity.WARNING, message=message, element=element))

    def deviate(self, code: str, message: str, *, fallback: str, element: int | None = None) -> None:
        """Record a feature replaced by ``fallback``: a warning under degrade, an error under strict."""
        severity = Severity.ERROR if self.policy is FallbackPolicy.STRICT else Severity.WARNING
        self._findings.append(
            Finding(code=code, severity=severity, message=message, element=element, fallback=fallback)
        )

    def refuse(self, code: str, message: str, *, element: int | None = None) -> None:
        """Record something no fallback can repair; always an error."""
        self._findings.append(Finding(code=code, severity=Severity.ERROR, message=message, element=element))

    @property
    def degrading(self) -> bool:
        """True when fallbacks are applied (the policy is degrade)."""
        return self.policy is FallbackPolicy.DEGRADE

    @property
    def has_errors(self) -> bool:
        """True when at least one finding is an error."""
        return any(f.severity is Severity.ERROR for f in self._findings)

    def findings(self) -> tuple[Finding, ...]:
        """Return every finding in the order it was recorded."""
        return tuple(self._findings)


__all__ = ["Finding", "FindingCollector"]
