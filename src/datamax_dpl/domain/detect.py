"""Guess the printer model from what it answers.

DPL has no "which model are you" query (``docs/dpl-reference.md`` section 8), so detection
collects evidence: whether the serial test (``<STX>k``), the firmware query (``<STX>v``) and the
memory report (``<STX>KQ``) are answered, and the label width the memory report gives. Each piece
narrows the set of profiles that fit; the result names the evidence so a person can judge it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .enums import Unit
from .profiles import MODEL_PROFILES, PrinterProfile
from .status import MemoryReport

#: Models whose manual entry lists the ``<STX>KQ`` memory report.
MEMORY_REPORT_MODELS = frozenset(
    {
        "dmx-600",
        "dmx-800",
        "prodigy-max-203",
        "prodigy-max-300",
        "pe42",
        "pe43",
        "ovation",
        "ovation-300",
        "ovation-2",
        "titan-6200",
    }
)
_PRODIGY = frozenset({"prodigy", "prodigy-152"})
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
