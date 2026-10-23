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


def to_dpl(value: float, unit: Unit, *, target: DplUnit, dpi: float) -> Converted:
    """Convert a length to the nearest integer DPL distance.

    Args:
        value: The length.
        unit: Unit ``value`` is written in.
        target: DPL unit of the job.
        dpi: Printer resolution, for :attr:`Unit.DOTS`.

    Returns:
        The rounded value and its rounding error.

    Example:
        >>> to_dpl(10, Unit.MM, target=DplUnit.MM_TENTHS, dpi=203).value
        100
        >>> to_dpl(10, Unit.MM, target=DplUnit.INCH_HUNDREDTHS, dpi=203).value
        39
    """
    inches = to_inches(value, unit, dpi=dpi)
    exact = inches * target.per_inch
    rounded = round(exact)
    return Converted(value=rounded, error_inches=(rounded - exact) / target.per_inch)


def to_dots(value: float, unit: Unit, *, dpi: float) -> int:
    """Convert a length to whole printer dots (at least one).

    Example:
        >>> to_dots(1, Unit.INCH, dpi=300)
        300
        >>> to_dots(0.01, Unit.MM, dpi=203)
        1
    """
    return max(1, round(to_inches(value, unit, dpi=dpi) * dpi))


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


__all__ = ["MM_PER_INCH", "Converted", "DplUnit", "from_dpl", "to_dots", "to_dpl", "to_inches"]
