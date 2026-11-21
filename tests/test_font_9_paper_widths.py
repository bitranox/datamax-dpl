"""Font 9 widths against paper: every measured line must end where the Allegro 2 printed it.

The record (``docs/measurements/2026-11-21-allegro-2-CA02-03.04-font-9-widths``) holds each line
printed on 2026-11-21: its text and size, the column its record was written at, the column of the
printed reference line it was aligned to, and the gap the user read between them. A line's
predicted end is its record column plus the dots the Allegro 2 prints font 9 right of its record
(:func:`~datamax_dpl.domain.text_metrics.font9_print_shift_dots`, measured apart with the V-line
label) plus :func:`~datamax_dpl.domain.text_metrics.text_extent`; its measured end is the reference
line less the reading.
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
    FONT_9_ADVANCES,
    FONT_9_MEASURED,
    char_advances,
    font9_em_dots,
    font9_print_shift_dots,
    text_extent,
)

_RECORD = Path(__file__).parents[1] / "docs/measurements/2026-11-21-allegro-2-CA02-03.04-font-9-widths/lines.json"
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
#: taken out), in dots: whole dots at 12 pt, to about 0.15 dot at 30 pt.
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
_PAPER_30_PT = {
    "M": 70.0,
    "0": 45.47,
    "5": 45.33,
    ",": 24.67,
    " ": 24.67,
    "/": 24.67,
    "S": 54.40,
    "T": 50.33,
    "K": 54.25,
    "B": 56.12,
    "O": 64.00,
    "X": 54.40,
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("char", "dots"), sorted(_PAPER_12_PT.items()))
def test_a_measured_character_advances_its_paper_width_at_12_pt(char: str, dots: int) -> None:
    assert char_advances(Text(x=0, y=0, text=char, font=FONT_9, points=12), _PROFILE) == (dots,)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("char", "dots"), sorted(_PAPER_30_PT.items()))
def test_a_measured_character_is_its_paper_width_at_30_pt_within_half_a_dot(char: str, dots: float) -> None:
    width = FONT_9_ADVANCES[char.encode("cp850")[0]] * font9_em_dots(30, _PROFILE) / EM_UNITS
    assert width == pytest.approx(dots, abs=0.5)
