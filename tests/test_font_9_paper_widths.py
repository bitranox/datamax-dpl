"""Font 9 widths against paper: every measured line must end where the Allegro 2 printed it.

The record (``docs/measurements/2026-11-21-allegro-2-CA02-03.04-font-9-widths``) holds each line
printed on 2026-11-21: its text and size, the column its record was written at, the column of the
printed reference line it was aligned to, and the gap the user read between them. A line's
predicted end is its record column plus :func:`~datamax_dpl.domain.text_metrics.text_extent`; its
measured end is the reference line less the reading.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter

from datamax_dpl.domain.label import FONT_9, Text
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.text_metrics import FONT_9_MEASURED, text_extent

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
        extent = text_extent(Text(x=0, y=0, text=self.text, font=FONT_9, points=self.points), _PROFILE)
        return (self.text_column * _DOTS_PER_UNIT + extent) / _DOTS_PER_MM

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
