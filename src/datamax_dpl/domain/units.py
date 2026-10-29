"""Length conversion between the label's unit and the DPL field units.

DPL writes every distance as an integer: hundredths of an inch in inch mode, tenths of a
millimetre in metric mode. Bar and module widths are always in printer dots. A label is authored
in one :class:`~datamax_dpl.domain.enums.Unit`; this module turns its numbers into those integers
and reports how far rounding moved them, so a caller can tell an exact layout from an
approximated one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .enums import Unit

MM_PER_INCH = 25.4
#: The range a printer's scale factor may take: printed length divided by nominal length.
MIN_SCALE = 0.5
MAX_SCALE = 1.5


class DplUnit(str, Enum):
    """The distance unit a DPL job is written in.

    Example:
        >>> DplUnit.INCH_HUNDREDTHS.per_inch
        100.0
        >>> round(DplUnit.MM_TENTHS.per_inch, 1)
        254.0
    """

    INCH_HUNDREDTHS = "inch"
    MM_TENTHS = "metric"

    @property
    def per_inch(self) -> float:
        """Number of DPL units in one inch."""
        return 100.0 if self is DplUnit.INCH_HUNDREDTHS else MM_PER_INCH * 10.0


class Axis(str, Enum):
    """Direction of a length on the label: across the web (columns) or along the feed (rows).

    The two differ physically: the dot pitch across is fixed by the printhead, the row pitch
    along the feed by the platen roller, so a printer can need a different scale on each.
    """

    ACROSS = "across"
    ALONG = "along"


def to_inches(value: float, unit: Unit, *, dpi: float) -> float:
    """Convert ``value`` written in ``unit`` to inches.

    Args:
        value: The length.
        unit: Unit ``value`` is written in.
        dpi: Printer resolution, used when ``unit`` is :attr:`Unit.DOTS`.

    Returns:
        The length in inches.

    Example:
        >>> to_inches(25.4, Unit.MM, dpi=203)
        1.0
        >>> to_inches(203, Unit.DOTS, dpi=203)
        1.0
    """
    if unit is Unit.MM:
        return value / MM_PER_INCH
    if unit is Unit.DOTS:
        return value / dpi
    return value


@dataclass(frozen=True)
class Converted:
    """An integer DPL distance and how far it is from the requested length.

    Attributes:
        value: The integer written into the DPL field.
        error_inches: ``value`` minus the requested length, in inches.
    """

    value: int
    error_inches: float


@dataclass(frozen=True)
class Correction:
    """How one printer prints lengths on one axis, against nominal.

    Attributes:
        scale: Printed length divided by nominal length; 1.0 is exact.
        unit_dots: Whole printer dots one 1/100 in prints as on models that round it to whole
            dots (2 on the Allegro 2, measured); None for the exact 1/100 in. Metric jobs and
            plain dot counts ignore it.

    Example:
        >>> NOMINAL.scale, NOMINAL.unit_dots
        (1.0, None)
    """

    scale: float = 1.0
    unit_dots: float | None = None


#: No correction: lengths print at their nominal size.
NOMINAL = Correction()


def _printed_inches(value: float, unit: Unit, *, dpi: float, scale: float) -> float:
    """Inches to ask for so that ``value`` comes out at its nominal length on paper.

    A length in dots already counts printed dots, so no scale applies to it.
    """
    inches = to_inches(value, unit, dpi=dpi)
    return inches if unit is Unit.DOTS else inches / scale


def to_dpl(value: float, unit: Unit, *, target: DplUnit, dpi: float, correction: Correction = NOMINAL) -> Converted:
    """Convert a length to the nearest integer DPL distance.

    Args:
        value: The length.
        unit: Unit ``value`` is written in.
        target: DPL unit of the job.
        dpi: Printer resolution, for :attr:`Unit.DOTS`.
        correction: How the printer prints this length's axis.

    Returns:
        The rounded value and its rounding error, in the DPL unit's own inches.

    Example:
        >>> to_dpl(10, Unit.MM, target=DplUnit.MM_TENTHS, dpi=203).value
        100
        >>> to_dpl(10, Unit.MM, target=DplUnit.INCH_HUNDREDTHS, dpi=203).value
        39
        >>> to_dpl(100, Unit.MM, target=DplUnit.INCH_HUNDREDTHS, dpi=203, correction=Correction(unit_dots=2)).value
        400
    """
    per_inch = target.per_inch
    if correction.unit_dots is not None and target is DplUnit.INCH_HUNDREDTHS:
        per_inch = dpi / correction.unit_dots
    exact = _printed_inches(value, unit, dpi=dpi, scale=correction.scale) * per_inch
    rounded = round(exact)
    return Converted(value=rounded, error_inches=(rounded - exact) / per_inch)


def exact_dots(value: float, unit: Unit, *, dpi: float, correction: Correction = NOMINAL) -> float:
    """Convert a length to printer dots, unrounded.

    Args:
        value: The length.
        unit: Unit ``value`` is written in.
        dpi: Printer resolution.
        correction: How the printer prints this length's axis; only its scale applies.

    Example:
        >>> exact_dots(1, Unit.INCH, dpi=300)
        300.0
        >>> exact_dots(1, Unit.INCH, dpi=300, correction=Correction(scale=0.5))
        600.0
    """
    return _printed_inches(value, unit, dpi=dpi, scale=correction.scale) * dpi


def to_dots(value: float, unit: Unit, *, dpi: float, correction: Correction = NOMINAL) -> int:
    """Convert a length to whole printer dots (at least one).

    Args:
        value: The length.
        unit: Unit ``value`` is written in.
        dpi: Printer resolution.
        correction: How the printer prints this length's axis; only its scale applies.

    Example:
        >>> to_dots(1, Unit.INCH, dpi=300)
        300
        >>> to_dots(0.01, Unit.MM, dpi=203)
        1
    """
    return max(1, round(exact_dots(value, unit, dpi=dpi, correction=correction)))


def from_dpl(value: int, *, source: DplUnit, unit: Unit, dpi: float) -> float:
    """Convert an integer DPL distance back to ``unit``.

    Example:
        >>> from_dpl(100, source=DplUnit.INCH_HUNDREDTHS, unit=Unit.MM, dpi=203)
        25.4
    """
    inches = value / source.per_inch
    if unit is Unit.MM:
        return round(inches * MM_PER_INCH, 4)
    if unit is Unit.DOTS:
        return round(inches * dpi, 4)
    return round(inches, 6)


__all__ = [
    "MAX_SCALE",
    "MIN_SCALE",
    "MM_PER_INCH",
    "NOMINAL",
    "Axis",
    "Converted",
    "Correction",
    "DplUnit",
    "exact_dots",
    "from_dpl",
    "to_dots",
    "to_dpl",
    "to_inches",
]
