"""A filled box prints its fill and its outline without sharing a dot.

Most models print overlapping fields XOR, and the Allegro 2 always does, so a dot the fill and the
outline both cover prints white. The jobs are decoded and each record drawn on its own layer the
way the preview draws it; the layers must not meet, and a solid box must come out solid.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from datamax_dpl.domain.decoder import explain
from datamax_dpl.domain.enums import CombineMode, FillPattern, Unit
from datamax_dpl.domain.label import Box, Label
from datamax_dpl.domain.preview import Canvas, PreviewGeometry, canvas_for, draw_element
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.raster import Bitmap
from datamax_dpl.domain.render import build_job, job_context
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.units import Axis

#: Polygon models at 300, 289.56 and 203 dpi (the DMX 400 from firmware 2.01).
_POLYGON_MODELS = (("dmx-600", ""), ("dmx-430", ""), ("dmx-400", "2.01"))


def _layers(box: Box, *, unit: Unit, model: str, firmware: str = "") -> list[Bitmap]:
    """Render ``box`` alone, decode the job and draw each decoded record on its own layer."""
    result = build_job(Label(unit=unit, elements=[box]), RenderOptions(model=model, firmware=firmware))
    assert result.ok, [f.message for f in result.errors]
    decoded = explain(result.data).label
    assert decoded is not None
    canvas = canvas_for(decoded, get_profile(model))
    layers: list[Bitmap] = []
    for element in decoded.elements:
        layer = Bitmap(canvas.width, canvas.height)
        draw_element(element, layer, canvas)
        layers.append(layer)
    return layers


def _row_bits(layer: Bitmap) -> list[int]:
    return [int.from_bytes(row, "big") for row in layer.rows()]


def _shared_dots(layers: list[Bitmap]) -> int:
    """Count the dots two or more layers cover."""
    shared = 0
    for rows in zip(*(_row_bits(layer) for layer in layers), strict=True):
        seen = twice = 0
        for row in rows:
            twice |= seen & row
            seen |= row
        shared += twice.bit_count()
    return shared


def _printed_xor(layers: list[Bitmap]) -> Bitmap:
    """Combine the layers the way a model printing XOR does."""
    printed = Bitmap(layers[0].width, layers[0].height)
    for layer in layers:
        printed.combine(layer, 0, 0, mode=CombineMode.XOR)
    return printed


def _box_area(box: Box, canvas: Canvas) -> tuple[int, int, int, int]:
    """The box's (left, top, width, height) in preview pixels."""
    left, bottom = canvas.point(box.x, box.y)
    width, height = canvas.length(box.width, axis=Axis.ACROSS), canvas.length(box.height, axis=Axis.ALONG)
    return left, bottom - height + 1, width, height


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("model", "firmware"), _POLYGON_MODELS)
@pytest.mark.parametrize("unit", [Unit.INCH, Unit.MM])
def test_a_patterned_box_fill_and_its_outline_share_no_dot(model: str, firmware: str, unit: Unit) -> None:
    size = 1.0 if unit is Unit.INCH else 25.0
    box = Box(
        x=size / 5,
        y=size / 5,
        width=size,
        height=size / 2,
        thickness=size / 20,
        side_thickness=size / 10,
        fill=FillPattern.SHADE_25,
    )
    layers = _layers(box, unit=unit, model=model, firmware=firmware)
    assert len(layers) == 2
    assert _shared_dots(layers) == 0


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("model", "firmware"), [*_POLYGON_MODELS, ("generic", "")])
def test_a_solid_box_prints_solid(model: str, firmware: str) -> None:
    box = Box(x=0.2, y=0.2, width=1.0, height=0.5, thickness=0.05, fill=FillPattern.SOLID)
    layers = _layers(box, unit=Unit.INCH, model=model, firmware=firmware)
    printed = _printed_xor(layers)
    geometry = PreviewGeometry(
        job_context(Label(unit=Unit.INCH, elements=[box]), RenderOptions(model=model, firmware=firmware))
    )
    canvas = Canvas(width=printed.width, height=printed.height, geometry=geometry)
    left, top, width, height = _box_area(box, canvas)
    white = [(x, y) for y in range(top, top + height) for x in range(left, left + width) if not printed.get(x, y)]
    assert white == []


@pytest.mark.os_agnostic
def test_a_patterned_box_whose_outline_fills_it_sends_no_fill() -> None:
    """Sides that meet in the middle leave the fill no room: only the outline is sent."""
    box = Box(x=0.2, y=0.2, width=0.2, height=0.5, thickness=0.05, side_thickness=0.1, fill=FillPattern.SHADE_25)
    result = build_job(Label(unit=Unit.INCH, elements=[box]), RenderOptions(model="dmx-600"))
    assert [line for line in result.data.split(b"\r") if line.startswith(b"1X")] == [b"1X1100000200020B020050005010"]


@pytest.mark.os_agnostic
@settings(max_examples=60, deadline=None)
@given(
    model=st.sampled_from(_POLYGON_MODELS),
    unit=st.sampled_from([Unit.INCH, Unit.MM]),
    place=st.tuples(st.integers(min_value=1, max_value=300), st.integers(min_value=1, max_value=400)),
    cells=st.tuples(*(st.integers(min_value=1, max_value=60) for _ in range(4))),
)
def test_no_patterned_box_shares_a_dot_with_its_outline(
    model: tuple[str, str], unit: Unit, place: tuple[int, int], cells: tuple[int, int, int, int]
) -> None:
    """Every position, size and edge, in hundredths of an inch or tenths of a millimetre."""
    step = 0.01 if unit is Unit.INCH else 0.1
    x, y = (cell * step for cell in place)
    width, height, edge, side = (cell * step for cell in cells)
    box = Box(
        x=x,
        y=y,
        width=width + 2 * side,
        height=height + 2 * edge,
        thickness=edge,
        side_thickness=side,
        fill=FillPattern.SHADE_50,
    )
    assert _shared_dots(_layers(box, unit=unit, model=model[0], firmware=model[1])) == 0
