"""Font 9's print shift: the Allegro 2 prints font 9 a few dots right of its record's column.

Measured with the V-line label (``docs/measurements/2026-11-21-allegro-2-CA02-03.04-font-9-widths/vline``):
2 dots at 30 pt, none at 12, 18 and 24 pt. Every upright font 9 field is written that much further
left, and the edge and overlap checks place it where it prints. The expected shifts are written
out here, not read from the profile, so a profile that loses them fails.
"""

from __future__ import annotations

import itertools
import re
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.domain.enums import FindingCode, Rotation, TextAlign
from datamax_dpl.domain.label import FONT_9, Label, Text
from datamax_dpl.domain.overlap import inexact_rectangle
from datamax_dpl.domain.preview import Canvas, PreviewGeometry, field_area
from datamax_dpl.domain.profiles import MODEL_PROFILES, get_profile
from datamax_dpl.domain.render import build_job, job_context
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.text_metrics import font9_print_shift_dots, text_column, text_extent

if TYPE_CHECKING:
    from datamax_dpl.domain.findings import Finding

_MODEL = "allegro-2"
#: Dots the Allegro 2 prints font 9 right of its record, per measured size.
_MEASURED_SHIFT = {12: 0, 18: 0, 24: 0, 30: 2}
#: The Allegro 2 prints 2 dots per 1/100 in, the unit its jobs place fields in.
_DOTS_PER_UNIT = 2
#: A text record: rotation, font, two multipliers, size, row and column (15 characters), then the data.
_TEXT_RECORD = re.compile(rb"^[1-4]([0-9])[1-9A-O][1-9A-O][0-9A-Za-z]{3}(\d{4})(\d{4})(.*)$")


def _records(model: str, *elements: Text, offset_across: float = 0.0) -> dict[bytes, tuple[int, int]]:
    """Row and column of each text record of a job, by the text it prints."""
    result = build_job(
        Label(width=100, height=55, elements=list(elements)), RenderOptions(model=model, offset_across=offset_across)
    )
    assert result.ok, result.findings
    found: dict[bytes, tuple[int, int]] = {}
    for record in result.data.split(b"\r"):
        match = _TEXT_RECORD.match(record)
        if match:
            found[match.group(4)] = (int(match.group(2)), int(match.group(3)))
    return found


def _job(text: Text, *, offset_across: float = 0.0, scale_across: float = 1.0) -> tuple[bool, list[Finding]]:
    result = build_job(
        Label(width=100, height=55, elements=[text]),
        RenderOptions(model=_MODEL, offset_across=offset_across, scale_across=scale_across),
    )
    return result.ok, list(result.findings)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("points", sorted(_MEASURED_SHIFT))
def test_a_left_aligned_font_9_record_moves_left_by_exactly_the_shift(points: int) -> None:
    # A font 2 field at the same x is written at x's own column: the font 9 one that many units left.
    reference = Text(x=50, y=10, text="REF", font=2)
    line = Text(x=50, y=20, text="5 STK", font=FONT_9, points=points)
    records = _records(_MODEL, reference, line)
    assert records[b"5 STK"][1] == records[b"REF"][1] - _MEASURED_SHIFT[points] // _DOTS_PER_UNIT


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("points", "align"), list(itertools.product(sorted(_MEASURED_SHIFT), [TextAlign.RIGHT, TextAlign.CENTER]))
)
def test_an_aligned_font_9_record_moves_back_by_its_width_and_the_shift(points: int, align: TextAlign) -> None:
    # "12 STK" is 276 dots wide at 30 pt: an even width, so the 2-dot shift moves the record a unit.
    reference = Text(x=50, y=10, text="REF", font=2)
    line = Text(x=50, y=20, text="12 STK", font=FONT_9, points=points, align=align)
    extent = text_extent(line, get_profile(_MODEL))
    share = extent if align is TextAlign.RIGHT else extent / 2
    records = _records(_MODEL, reference, line)
    assert records[b"12 STK"][1] == records[b"REF"][1] - round((share + _MEASURED_SHIFT[points]) / _DOTS_PER_UNIT)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("points", "shift"), [(6, 0), (8, 0), (10, 0), (14, 0), (36, 2), (48, 2)])
def test_an_unmeasured_size_takes_the_nearest_measured_sizes_shift(points: int, shift: int) -> None:
    line = Text(x=0, y=0, text="5", font=FONT_9, points=points)
    assert font9_print_shift_dots(line, get_profile(_MODEL)) == shift
    measured = get_profile(_MODEL).font9_print_shift
    assert measured is not None
    assert not measured.measured(points)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", sorted(set(MODEL_PROFILES) - {_MODEL}))
def test_no_other_model_moves_font_9(model: str) -> None:
    profile = get_profile(model)
    if not (profile.a_series_points or profile.old_series_points):
        pytest.skip(f"{model} has no font 9 size")
    points = max((*profile.a_series_points, *profile.old_series_points))
    reference = Text(x=50, y=10, text="REF", font=2)
    line = Text(x=50, y=20, text="5 STK", font=FONT_9, points=points)
    records = _records(model, reference, line)
    assert records[b"5 STK"][1] == records[b"REF"][1]


@pytest.mark.os_agnostic
def test_a_turned_font_9_field_is_not_moved() -> None:
    # Only upright font 9 was measured; a turned field keeps x's column.
    reference = Text(x=50, y=10, text="REF", font=2)
    line = Text(x=50, y=20, text="5 STK", font=FONT_9, points=30, rotation=Rotation.DEG_90)
    records = _records(_MODEL, reference, line)
    assert records[b"5 STK"][1] == records[b"REF"][1]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("align", list(TextAlign))
def test_the_preview_and_the_checks_place_font_9_where_it_prints(align: TextAlign) -> None:
    # The record lies one unit (2 dots) further left at 30 pt; the printer puts the glyphs 2 dots right of it.
    line = Text(x=50, y=20, text="5 STK", font=FONT_9, points=30, align=align)
    label = Label(width=100, height=55, elements=[line])
    ctx = job_context(label, RenderOptions(model=_MODEL))
    printed_at = text_column(line, ctx) * _DOTS_PER_UNIT + _MEASURED_SHIFT[30]
    rectangle = inexact_rectangle(line, ctx)
    assert rectangle is not None
    assert rectangle.left == printed_at
    assert field_area(line, Canvas(width=1, height=1, geometry=PreviewGeometry(ctx))).x == printed_at


@pytest.mark.os_agnostic
def test_a_left_font_9_field_at_the_label_edge_whose_record_cannot_go_further_left_is_refused() -> None:
    # x 0 at 30 pt: the record would need column -1, left of the printhead's first dot.
    ok, findings = _job(Text(x=0, y=10, text="5 STK", font=FONT_9, points=30))
    assert not ok
    messages = [f.message for f in findings if f.code is FindingCode.TEXT_PAST_LEFT_EDGE]
    assert messages == [
        "the Allegro 2 prints font 9 at 30 pt 2 dots right of its record, which would start left of the "
        "printhead's first dot; move x right by 0.2 mm"
    ]
    # 0.2 mm is 0.79 units, which rounds to the one unit the record needs.
    assert _job(Text(x=0.2, y=10, text="5 STK", font=FONT_9, points=30))[0]


@pytest.mark.os_agnostic
def test_a_left_font_9_field_whose_ink_reaches_the_label_edge_prints() -> None:
    # An offset of 3 mm puts the label's edge at column 12: the record goes to 11, its ink to the edge.
    ok, findings = _job(Text(x=0, y=10, text="5 STK", font=FONT_9, points=30), offset_across=3.0)
    assert ok, findings


def _named_move(findings: list[Finding]) -> float | None:
    for finding in findings:
        if finding.code is FindingCode.TEXT_PAST_LEFT_EDGE:
            named = re.search(r"move x right by (\d+\.\d) mm", finding.message)
            assert named is not None, finding.message
            return float(named.group(1))
    for finding in findings:
        if finding.code is FindingCode.OFFSET_PAST_LABEL_EDGE:
            past = re.search(r"moves this field (\d+(?:\.\d+)?) mm past the label edge", finding.message)
            assert past is not None, finding.message
            return float(past.group(1))
    return None


@pytest.mark.os_agnostic
def test_every_named_move_of_a_font_9_line_gives_a_job_that_prints() -> None:
    """Offsets either way, scales and every alignment, with the shift: the named move always clears."""
    still_refused: list[tuple[object, ...]] = []
    refusals = 0
    for offset, scale, align, points, chars, step in itertools.product(
        (-3.0, 0.0, 2.0), (1.0, 1.05, 0.95), list(TextAlign), (12, 30), (1, 4), range(10)
    ):
        text = Text(x=round(step * 0.29, 6), y=10, text="M" * chars, font=FONT_9, points=points, align=align)
        move = _named_move(_job(text, offset_across=offset, scale_across=scale)[1])
        if move is None:
            continue
        refusals += 1
        moved = text.model_copy(update={"x": round(text.x + move, 6)})
        ok, findings = _job(moved, offset_across=offset, scale_across=scale)
        if not ok:
            still_refused.append(
                (offset, scale, align.value, points, chars, text.x, move, sorted(f.code for f in findings))
            )
    assert refusals > 150, refusals  # the grid must reach the refusals it checks
    assert not still_refused, still_refused[:5]
