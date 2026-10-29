"""Length conversion: the whole-dot inch unit and the per-axis scale factor."""

from __future__ import annotations

import pytest

from datamax_dpl.domain.enums import Unit
from datamax_dpl.domain.units import Correction, DplUnit, to_dots, to_dpl


@pytest.mark.os_agnostic
def test_whole_dot_inch_unit_maps_a_millimetre_length_to_more_hundredths() -> None:
    """Two dots at 203 dpi print 0.2502 mm per 1/100 in, so 100 mm needs 400 units, not 394."""
    assert (
        to_dpl(100, Unit.MM, target=DplUnit.INCH_HUNDREDTHS, dpi=203, correction=Correction(unit_dots=2)).value == 400
    )


@pytest.mark.os_agnostic
def test_whole_dot_inch_unit_does_not_apply_to_metric_jobs() -> None:
    assert to_dpl(10, Unit.MM, target=DplUnit.MM_TENTHS, dpi=203, correction=Correction(unit_dots=2)).value == 100


@pytest.mark.os_agnostic
def test_a_scale_below_one_lengthens_the_written_value() -> None:
    """A printer that prints 0.977 of nominal needs 200 mm written as 200 / 0.977 mm."""
    assert (
        to_dpl(200, Unit.MM, target=DplUnit.INCH_HUNDREDTHS, dpi=203, correction=Correction(scale=0.977)).value == 806
    )


@pytest.mark.os_agnostic
def test_a_length_in_dots_ignores_the_scale_and_uses_the_whole_dot_unit() -> None:
    """Dots already count printed dots: 203 dots are 101.5 two-dot units, rounded to 102."""
    assert (
        to_dpl(
            203, Unit.DOTS, target=DplUnit.INCH_HUNDREDTHS, dpi=203, correction=Correction(unit_dots=2, scale=0.9)
        ).value
        == 102
    )


@pytest.mark.os_agnostic
def test_to_dots_applies_the_scale() -> None:
    assert to_dots(25.4, Unit.MM, dpi=203, correction=Correction(scale=0.5)) == 406


@pytest.mark.os_agnostic
def test_to_dots_ignores_the_scale_for_dots() -> None:
    assert to_dots(50, Unit.DOTS, dpi=203, correction=Correction(scale=0.5)) == 50


@pytest.mark.os_agnostic
def test_the_defaults_keep_the_nominal_result() -> None:
    assert to_dpl(10, Unit.MM, target=DplUnit.INCH_HUNDREDTHS, dpi=203).value == 39
    assert to_dots(1, Unit.INCH, dpi=300) == 300
