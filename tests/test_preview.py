"""The domain preview against the encoder: what the job places is what the picture shows.

Expected geometry is read back from the rendered job (the record's numbers, in the DPL unit the job
announces), never worked out by hand, so a test here fails when preview and encoder disagree.
"""

from __future__ import annotations

import re

import pytest

from datamax_dpl.domain.decoder import explain
from datamax_dpl.domain.enums import Unit
from datamax_dpl.domain.label import Element, Label, Line
from datamax_dpl.domain.preview import canvas_for, preview_bitmap
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.raster import Bitmap
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.units import MM_PER_INCH

#: A 203-dpi model with metric formats and no calibration defaults: the job is nominal geometry.
MODEL = "dmx-400"
LINE_RECORD = re.compile(rb"1X11000(\d{4})(\d{4})[Ll](\d{3,4}?)(\d{3,4})\r")
TENTHS_PER_MM = 10
HUNDREDTHS_PER_INCH = 100


def black_set(bitmap: Bitmap) -> set[tuple[int, int]]:
    return {(x, y) for y in range(bitmap.height) for x in range(bitmap.width) if bitmap.get(x, y)}


def framed(label: Label, model: str = MODEL) -> Bitmap:
    """The preview of an empty label: just the one-dot frame, to subtract from a drawing."""
    canvas = canvas_for(label, get_profile(model))
    frame = Bitmap(canvas.width, canvas.height)
    frame.draw_polygon([(0, 0), (canvas.width - 1, 0), (canvas.width - 1, canvas.height - 1), (0, canvas.height - 1)])
    return frame


def drawn(label: Label, model: str = MODEL) -> set[tuple[int, int]]:
    """The black pixels the preview adds to the frame."""
    return black_set(preview_bitmap(label, get_profile(model))) - black_set(framed(label, model))


def mm_label(*elements: Element) -> Label:
    return Label(unit=Unit.MM, width=50, height=30, elements=list(elements))


def encoded_rectangle(label: Label, model: str = MODEL) -> set[tuple[int, int]]:
    """The pixels of the label's one line record, converted from the job's own DPL unit to dots.

    The record's row and column are the rectangle's lower-left corner; its horizontal and vertical
    sizes run right and up from there.
    """
    data = build_job(label, RenderOptions(model=model)).data
    [record] = LINE_RECORD.findall(data)
    explanation = explain(data)
    assert explanation.label is not None
    per_inch = MM_PER_INCH * TENTHS_PER_MM if explanation.label.unit is Unit.MM else HUNDREDTHS_PER_INCH
    dpi = get_profile(model).dpi
    row, column, horizontal, vertical = (round(int(value) * dpi / per_inch) for value in record)
    bottom = canvas_for(label, get_profile(model)).height - 1 - row
    return {(x, y) for x in range(column, column + horizontal) for y in range(bottom - vertical + 1, bottom + 1)}


@pytest.mark.os_agnostic
def test_a_horizontal_line_covers_exactly_the_encoder_rectangle() -> None:
    label = mm_label(Line(x=10, y=10, x2=30, y2=10, thickness=1))
    expected = encoded_rectangle(label)
    assert expected
    assert drawn(label) == expected


@pytest.mark.os_agnostic
def test_a_vertical_line_covers_exactly_the_encoder_rectangle() -> None:
    label = mm_label(Line(x=12, y=25, x2=12, y2=5, thickness=0.5))
    expected = encoded_rectangle(label)
    assert expected
    assert drawn(label) == expected


@pytest.mark.os_agnostic
def test_a_line_drawn_right_to_left_covers_the_same_rectangle() -> None:
    label = mm_label(Line(x=30, y=10, x2=10, y2=10, thickness=1))
    assert drawn(label) == encoded_rectangle(label)


@pytest.mark.os_agnostic
def test_a_slanted_line_keeps_the_centred_stamp() -> None:
    label = mm_label(Line(x=5, y=5, x2=40, y2=25, thickness=1))
    canvas = canvas_for(label, get_profile(MODEL))
    stamped = framed(label)
    x0, y0 = canvas.point(5, 5)
    x1, y1 = canvas.point(40, 25)
    stamped.draw_line(x0, y0, x1, y1, thickness=canvas.dots(1))
    assert drawn(label) == black_set(stamped) - black_set(framed(label))
