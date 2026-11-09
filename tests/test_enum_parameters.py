"""A raw enum value handed to a public function takes the same branch as the enum member.

An untyped caller (a script, a notebook) can pass ``"xor"``, ``90`` or ``"mm"`` where the signature
names the enum. Comparing a parameter with ``is`` sent every such value down the other branch
silently: the wrong mode, the wrong rotation, a length in the wrong unit.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from datamax_dpl.domain.enums import CombineMode, Rotation, Unit
from datamax_dpl.domain.geometry import Rect
from datamax_dpl.domain.label import Label, Text
from datamax_dpl.domain.preview import FieldArea
from datamax_dpl.domain.raster import Bitmap
from datamax_dpl.domain.render import job_context
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.units import Axis, Correction, exact_dots, to_inches


@pytest.mark.os_agnostic
def test_combine_whitens_shared_dots_for_a_raw_xor() -> None:
    canvas = Bitmap.from_rows(["##"])
    canvas.combine(Bitmap.from_rows(["#."]), 0, 0, mode=cast("CombineMode", "xor"))
    assert canvas.rows() == Bitmap.from_rows([".#"]).rows()


@pytest.mark.os_agnostic
def test_combine_whitens_under_the_layer_for_a_raw_opaque() -> None:
    canvas = Bitmap.from_rows(["###"])
    canvas.combine(Bitmap.from_rows(["#."]), 1, 0, mode=cast("CombineMode", CombineMode.OPAQUE.value))
    assert canvas.rows() == Bitmap.from_rows(["##."]).rows()


@pytest.mark.os_agnostic
def test_combine_refuses_a_mode_that_names_none() -> None:
    with pytest.raises(ValueError, match="nonsense"):
        Bitmap.from_rows(["#"]).combine(Bitmap.from_rows(["#"]), 0, 0, mode=cast("CombineMode", "nonsense"))


@pytest.mark.os_agnostic
def test_field_area_turns_for_a_raw_quarter_turn() -> None:
    area = FieldArea(x=10, y=50, length=30, height=8, rotation=cast("Rotation", 90))
    assert area.box() == Rect(left=10, top=50, width=8, height=30)


@pytest.mark.parametrize("rotation", list(Rotation))
@pytest.mark.os_agnostic
def test_field_area_reads_a_raw_rotation_as_its_member(rotation: Rotation) -> None:
    typed = FieldArea(x=40, y=50, length=30, height=8, rotation=rotation)
    raw = replace(typed, rotation=cast("Rotation", int(rotation)))
    assert raw.box() == typed.box()
    assert raw.on_canvas(width=100, height=100) == typed.on_canvas(width=100, height=100)
    assert raw.rise() == typed.rise()


@pytest.mark.os_agnostic
def test_to_inches_converts_a_raw_mm_as_millimetres() -> None:
    assert to_inches(25.4, cast("Unit", "mm"), dpi=203) == pytest.approx(1.0)


@pytest.mark.os_agnostic
def test_to_inches_converts_raw_dots_by_the_resolution() -> None:
    assert to_inches(203, cast("Unit", "dots"), dpi=203) == pytest.approx(1.0)


@pytest.mark.os_agnostic
def test_a_raw_dots_length_takes_no_scale() -> None:
    """A length in dots already counts printed dots; only the DOTS branch leaves it unscaled."""
    scaled = Correction(scale=0.5)
    assert exact_dots(203, cast("Unit", "dots"), dpi=203, correction=scaled) == pytest.approx(203.0)


@pytest.mark.os_agnostic
def test_a_raw_axis_reads_that_axis_of_the_context() -> None:
    options = RenderOptions(model="allegro-2", scale_across=0.9, scale_along=1.1, offset_across=1.0, offset_along=2.0)
    ctx = job_context(Label(elements=[Text(x=0, y=0, text="x")]), options)
    across = cast("Axis", Axis.ACROSS.value)
    assert ctx.across != ctx.along
    assert ctx.correction(across) == ctx.across
    assert ctx.offset_mm(across) == ctx.offset_across
