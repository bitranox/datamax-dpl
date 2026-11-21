"""The step a move is named in: one fact, read-only, for every unit a label is written in."""

from __future__ import annotations

from typing import cast

import pytest

from datamax_dpl.domain.encode_common import STEPS_PER_UNIT
from datamax_dpl.domain.enums import Unit


@pytest.mark.os_agnostic
def test_every_label_unit_has_a_step() -> None:
    assert set(STEPS_PER_UNIT) == set(Unit)


@pytest.mark.os_agnostic
def test_the_steps_are_a_hundredth_of_a_mm_or_an_inch_and_a_whole_dot() -> None:
    assert {unit: 1 / steps for unit, steps in STEPS_PER_UNIT.items()} == {
        Unit.MM: 0.01,
        Unit.INCH: 0.01,
        Unit.DOTS: 1.0,
    }


@pytest.mark.os_agnostic
def test_the_steps_cannot_be_changed() -> None:
    # Cast to the mutable type a caller might wrongly assume, so the write reaches the object.
    writable = cast("dict[Unit, int]", STEPS_PER_UNIT)
    with pytest.raises(TypeError):
        writable[Unit.MM] = 10
