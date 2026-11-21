"""Font 9 widths against paper: every measured line must end where the Allegro 2 printed it.

The record (``docs/measurements/2026-11-21-allegro-2-CA02-03.04-font-9-widths``) holds each line
printed on 2026-11-21: its text and size, the column its record was written at, the column of the
printed reference line it was aligned to, and the gap the user read between them. A line's
predicted end is its record column plus the dots the Allegro 2 prints font 9 right of its record
(:func:`~datamax_dpl.domain.text_metrics.font9_print_shift_dots`, measured apart with the V-line
label) plus :func:`~datamax_dpl.domain.text_metrics.text_extent`; its measured end is the reference
line less the reading.

The fill-and-V round (``fill.json`` in the same record) adds 12, 18, 24 and 30 pt: each row repeats
one character N times before a final M, and the reading says how many vertical lines (0.5 mm apart)
the M's ink middle sits off the middle line. The row's record columns and its reading give how far
the N characters reached, independently of the widths shipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter

from datamax_dpl.domain.label import FONT_9, Text
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.text_metrics import (
    EM_UNITS,
    FONT_9_MEASURED,
    FONT_9_PRINTED_ADVANCES,
    char_advances,
    font9_em_dots,
    font9_print_shift_dots,
    text_extent,
)

_RECORD_DIR = Path(__file__).parents[1] / "docs/measurements/2026-11-21-allegro-2-CA02-03.04-font-9-widths"
_RECORD = _RECORD_DIR / "lines.json"
_PROFILE = get_profile("allegro-2")
_DOTS_PER_MM = _PROFILE.dpi / 25.4
#: The Allegro 2 prints 2 dots per 1/100 in, the unit the record columns are in.
_DOTS_PER_UNIT = 2
#: Readings are by eye to 0.1 mm against a reference line 0.25 mm wide; the two lines of M alone at
#: 12 pt, on different labels, came out 0.3 mm apart.
_TOLERANCE_MM = 0.35
_LABEL_WIDTH_MM = 100.0


class _PaperLine(BaseModel):
    """One printed line of the record."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    label: str
    text: str
    points: int
    line_column: int
    text_column: int
    reading: str
    quality: Literal["ok", "uncertain", "off_label"]
    characters: Literal["measured", "has_unmeasured"]

    @property
    def name(self) -> str:
        return f"{self.label}:{self.points}pt:{self.text[:1]!r}{len(self.text)}"

    def predicted_end_mm(self) -> float:
        printed = Text(x=0, y=0, text=self.text, font=FONT_9, points=self.points)
        shift = font9_print_shift_dots(printed, _PROFILE)
        return (self.text_column * _DOTS_PER_UNIT + shift + text_extent(printed, _PROFILE)) / _DOTS_PER_MM

    def measured_end_mm(self) -> float:
        """The reference line less the reading: S (short) positive, O (over) negative."""
        value = self.reading.rstrip("?")
        gap = 0.0 if value == "0" else (1 if value[0] == "S" else -1) * float(value[1:])
        return self.line_column * _DOTS_PER_UNIT / _DOTS_PER_MM - gap


_LINES = TypeAdapter(list[_PaperLine]).validate_json(_RECORD.read_bytes())
_CHECKED = [line for line in _LINES if line.characters == "measured" and line.quality == "ok"]
_OFF_LABEL = [line for line in _LINES if line.quality == "off_label"]


@pytest.mark.os_agnostic
def test_the_record_holds_every_measured_line() -> None:
    assert (len(_LINES), len(_CHECKED), len(_OFF_LABEL)) == (75, 56, 4)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("line", _CHECKED, ids=[line.name for line in _CHECKED])
def test_a_measured_line_ends_where_it_printed(line: _PaperLine) -> None:
    assert line.predicted_end_mm() == pytest.approx(line.measured_end_mm(), abs=_TOLERANCE_MM)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("line", _OFF_LABEL, ids=[line.name for line in _OFF_LABEL])
def test_a_line_that_ran_off_the_label_is_predicted_past_its_edge(line: _PaperLine) -> None:
    assert line.predicted_end_mm() > _LABEL_WIDTH_MM


@pytest.mark.os_agnostic
def test_the_measured_bytes_are_the_characters_of_the_measured_lines() -> None:
    printed = {byte for line in _LINES if line.characters == "measured" for byte in line.text.encode("cp850")}
    assert printed == FONT_9_MEASURED


#: Widths read off the paper per character (lines of one character then an M, the M's own error
#: taken out), in whole dots at 12 pt.
_PAPER_12_PT = {
    **dict.fromkeys("0123456789", 18),
    "M": 28,
    **dict.fromkeys("AB", 23),
    **dict.fromkeys("CDR", 24),
    **dict.fromkeys("EPSVXK", 22),
    **dict.fromkeys("FTZ", 20),
    **dict.fromkeys("GOQ", 26),
    **dict.fromkeys("HNU", 25),
    "I": 9,
    "J": 17,
    "L": 19,
    "W": 31,
    "Y": 23,
    "-": 10,
    **dict.fromkeys(",. ", 10),
    "/": 11,
}
#: Dots the Allegro 2 prints font 9 at 30 pt right of its record, measured apart (the V-line label).
_SHIFT_30_PT = 2
#: M's right side bearing in Adobe's Helvetica-Bold AFM (WX 833, bbox right 765), thousandths of the em.
_M_RIGHT_BEARING = 68


def _paper_widths_30_pt() -> dict[str, float]:
    """Each character's 30 pt width read off its line in the record, in exact dots.

    The line of M alone gives M: its ink span from the record, less the print shift, plus M's right
    side bearing, over the count. A line of one character then an M gives that character the same
    way with M's advance taken out. No rounding is modelled, so a width can differ from the table by
    the half dot the reached positions round away, spread over the line.
    """
    em = font9_em_dots(30, _PROFILE)
    bearing = _M_RIGHT_BEARING * em / EM_UNITS
    lines = [line for line in _CHECKED if line.points == 30]

    def ink(line: _PaperLine) -> float:
        return line.measured_end_mm() * _DOTS_PER_MM - line.text_column * _DOTS_PER_UNIT - _SHIFT_30_PT

    (only_m,) = [line for line in lines if set(line.text) == {"M"}]
    m_width = (ink(only_m) + bearing) / len(only_m.text)
    widths = {"M": m_width}
    for line in lines:
        if line is not only_m:
            assert line.text[-1] == "M" and len(set(line.text[:-1])) == 1, line.text
            widths[line.text[0]] = (ink(line) + bearing - m_width) / (len(line.text) - 1)
    return widths


_PAPER_30_PT = _paper_widths_30_pt()


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("char", "dots"), sorted(_PAPER_12_PT.items()))
def test_a_measured_character_advances_its_paper_width_at_12_pt(char: str, dots: int) -> None:
    assert char_advances(Text(x=0, y=0, text=char, font=FONT_9, points=12), _PROFILE) == (dots,)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("model", "points", "dots"),
    [("allegro-2", 12, 11), ("allegro-2", 6, 5), ("allegro-2", 10, 8), ("allegro-2", 14, 12), ("dmx-600", 12, 15)],
)
def test_the_11_dot_slash_holds_at_203_dpi_and_12_pt_only(model: str, points: int, dots: int) -> None:
    # Its 12 pt line gave "/" 11 dots, more than its width (9.92 dots at 12 pt) rounds to. Every other
    # size, and 12 pt at another resolution, scales the width: 6 pt 4.96, 10 pt 8.27, 14 pt 11.58
    # dots, and 14.66 dots at 300 dpi.
    assert char_advances(Text(x=0, y=0, text="/", font=FONT_9, points=points), get_profile(model)) == (dots,)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("char", "dots"), sorted(_PAPER_30_PT.items()), ids=sorted(_PAPER_30_PT))
def test_a_measured_character_is_its_paper_width_at_30_pt_within_a_tenth_of_a_dot(char: str, dots: float) -> None:
    # The digits share the mean of 0 and 5, which read 0.13 dots apart; every other width matches
    # its line to 0.003 dots.
    assert FONT_9_PRINTED_ADVANCES[30][char.encode("cp850")[0]] == pytest.approx(dots, abs=0.1)


@pytest.mark.os_agnostic
def test_the_30_pt_paper_widths_cover_every_character_measured_at_30_pt() -> None:
    assert sorted(_PAPER_30_PT) == sorted("M05, /STKBOX")


class _FillRow(BaseModel):
    """One row of the fill-and-V round: ``char`` N times, then an M right-aligned at x 95 mm."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    line: int
    label: str
    points: int
    char: str
    count: int
    reading: float
    text_column: int
    line_column: int

    @property
    def name(self) -> str:
        return f"{self.line}:{self.points}pt:{self.char!r}"

    def reached_dots(self) -> float:
        """How far the N characters reached on paper, from the text record's start, in dots.

        The middle vertical line's ink middle, less the reading (lines 4 dots apart, + = the M must
        move right), is where the M's ink middle printed. Less the text record's start, the print
        shift and the M's ink middle beyond its origin, that leaves the N advances.
        """
        line_middle = self.line_column * _DOTS_PER_UNIT + _LINE_RECORD_MIDDLE
        m_ink_middle = _M_INK_MIDDLE * self.points * _PROFILE.dpi / 72 / EM_UNITS
        shift = _SHIFT_30_PT if self.points == 30 else 0
        start = self.text_column * _DOTS_PER_UNIT + shift
        return line_middle - _DOTS_PER_V_LINE * self.reading - start - m_ink_middle


#: A line record is one unit (2 dots) wide, so its middle lies 1 dot right of its column.
_LINE_RECORD_MIDDLE = 1
#: The five vertical lines lie 0.5 mm, 2 units, 4 dots apart; readings come in half lines.
_DOTS_PER_V_LINE = 4
_HALF_V_LINE = _DOTS_PER_V_LINE / 2
#: M's ink middle right of its origin in the AFM: (llx 69 + urx 765) / 2, thousandths of the em.
_M_INK_MIDDLE = (69 + 765) / 2
#: Label f23 read the very readings of label f13 and is left out (the record's README).
_REPEATED_LABEL = "fill/f23-24pt-lines-118-122.json"
_FILL = TypeAdapter(list[_FillRow]).validate_json((_RECORD_DIR / "fill.json").read_bytes())
#: At 30 pt the characters the line-end round measured keep its finer widths, checked above.
_FILL_CHECKED = [
    row for row in _FILL if row.label != _REPEATED_LABEL and not (row.points == 30 and row.char in _PAPER_30_PT)
]


@pytest.mark.os_agnostic
def test_the_fill_round_holds_every_row_and_checks_all_but_the_repeated_label_and_the_line_end_characters() -> None:
    assert (len(_FILL), len(_FILL_CHECKED)) == (164, 147)
    assert {(row.points, row.char) for row in _FILL} == {
        (points, chr(byte)) for points in (12, 18, 24, 30) for byte in FONT_9_MEASURED
    }


@pytest.mark.os_agnostic
@pytest.mark.parametrize("row", _FILL_CHECKED, ids=[row.name for row in _FILL_CHECKED])
def test_a_fill_row_gives_the_advance_datamax_dpl_prints_within_its_resolution(row: _FillRow) -> None:
    # A half line (2 dots) over N characters is what one reading resolves. The advances are the
    # printed ones, rounded as the printer rounds, so they compare with the paper as they are.
    printed = char_advances(Text(x=0, y=0, text=row.char * row.count, font=FONT_9, points=row.points), _PROFILE)
    assert sum(printed) / row.count == pytest.approx(row.reached_dots() / row.count, abs=_HALF_V_LINE / row.count)
