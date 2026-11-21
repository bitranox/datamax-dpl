"""Centred and right-aligned text: the field moves back by the width its line prints.

DPL places a field at its lower-left corner, so ``align`` is the encoder's arithmetic: the record's
column is ``x``'s minus the line's width in DPL units. Fonts 0-8 are measured by their cells, which
the manual gives; font 9 by widths measured on paper, or a Helvetica-Bold stand-in for the
characters not measured (``text_metrics``).
"""

from __future__ import annotations

import io
import itertools
import re
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from datamax_dpl import render_batch
from datamax_dpl.adapters.preview import render_preview_png
from datamax_dpl.domain.charset import FONT_BYTES, encode_for_bitmapped_font
from datamax_dpl.domain.decoder import explain
from datamax_dpl.domain.enums import FallbackPolicy, FindingCode, Rotation, TextAlign
from datamax_dpl.domain.label import FONT_9, Counter, Label, Line, Text
from datamax_dpl.domain.preview import canvas_for, field_area, letter_spans
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.render import RenderResult, build_job, render
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
    assert _column(Text(x=50, y=10, text="ABC")) == _column(Text(x=50, y=10, text="ABC", align=TextAlign.LEFT))


@pytest.mark.os_agnostic
def test_a_right_aligned_font_2_line_moves_back_by_its_cells_less_the_last_gap() -> None:
    # Font 2 at 203 dpi: cells 10 dots wide with a 2-dot gap, so ABC spans 3 * 12 - 2 = 34 dots.
    left = _column(Text(x=50, y=10, text="ABC", font=2))
    assert left - _column(Text(x=50, y=10, text="ABC", font=2, align=TextAlign.RIGHT)) == 34 // _DOTS_PER_UNIT
    # Centred: half of 34 dots is 8.5 units, rounded to the even 8.
    assert left - _column(Text(x=50, y=10, text="ABC", font=2, align=TextAlign.CENTER)) == 8


@pytest.mark.os_agnostic
def test_a_width_multiplier_of_fonts_0_to_8_widens_the_shift_with_the_cells() -> None:
    left = _column(Text(x=50, y=10, text="ABC", font=2, width_multiplier=2))
    right = _column(Text(x=50, y=10, text="ABC", font=2, width_multiplier=2, align=TextAlign.RIGHT))
    assert left - right == (3 * 24 - 4) // _DOTS_PER_UNIT


@pytest.mark.os_agnostic
def test_font_9_moves_back_by_each_letters_own_width() -> None:
    # 8 pt at 203 dpi is an em of 22.56 dots: i advances 278/1000 em (6 dots) and its ink ends
    # 69/1000 em (2 dots) before its advance; W advances 916.3/1000 (21 dots), its ink 0 dots short.
    narrow = Text(x=95, y=10, text="iiiiiiii", font=FONT_9, points=8)
    wide = Text(x=95, y=10, text="WWWWWWWW", font=FONT_9, points=8)
    assert _column(narrow) - _column(narrow.model_copy(update={"align": TextAlign.RIGHT})) == (48 - 2) // _DOTS_PER_UNIT
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
        width=100,
        height=55,
        elements=[Text(x=95, y=10, text="{qty} STK", font=FONT_9, points=30, align=TextAlign.RIGHT)],
    )
    batch = render_batch(label, [{"qty": "5"}, {"qty": "12345,5"}], RenderOptions(model=_MODEL))
    assert all(result.ok for result in batch.results)
    short = _columns(batch.results[0].data)[b"5 STK"]
    long = _columns(batch.results[1].data)[b"12345,5 STK"]
    profile = get_profile(_MODEL)
    widths = [
        text_extent(Text(x=0, y=0, text=text, font=FONT_9, points=30), profile) for text in ("5 STK", "12345,5 STK")
    ]
    assert short - long == pytest.approx((widths[1] - widths[0]) / _DOTS_PER_UNIT, abs=1)


@pytest.mark.os_agnostic
def test_a_line_that_would_start_left_of_the_label_is_refused() -> None:
    label = Label(
        width=100, height=55, elements=[Text(x=5, y=10, text="WWWWWWWW", font=FONT_9, points=30, align=TextAlign.RIGHT)]
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
    element = Text(x=95, y=10, text="12345,5 STK", font=FONT_9, points=30, align=TextAlign.RIGHT)
    label = Label(width=100, height=55, elements=[element])
    canvas = canvas_for(label, get_profile(_MODEL))
    area = field_area(element, canvas)
    end, x_dot = area.x + area.length, canvas.point(element.x, element.y).x
    # The shift rounds to whole DPL units: the line ends within one unit of x's dot.
    assert abs(end - x_dot) <= _DOTS_PER_UNIT
    assert area.length == sum(char_advances(element, get_profile(_MODEL)))


@pytest.mark.os_agnostic
def test_the_label_edge_check_reads_the_aligned_position() -> None:
    def edge_findings(align: TextAlign) -> set[FindingCode]:
        text = Text(x=99, y=10, text="BOX 12/12", font=FONT_9, points=30, align=align)
        return _codes(render(Label(width=100, height=55, elements=[text]), RenderOptions(model=_MODEL)).findings)

    assert FindingCode.ELEMENT_PAST_LABEL_EDGE in edge_findings(TextAlign.LEFT)
    assert FindingCode.ELEMENT_PAST_LABEL_EDGE not in edge_findings(TextAlign.RIGHT)


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


@pytest.mark.os_agnostic
@pytest.mark.parametrize("font", range(9))
def test_a_right_aligned_line_at_the_label_edge_does_not_pass_it(font: int) -> None:
    """The blank gap after the last character of fonts 0-8 prints no dot, so it is not the line.

    Width multiplier 4 widens every gap past the edge check's tolerance (one unit plus one dot, 3
    Allegro 2 dots): at 1, the 1- and 2-dot gaps of fonts 0-3 hide inside it and the check would
    pass with the gap counted as line.
    """
    for policy in (FallbackPolicy.DEGRADE, FallbackPolicy.STRICT):
        text = Text(x=100, y=10, text="12345", font=font, align=TextAlign.RIGHT, width_multiplier=4)
        label = Label(width=100, height=55, elements=[text])
        result = render(label, RenderOptions(model=_MODEL, policy=policy))
        assert FindingCode.ELEMENT_PAST_LABEL_EDGE not in _codes(result.findings), (font, policy)


def _font_2_job(
    chars: int, *, offset_across: float, x: float = 10, scale_across: float = 1.0, align: TextAlign = TextAlign.RIGHT
) -> RenderResult:
    # Font 2 at 203 dpi: 12 dots per character less a 2-dot gap; x 10 mm is 40 units of 2 dots.
    text = Text(x=x, y=10, text="A" * chars, font=2, align=align)
    return build_job(
        Label(width=100, height=55, elements=[text]),
        RenderOptions(model=_MODEL, offset_across=offset_across, scale_across=scale_across),
    )


def _font_2_line(
    chars: int, *, offset_across: float, x: float = 10, scale_across: float = 1.0, align: TextAlign = TextAlign.RIGHT
) -> tuple[bool, list[Finding]]:
    result = _font_2_job(chars, offset_across=offset_across, x=x, scale_across=scale_across, align=align)
    return result.ok, list(result.findings)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("offset_across", [3.0, -3.0])
def test_the_left_edge_refusal_measures_from_the_labels_edge_not_the_printheads(offset_across: float) -> None:
    # Eight characters span 94 dots (47 units): the line starts 7 units (1.75 mm) left of the label
    # whichever way the printer's offset moves the label on the printhead.
    ok, findings = _font_2_line(8, offset_across=offset_across)
    assert not ok
    refusal = next(f for f in findings if f.code is FindingCode.TEXT_PAST_LEFT_EDGE)
    assert "about 1.8 mm left of the label's left edge" in refusal.message


@pytest.mark.os_agnostic
def test_a_line_on_the_label_but_left_of_the_printhead_is_refused_like_a_left_field() -> None:
    # Six characters span 70 dots (35 units): with offset_across -3 mm the line starts 7 units left of
    # the printhead's first column but 5 units right of the label's edge, as a left-aligned field
    # moved there by the offset would: the offset is refused, not the alignment.
    ok, findings = _font_2_line(6, offset_across=-3.0)
    assert not ok
    codes = _codes(findings)
    assert FindingCode.TEXT_PAST_LEFT_EDGE not in codes
    assert FindingCode.OFFSET_PAST_LABEL_EDGE in codes


_EDGE_CODES = {FindingCode.TEXT_PAST_LEFT_EDGE, FindingCode.OFFSET_PAST_LABEL_EDGE}


def _edge_messages(findings: Iterable[Finding]) -> list[tuple[FindingCode, str]]:
    return [(finding.code, finding.message) for finding in findings if finding.code in _EDGE_CODES]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("x", "chars", "past"),
    [
        # x 1 mm with offset -3 mm is 8 units left of the printhead; four characters (23 units)
        # start 31 units left of it, 19 units (4.75 mm) left of the label's edge at -12.
        (1.0, 4, "about 4.8 mm"),
        # One character (5 units) from x 1 mm starts 13 units left: 1 unit (0.25 mm) past the edge.
        (1.0, 1, "about 0.3 mm"),
        # x 2.5 mm is 2 units left of the printhead; two characters (11 units) start 1 unit past the edge.
        (2.5, 2, "about 0.3 mm"),
    ],
)
def test_a_line_whose_x_is_left_of_the_printhead_is_measured_from_its_true_start(
    x: float, chars: int, past: str
) -> None:
    ok, findings = _font_2_line(chars, offset_across=-3.0, x=x)
    assert not ok
    edges = _edge_messages(findings)
    assert len(edges) == 1, edges
    [(code, message)] = edges
    assert code is FindingCode.TEXT_PAST_LEFT_EDGE
    assert f"{past} left of the label's left edge" in message


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("x", "chars", "offset_across", "scale_across"),
    [
        (1.0, 4, -3.0, 1.0),
        (1.0, 1, -3.0, 1.0),
        (2.5, 2, -3.0, 1.0),
        (10.0, 8, -3.0, 1.0),
        # A scale across stretches what x asks for: the printed dots of the overhang fall short of the move.
        (0.148, 2, 0.0, 1.05),
    ],
)
def test_moving_x_right_by_the_reported_distance_puts_the_line_on_the_label(
    x: float, chars: int, offset_across: float, scale_across: float
) -> None:
    _, findings = _font_2_line(chars, offset_across=offset_across, x=x, scale_across=scale_across)
    move = _reported_move(findings)
    assert move is not None
    ok, moved = _font_2_line(chars, offset_across=offset_across, x=x + move, scale_across=scale_across)
    assert ok, moved


def _reported_move(findings: Iterable[Finding]) -> float | None:
    """The distance a text_past_left_edge refusal tells x to move, or None without one."""
    refusal = next((f for f in findings if f.code is FindingCode.TEXT_PAST_LEFT_EDGE), None)
    if refusal is None:
        return None
    named = re.search(r"move x right by about (\d+\.\d) mm", refusal.message)
    past = named or re.search(
        r"about (\d+\.\d) mm left of the label's left edge; move x right by that", refusal.message
    )
    assert past is not None, refusal.message
    return float(past.group(1))


@pytest.mark.os_agnostic
def test_every_refused_line_moved_by_the_reported_distance_prints() -> None:
    """Over offsets either way, scales and both alignments, the named move always gives a job that prints."""
    still_refused: list[tuple[float, float, str, int, float, float, list[FindingCode]]] = []
    refusals = 0
    for offset, scale, align, chars, step in itertools.product(
        (-3.0, 0.0, 2.0), (1.0, 1.05, 0.95), (TextAlign.RIGHT, TextAlign.CENTER), (1, 4, 8, 13), range(12)
    ):
        x = step * 0.53
        move = _reported_move(_font_2_line(chars, offset_across=offset, x=x, scale_across=scale, align=align)[1])
        if move is None:
            continue
        refusals += 1
        ok, moved = _font_2_line(chars, offset_across=offset, x=x + move, scale_across=scale, align=align)
        if not ok:
            still_refused.append((offset, scale, align.value, chars, x, move, sorted(_codes(moved))))
    assert refusals > 300, refusals  # the grid must reach the refusal it checks
    assert not still_refused, still_refused[:5]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("offset_across", "column"), [(2.0, b"0008"), (-3.0, b"0000"), (0.0, b"0000")])
def test_a_refused_line_is_written_at_the_label_edge_or_the_printheads_first_column(
    offset_across: float, column: bytes
) -> None:
    # Right-aligned "AAAA" (23 units) at x 1 mm starts left of the label whatever the offset. Its
    # record goes to the later of the label's edge (8 units in with offset 2 mm, 12 out with -3 mm)
    # and the printhead's first column, so the job still renders for the refusal.
    result = _font_2_job(4, offset_across=offset_across, x=1.0)
    assert not result.ok
    [record] = [record for record in result.data.split(b"\r") if record.endswith(b"AAAA")]
    assert record[11:15] == column


@pytest.mark.os_agnostic
def test_a_line_whose_x_and_start_are_both_left_of_the_printhead_gets_one_offset_finding() -> None:
    # x 2.5 mm is 2 units left of the printhead; one character (5 units) starts 7 units (1.75 mm)
    # left of it and 5 units right of the label's edge: one finding, for where the line starts.
    _, findings = _font_2_line(1, offset_across=-3.0, x=2.5)
    edges = _edge_messages(findings)
    assert len(edges) == 1, edges
    [(code, message)] = edges
    assert code is FindingCode.OFFSET_PAST_LABEL_EDGE
    assert "moves this field 1.75 mm past the label edge" in message


@pytest.mark.os_agnostic
def test_an_unfilled_template_is_not_refused_for_the_width_of_its_braces() -> None:
    """Without data the field prints its template; only filled records are measured for the edge."""
    text = Text(x=40, y=10, text="{qty} STK", font=FONT_9, points=30, align=TextAlign.RIGHT)
    label = Label(width=100, height=55, elements=[text])
    template = render(label, RenderOptions(model=_MODEL))
    codes = _codes(template.findings)
    assert FindingCode.PLACEHOLDERS_UNFILLED in codes
    assert FindingCode.TEXT_PAST_LEFT_EDGE not in codes
    assert template.ok
    batch = render_batch(label, [{"qty": "5"}, {"qty": "12345,5"}], RenderOptions(model=_MODEL))
    assert batch.results[0].ok
    assert FindingCode.TEXT_PAST_LEFT_EDGE in _codes(batch.results[1].findings)


@pytest.mark.os_agnostic
def test_the_preview_ends_an_aligned_line_at_x_under_a_scale_across() -> None:
    element = Text(x=95, y=10, text="12345,5 STK", font=FONT_9, points=30, align=TextAlign.RIGHT)
    label = Label(width=100, height=55, elements=[element])
    canvas = canvas_for(label, get_profile(_MODEL), options=RenderOptions(model=_MODEL, scale_across=1.05))
    area = field_area(element, canvas)
    anchor = canvas.point(element.x, element.y).x
    assert abs(area.x + area.length - anchor) <= _DOTS_PER_UNIT


@pytest.mark.os_agnostic
def test_font_9_prints_code_page_850() -> None:
    """The manual's font 9 set is code page 850: its accented capitals, never 437's box drawing."""
    cent, a_acute, copyright_sign, times = chr(0xA2), chr(0xC1), chr(0xA9), chr(0xD7)
    encoded = encode_for_bitmapped_font(cent + a_acute + copyright_sign + times, font=FONT_9)
    assert encoded.data == bytes([0xBD, 0xB5, 0xB8, 0x9E])
    assert encoded.unmappable == ()
    # A box-drawing character of code page 437 is outside font 9.
    assert encode_for_bitmapped_font(chr(0x2502), font=FONT_9).unmappable == (chr(0x2502),)
    # Fonts 1 and 2 stay code page 437, where the cent sign is byte 155.
    assert encode_for_bitmapped_font(cent, font=1).data == bytes([155])


@pytest.mark.os_agnostic
def test_every_font_9_character_has_its_own_stand_in_width() -> None:
    # Code page 850 has a Helvetica-Bold glyph for every byte of the set: no fallback width.
    assert FONT_9_ADVANCES[0xB5] == 722  # A acute, as wide as A
    assert FONT_9_ADVANCES[0xBD] == 556  # cent
    assert FONT_9_ADVANCES[0xB8] == 737  # copyright


@pytest.mark.os_agnostic
def test_a_font_9_record_reads_back_in_code_page_850() -> None:
    text = Text(x=5, y=10, text="Ch. " + chr(0xC1) + chr(0xA2), font=FONT_9, points=12)
    result = render(Label(width=100, height=55, elements=[text]), RenderOptions(model=_MODEL))
    assert result.ok
    rebuilt = explain(result.data).label
    assert rebuilt is not None
    texts = [element.text for element in rebuilt.elements if isinstance(element, Text)]
    assert texts == [text.text]
