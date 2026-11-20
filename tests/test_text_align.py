"""Centred and right-aligned text: the field moves back by the width its line prints.

DPL places a field at its lower-left corner, so ``align`` is the encoder's arithmetic: the record's
column is ``x``'s minus the line's width in DPL units. Fonts 0-8 are measured by their cells, which
the manual gives; font 9 by a Helvetica-Bold stand-in for CG Triumvirate Bold (``text_metrics``).
"""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from datamax_dpl import render_batch
from datamax_dpl.adapters.preview import render_preview_png
from datamax_dpl.domain.charset import FONT_BYTES
from datamax_dpl.domain.enums import FindingCode, Rotation, TextAlign
from datamax_dpl.domain.label import FONT_9, Counter, Label, Line, Text
from datamax_dpl.domain.preview import canvas_for, field_area, letter_spans
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.render import build_job, render
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.text_metrics import FONT_9_ADVANCES, char_advances, text_extent

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamax_dpl.domain.findings import Finding

_MODEL = "allegro-2"
#: The Allegro 2 prints 2 dots per 1/100 in, the unit its jobs place fields in.
_DOTS_PER_UNIT = 2
#: A text record: rotation, font, two multipliers, size, row and column (15 characters), then the data.
_TEXT_RECORD = re.compile(rb"^[1-4][0-9][1-9A-O][1-9A-O][0-9A-Za-z]{3}(\d{4})(\d{4})(.*)$")


def _columns(data: bytes) -> dict[bytes, int]:
    """The record column of each text field in a job, by the text it prints."""
    found: dict[bytes, int] = {}
    for record in data.split(b"\r"):
        match = _TEXT_RECORD.match(record)
        if match:
            found[match.group(3)] = int(match.group(2))
    return found


def _column(element: Text) -> int:
    result = render(Label(width=100, height=55, elements=[element]), RenderOptions(model=_MODEL))
    assert result.ok, result.findings
    return _columns(result.data)[element.text.encode("cp437")]


def _codes(findings: Iterable[Finding]) -> set[FindingCode]:
    return {finding.code for finding in findings}


@pytest.mark.os_agnostic
def test_left_is_the_default_and_places_the_field_at_x() -> None:
    assert Text(x=50, y=10, text="ABC").align is TextAlign.LEFT
    assert _column(Text(x=50, y=10, text="ABC")) == _column(Text(x=50, y=10, text="ABC", align="left"))


@pytest.mark.os_agnostic
def test_a_right_aligned_font_2_line_moves_back_by_its_cells_less_the_last_gap() -> None:
    # Font 2 at 203 dpi: cells 10 dots wide with a 2-dot gap, so ABC spans 3 * 12 - 2 = 34 dots.
    left = _column(Text(x=50, y=10, text="ABC", font=2))
    assert left - _column(Text(x=50, y=10, text="ABC", font=2, align="right")) == 34 // _DOTS_PER_UNIT
    # Centred: half of 34 dots is 8.5 units, rounded to the even 8.
    assert left - _column(Text(x=50, y=10, text="ABC", font=2, align="center")) == 8


@pytest.mark.os_agnostic
def test_a_width_multiplier_of_fonts_0_to_8_widens_the_shift_with_the_cells() -> None:
    left = _column(Text(x=50, y=10, text="ABC", font=2, width_multiplier=2))
    right = _column(Text(x=50, y=10, text="ABC", font=2, width_multiplier=2, align="right"))
    assert left - right == (3 * 24 - 4) // _DOTS_PER_UNIT


@pytest.mark.os_agnostic
def test_font_9_moves_back_by_each_letters_own_width() -> None:
    # 8 pt at 203 dpi is an em of 22.56 dots: i advances 278/1000 em (6 dots), W 944/1000 (21 dots).
    narrow = Text(x=95, y=10, text="iiiiiiii", font=FONT_9, points=8)
    wide = Text(x=95, y=10, text="WWWWWWWW", font=FONT_9, points=8)
    assert _column(narrow) - _column(narrow.model_copy(update={"align": TextAlign.RIGHT})) == 48 // _DOTS_PER_UNIT
    assert _column(wide) - _column(wide.model_copy(update={"align": TextAlign.RIGHT})) == 168 // _DOTS_PER_UNIT


@pytest.mark.os_agnostic
def test_font_9_is_measured_at_the_size_the_model_prints() -> None:
    # The Allegro 2 has no 4 pt size; the encoder sends 6 pt, so the line is 6 pt wide.
    profile = get_profile(_MODEL)
    asked = Text(x=0, y=0, text="WWWW", font=FONT_9, points=4)
    printed = Text(x=0, y=0, text="WWWW", font=FONT_9, points=6)
    assert char_advances(asked, profile) == char_advances(printed, profile)


@pytest.mark.os_agnostic
def test_alignment_is_worked_out_per_record_after_the_placeholders_are_filled() -> None:
    label = Label(
        width=100, height=55, elements=[Text(x=95, y=10, text="{qty} STK", font=FONT_9, points=30, align="right")]
    )
    batch = render_batch(label, [{"qty": "5"}, {"qty": "12345,5"}], RenderOptions(model=_MODEL))
    assert all(result.ok for result in batch.results)
    short = _columns(batch.results[0].data)[b"5 STK"]
    long = _columns(batch.results[1].data)[b"12345,5 STK"]
    profile = get_profile(_MODEL)
    extra = text_extent(Text(x=0, y=0, text="12345,", font=FONT_9, points=30), profile)
    assert short - long == pytest.approx(extra / _DOTS_PER_UNIT, abs=1)


@pytest.mark.os_agnostic
def test_a_line_that_would_start_left_of_the_label_is_refused() -> None:
    label = Label(
        width=100, height=55, elements=[Text(x=5, y=10, text="WWWWWWWW", font=FONT_9, points=30, align="right")]
    )
    result = build_job(label, RenderOptions(model=_MODEL))
    assert not result.ok
    refusal = next(f for f in result.errors if f.code is FindingCode.TEXT_PAST_LEFT_EDGE)
    assert refusal.element == 0
    assert "move x right" in refusal.message


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("changes", "needs"),
    [
        ({"rotation": Rotation.DEG_90}, "upright"),
        ({"counter": Counter()}, "counter"),
        ({"font_id": "100"}, "font_id"),
        ({"width_multiplier": 2}, "width_multiplier"),
    ],
)
def test_alignment_of_a_line_whose_width_is_unknown_is_refused(changes: dict[str, object], needs: str) -> None:
    fields: dict[str, object] = {"x": 50, "y": 10, "text": "12", "font": FONT_9, "points": 8, "align": "right"}
    if "font_id" in changes:
        fields.pop("points")
    with pytest.raises(ValidationError, match=needs):
        Text.model_validate({**fields, **changes})
    # The same field left-aligned stays valid: only the alignment needs the width.
    assert Text.model_validate({**fields, **changes, "align": "left"}).align is TextAlign.LEFT


@pytest.mark.os_agnostic
def test_the_preview_ends_a_right_aligned_line_at_x() -> None:
    element = Text(x=95, y=10, text="12345,5 STK", font=FONT_9, points=30, align="right")
    label = Label(width=100, height=55, elements=[element])
    canvas = canvas_for(label, get_profile(_MODEL))
    area = field_area(element, canvas)
    end, x_dot = area.x + area.length, canvas.point(element.x, element.y).x
    # The shift rounds to whole DPL units: the line ends within one unit of x's dot.
    assert abs(end - x_dot) <= _DOTS_PER_UNIT
    assert area.length == sum(char_advances(element, get_profile(_MODEL)))


@pytest.mark.os_agnostic
def test_the_label_edge_check_reads_the_aligned_position() -> None:
    def edge_findings(align: str) -> set[FindingCode]:
        text = Text(x=99, y=10, text="BOX 12/12", font=FONT_9, points=30, align=align)
        return _codes(render(Label(width=100, height=55, elements=[text]), RenderOptions(model=_MODEL)).findings)

    assert FindingCode.ELEMENT_PAST_LABEL_EDGE in edge_findings("left")
    assert FindingCode.ELEMENT_PAST_LABEL_EDGE not in edge_findings("right")


@pytest.mark.os_agnostic
def test_every_byte_font_9_prints_has_a_width() -> None:
    assert set(FONT_9_ADVANCES) == set(FONT_BYTES[FONT_9])
    assert all(0 < width <= 1000 for width in FONT_9_ADVANCES.values())


@pytest.mark.os_agnostic
def test_font_9_letters_follow_their_widths_in_the_preview_cells() -> None:
    element = Text(x=5, y=10, text="Wi", font=FONT_9, points=30)
    canvas = canvas_for(Label(width=100, height=55, elements=[element]), get_profile(_MODEL))
    wide, narrow = letter_spans(element, canvas)
    assert (wide.start, narrow.start) == (0, wide.length)
    assert wide.length > 3 * narrow.length


#: A grey level below this reads as dark in the preview PNG (mode "1": 0 or 255).
_DARK_BELOW = 128


@pytest.mark.os_agnostic
def test_the_lettered_preview_prints_the_last_letter_of_a_font_9_line() -> None:
    """An Allegro 2 printed NORTHWAY whole at 8 pt where the preview cut its Y (2026-11-20)."""
    image_module = pytest.importorskip("PIL.Image")
    element = Text(x=5, y=20, text="NORTHWAY", font=FONT_9, points=8)
    # A vertical line as a landmark keeps the canvas from cropping anything: the label is fixed size.
    label = Label(width=100, height=55, elements=[element, Line(x=90, y=1, x2=90, y2=5, thickness=0.3)])
    profile = get_profile(_MODEL)
    canvas = canvas_for(label, profile)
    area = field_area(element, canvas)
    last = letter_spans(element, canvas)[-1]
    with image_module.open(io.BytesIO(render_preview_png(label, profile))) as image:
        grey = image.convert("L")
        top = area.y - area.height + 1
        dark = [
            (x, y)
            for x in range(area.x + last.start, area.x + last.start + last.length)
            for y in range(top, area.y + 1)
            if grey.getpixel((x, y)) < _DARK_BELOW
        ]
    assert dark, "the Y of NORTHWAY is lettered"
