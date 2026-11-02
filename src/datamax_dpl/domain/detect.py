"""Guess the printer model from what it answers.

DPL has no "which model are you" query (``docs/dpl-reference.md`` section 8), so detection
collects evidence: whether the serial test (``<STX>k``), the firmware query (``<STX>v``) and the
memory report (``<STX>KQ``) are answered, and the label width the memory report gives. Each piece
narrows the set of profiles that fit; the result names the evidence so a person can judge it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .enums import ModelFamily, Unit
from .profiles import MODEL_PROFILES, PrinterProfile
from .status import MemoryReport

#: Families whose manual entry lists the ``<STX>KQ`` memory report: the DMX 600/800, Prodigy Max,
#: PE42/PE43, Ovation and 6200 models.
_MEMORY_REPORT_FAMILIES = frozenset(
    {
        ModelFamily.DMX_600,
        ModelFamily.DMX_800,
        ModelFamily.MAX_PE,
        ModelFamily.PE,
        ModelFamily.OVATION,
        ModelFamily.TITAN,
    }
)


def _models_of(families: frozenset[ModelFamily]) -> frozenset[str]:
    """The ids of the models in ``families``, so a model added to a family is counted with it."""
    return frozenset(model for model, profile in MODEL_PROFILES.items() if profile.family in families)


#: Models whose manual entry lists the ``<STX>KQ`` memory report.
MEMORY_REPORT_MODELS = _models_of(_MEMORY_REPORT_FAMILIES)
#: The standard Prodigy and its 152 dpi variant: a printer that answers the serial test is neither.
_PRODIGY = _models_of(frozenset({ModelFamily.PRODIGY}))
#: Relative tolerance when matching the reported label width against a model's print width.
WIDTH_TOLERANCE = 0.02
_HUNDREDTHS = 100
_MM_TENTHS_PER_INCH = 254


class Evidence(BaseModel):
    """What the printer answered.

    Attributes:
        serial_test: ``<STX>k`` answered ``Y``; None when not asked or not answered.
        version: The firmware string from ``<STX>v``, or None.
        memory: The parsed ``<STX>KQ`` report, or None.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    serial_test: bool | None = None
    version: str | None = None
    memory: MemoryReport | None = None


class Detection(BaseModel):
    """Profiles that fit the evidence, best first, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidates: list[str]
    reasons: list[str]


def _width_inches(memory: MemoryReport) -> float | None:
    if memory.label_width is None:
        return None
    per_inch = _MM_TENTHS_PER_INCH if memory.units is Unit.MM else _HUNDREDTHS
    return memory.label_width / per_inch


def _width_fits(profile: PrinterProfile, width: float) -> bool:
    return abs(profile.print_width_in - width) <= profile.print_width_in * WIDTH_TOLERANCE


def _narrow(candidates: set[str], evidence: Evidence, reasons: list[str]) -> set[str]:
    if evidence.serial_test:
        candidates -= _PRODIGY
        reasons.append("answers the serial test, so not a Prodigy")
    if evidence.memory is not None:
        candidates &= MEMORY_REPORT_MODELS
        reasons.append("answers the memory report, which only the newer models have")
        width = _width_inches(evidence.memory)
        if width is not None:
            fitting = {c for c in candidates if _width_fits(MODEL_PROFILES[c], width)}
            reasons.append(f"reports a print width of {width:.2f} in")
            candidates = fitting or candidates
    elif evidence.version is not None:
        candidates -= MEMORY_REPORT_MODELS
        reasons.append("answers the firmware query but not the memory report")
    return candidates


def suggest_profiles(evidence: Evidence) -> Detection:
    """Return the profiles that fit ``evidence``.

    Example:
        >>> suggest_profiles(Evidence(serial_test=True, version="VER DA - 01.01")).candidates[:2]
        ['allegro', 'allegro-2']
    """
    reasons: list[str] = []
    candidates = _narrow(set(MODEL_PROFILES), evidence, reasons)
    if evidence.version is None and evidence.serial_test is None and evidence.memory is None:
        reasons.append("the printer answered nothing; check that the channel can read (tcp or serial)")
    ordered = [model for model in MODEL_PROFILES if model in candidates]
    return Detection(candidates=ordered, reasons=reasons)


__all__ = ["MEMORY_REPORT_MODELS", "WIDTH_TOLERANCE", "Detection", "Evidence", "suggest_profiles"]
