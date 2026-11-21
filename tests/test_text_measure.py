"""A text field is measured once per render, and every caller places it by that one measurement.

The encoder, the overlap and edge checks and the preview each need an aligned line's width; they
read it from the render context (``text_metrics.measured``), so a right-aligned or centred font 9
line is measured once per record instead of five times. The tests seed that memo with a figure no
real line has and require each caller to place the field by it, so a caller that measures on its
own goes red; and they pin that the kept figures are the ones the pure functions give.
"""

from __future__ import annotations

import itertools

import pytest

from datamax_dpl.domain.encode_common import position_units
from datamax_dpl.domain.encode_text import encode_text
from datamax_dpl.domain.enums import FindingCode, TextAlign
from datamax_dpl.domain.label import FONT_9, Label, Text
from datamax_dpl.domain.overlap import inexact_rectangle
from datamax_dpl.domain.preview import Canvas, PreviewGeometry, field_area, letter_spans
from datamax_dpl.domain.render import job_context
from datamax_dpl.domain.render_context import RenderContext, RenderOptions
from datamax_dpl.domain.text_metrics import (
    TextMeasure,
    font9_left_bearing_dots,
    font9_print_shift_dots,
    measure_text,
    measured,
    text_column,
    text_extent,
    units_left_of_x,
)
from datamax_dpl.domain.units import Axis

_MODEL = "allegro-2"
#: The Allegro 2 prints 2 dots per 1/100 in, the unit its jobs place fields in.
_DOTS_PER_UNIT = 2
#: A width and a shift no real line of the fields below has, so a figure taken from elsewhere shows.
_SEEDED = TextMeasure(advances=(100, 100, 100), extent=300, left_bearing=10.0, print_shift=4)


def _context(element: Text, model: str = _MODEL) -> RenderContext:
    return job_context(Label(width=100, height=55, elements=[element]), RenderOptions(model=model))


def _seeded(element: Text) -> RenderContext:
    ctx = _context(element)
    ctx.text_measures[element] = _SEEDED
    return ctx


def _x_column(element: Text, ctx: RenderContext) -> int:
    """The column ``x`` itself is written at, the model's offset across included."""
    return position_units(element.x, axis=Axis.ACROSS, ctx=ctx)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("align", "share"), [(TextAlign.RIGHT, 300), (TextAlign.CENTER, 155)])
def test_the_record_column_comes_from_the_kept_measure(align: TextAlign, share: float) -> None:
    line = Text(x=50, y=20, text="5 STK", font=FONT_9, points=30, align=align)
    ctx = _seeded(line)
    back = round((share + _SEEDED.print_shift) / _DOTS_PER_UNIT)
    assert units_left_of_x(line, ctx) == back
    assert text_column(line, ctx) == _x_column(line, ctx) - back
    record = next(record for record in encode_text(line, index=0, ctx=ctx) if record.endswith(b"5 STK\r"))
    assert int(record[11:15]) == _x_column(line, ctx) - back


@pytest.mark.os_agnostic
def test_the_checks_and_the_preview_place_the_field_by_the_kept_measure() -> None:
    line = Text(x=50, y=20, text="5 STK", font=FONT_9, points=30, align=TextAlign.RIGHT)
    ctx = _seeded(line)
    printed_at = text_column(line, ctx) * _DOTS_PER_UNIT + _SEEDED.print_shift
    rectangle = inexact_rectangle(line, ctx)
    assert rectangle is not None
    assert (rectangle.left, rectangle.width) == (printed_at, _SEEDED.extent)
    canvas = Canvas(width=1, height=1, geometry=PreviewGeometry(ctx))
    area = field_area(line, canvas)
    assert (area.x, area.length) == (printed_at, _SEEDED.extent)
    spans = letter_spans(line, canvas)
    assert [span.start for span in spans] == [0, 100, 200]
    assert spans[-1].start + spans[-1].length == _SEEDED.extent


@pytest.mark.os_agnostic
def test_the_edge_check_reads_the_kept_print_shift() -> None:
    # A shift of 500 dots puts the right-aligned line's record left of the printhead: the refusal
    # names the kept shift, which no real model has.
    line = Text(x=50, y=20, text="5 STK", font=FONT_9, points=30, align=TextAlign.RIGHT)
    ctx = _context(line)
    ctx.text_measures[line] = TextMeasure(advances=(1,), extent=1, left_bearing=0.0, print_shift=500)
    encode_text(line, index=0, ctx=ctx)
    messages = [f.message for f in ctx.findings.findings() if f.code == FindingCode.TEXT_PAST_LEFT_EDGE]
    assert len(messages) == 1
    assert "500 dots right of its record" in messages[0]


@pytest.mark.os_agnostic
def test_a_line_is_measured_once_and_kept_by_its_value() -> None:
    line = Text(x=50, y=20, text="5 STK", font=FONT_9, points=30, align=TextAlign.CENTER)
    ctx = _context(line)
    first = measured(line, ctx)
    assert measured(line.model_copy(), ctx) is first
    assert list(ctx.text_measures) == [line]


_LINES = [
    Text(x=60, y=20, text=text, font=FONT_9, points=points, align=align)
    for text, points, align in itertools.product(
        ["5 STK", "W/,-.MI", " AB ", "Qu" + chr(0x00E4) + "lity 42"], [12, 18, 24, 30], TextAlign
    )
] + [
    Text(x=60, y=20, text="ABC 123", font=font, align=align, width_multiplier=2)
    for font, align in itertools.product([1, 2, 3, 4], TextAlign)
]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", ["allegro-2", "dmx-600", "prodigy"])
def test_the_kept_measure_is_what_the_pure_functions_give(model: str) -> None:
    for line in _LINES:
        ctx = _context(line, model)
        profile = ctx.profile
        assert measure_text(line, profile) == TextMeasure(
            advances=measure_text(line, profile).advances,
            extent=text_extent(line, profile),
            left_bearing=font9_left_bearing_dots(line, profile),
            print_shift=font9_print_shift_dots(line, profile),
        )
        share = {
            TextAlign.LEFT: 0.0,
            TextAlign.RIGHT: text_extent(line, profile),
            TextAlign.CENTER: (text_extent(line, profile) + font9_left_bearing_dots(line, profile)) / 2,
        }[line.align]
        expected = round((share + font9_print_shift_dots(line, profile)) / ctx.dots_per_unit)
        assert units_left_of_x(line, ctx) == expected, line
