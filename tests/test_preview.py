"""The domain preview against the encoder: what the job places is what the picture shows.

Expected geometry is read back from the rendered job (the record's numbers, in the DPL unit the job
announces), never worked out by hand, so a test here fails when preview and encoder disagree.
"""

from __future__ import annotations

import re

import pytest

from datamax_dpl.domain.decoder import explain
from datamax_dpl.domain.enums import Rotation, Unit
from datamax_dpl.domain.label import Barcode, Element, Label, Line, ScalableText, Text
from datamax_dpl.domain.preview import canvas_for, preview_bitmap, text_box
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.raster import Bitmap
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.symbology import Symbology
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


# ======================== rotation (manual p. 94 and Figure 7-1, p. 92) ========================

Rect = tuple[int, int, int, int]


def bounds(pixels: set[tuple[int, int]]) -> Rect:
    """(left, top, width, height) of a set of pixels."""
    xs, ys = [x for x, _ in pixels], [y for _, y in pixels]
    return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def turned_text(text: str, rotation: Rotation) -> Label:
    """A dots label with one font-2 text whose reference point sits in the middle of the canvas."""
    element = Text(x=100, y=100, text=text, font=2, rotation=rotation)
    return Label(unit=Unit.DOTS, width=200, height=200, elements=[element])


def reference(label: Label) -> tuple[int, int]:
    """The pixel of the label's only element's reference point."""
    [element] = label.elements
    assert isinstance(element, Text | ScalableText | Barcode)
    return canvas_for(label, get_profile(MODEL)).point(element.x, element.y)


def upright_size(text: str) -> tuple[int, int]:
    """(length, height) in dots of the text written at rotation 0."""
    label = turned_text(text, Rotation.DEG_0)
    [element] = label.elements
    assert isinstance(element, Text)
    _, _, length, height = text_box(element, canvas_for(label, get_profile(MODEL)))
    return length, height


def expected_turn(label: Label, length: int, height: int, rotation: Rotation) -> Rect:
    """The manual's rule: the field turns clockwise about its reference point (lower-left at 0).

    0 runs right and up, 90 down and right, 180 left and down, 270 up and left.
    """
    x, y = reference(label)
    return {
        Rotation.DEG_0: (x, y - height + 1, length, height),
        Rotation.DEG_90: (x, y, height, length),
        Rotation.DEG_180: (x - length + 1, y, length, height),
        Rotation.DEG_270: (x - height + 1, y - length + 1, height, length),
    }[rotation]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("rotation", list(Rotation))
def test_a_turned_text_box_follows_the_manuals_clockwise_rule(rotation: Rotation) -> None:
    label = turned_text("ABC", rotation)
    [element] = label.elements
    assert isinstance(element, Text)
    length, height = upright_size("ABC")
    assert text_box(element, canvas_for(label, get_profile(MODEL))) == expected_turn(label, length, height, rotation)
    # The box holds the gap after the last character; the drawn cells end before it.
    _, _, inked, _ = bounds(drawn(turned_text("ABC", Rotation.DEG_0)))
    assert bounds(drawn(label)) == expected_turn(label, inked, height, rotation)


@pytest.mark.os_agnostic
def test_a_text_at_90_is_taller_than_wide_and_runs_down_from_the_reference_point() -> None:
    label = turned_text("ABC", Rotation.DEG_90)
    left, top, width, height = bounds(drawn(label))
    assert height > width
    assert (left, top) == reference(label)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("rotation", list(Rotation))
def test_the_first_character_sits_at_the_reference_point(rotation: Rotation) -> None:
    """Only the A of "A  " is drawn: its cell is the one the characters advance from."""
    _, _, cell, height = bounds(drawn(turned_text("A  ", Rotation.DEG_0)))
    label = turned_text("A  ", rotation)
    assert bounds(drawn(label)) == expected_turn(label, cell, height, rotation)


@pytest.mark.os_agnostic
def test_the_four_rotations_draw_four_different_pictures() -> None:
    pictures = {frozenset(drawn(turned_text("ABC", rotation))) for rotation in Rotation}
    assert len(pictures) == len(Rotation)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("rotation", list(Rotation))
def test_a_turned_barcode_turns_about_its_reference_point(rotation: Rotation) -> None:
    upright = Barcode(x=150, y=150, symbology=Symbology.CODE128, data="12", height=40, narrow_dots=1)
    label = Label(unit=Unit.DOTS, width=300, height=300, elements=[upright.model_copy(update={"rotation": rotation})])
    canvas = canvas_for(label, get_profile(MODEL))
    _, _, length, height = text_box(upright, canvas)
    assert bounds(drawn(label)) == expected_turn(label, length, height, rotation)


@pytest.mark.os_agnostic
def test_a_turned_scalable_text_follows_the_same_rule() -> None:
    upright = ScalableText(x=100, y=100, text="AB", height_points=12)
    turned = upright.model_copy(update={"rotation": Rotation.DEG_270})
    label = Label(unit=Unit.DOTS, width=200, height=200, elements=[turned])
    _, _, length, height = text_box(upright, canvas_for(label, get_profile(MODEL)))
    assert bounds(drawn(label)) == expected_turn(label, length, height, Rotation.DEG_270)


@pytest.mark.os_agnostic
def test_a_label_without_height_is_tall_enough_for_text_turned_upward() -> None:
    element = Text(x=50, y=2, text="ABCDEFGH", font=2, rotation=Rotation.DEG_270)
    label = Label(unit=Unit.DOTS, width=200, elements=[element])
    _, top, _, _ = text_box(element, canvas_for(label, get_profile(MODEL)))
    assert top >= 1
