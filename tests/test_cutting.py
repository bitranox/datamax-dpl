"""Cutting: which labels the cutter cuts, where it stops, and how copies are grouped between cuts.

Measured 2026-11-06 on one Allegro 2 (firmware CA02 03.04) with its cutter fitted, one print per
setting: ``<STX>V1`` cut every label and ``<STX>V0`` cut none; the stop ``<STX>f302`` (76.8 mm)
cut in the gap where the tear-off stop ``<STX>f263`` cut 10 mm short; and ``:0003`` with three
copies (``Q0003``) cut once, after the third, although the manual (p.64) asks for an amount
smaller than the quantity.
"""

from __future__ import annotations

import pytest

from datamax_dpl.application.use_cases import render_batch
from datamax_dpl.domain.enums import FindingCode, Severity
from datamax_dpl.domain.label import Cut, Label, Text
from datamax_dpl.domain.render import RenderResult, build_job
from datamax_dpl.domain.render_context import RenderOptions

STX = b"\x02"
CUT_NOW = STX + b"o"


def _label(**fields: object) -> Label:
    return Label.model_validate({"elements": [Text(x=5, y=5, text="Hi")], **fields})


def _job(label: Label, **options: object) -> RenderResult:
    return build_job(label, RenderOptions.model_validate(options))


def _lines(result: RenderResult) -> list[bytes]:
    return result.data.split(b"\r")


def _messages(result: RenderResult, code: FindingCode) -> list[str]:
    return [finding.message for finding in result.findings if finding.code is code]


# ======================== Copies between cuts ========================


@pytest.mark.parametrize(("every", "quantity"), [(3, 3), (5, 3), (2, 1)])
def test_a_cut_every_at_or_past_the_quantity_cuts_once_after_the_last_copy(every: int, quantity: int) -> None:
    result = _job(_label(quantity=quantity, cut=Cut(every=every)), model="allegro-2", cutter_installed=True)
    assert f":{quantity:04d}".encode() in _lines(result)
    assert CUT_NOW not in result.data
    assert result.ok


def test_a_cut_every_below_the_quantity_counts_groups_and_cuts_the_rest() -> None:
    result = _job(_label(quantity=5, cut=Cut(every=2)), model="allegro-2", cutter_installed=True)
    assert b":0002" in _lines(result)
    assert result.data.count(CUT_NOW) == 1


def test_a_model_without_cut_by_says_it_cuts_every_label_when_one_cut_is_asked_for() -> None:
    result = _job(_label(quantity=3, cut=Cut(every=3)), model="prodigy", cutter_installed=True)
    assert [f.severity for f in result.findings if f.code is FindingCode.CUT_BY_UNSUPPORTED] == [Severity.WARNING]
    assert b":0003" not in _lines(result)


# ======================== The cutter stop ========================


def test_an_allegro_2_label_that_cuts_stops_at_the_measured_cutter_position() -> None:
    result = _job(_label(cut=Cut()), model="allegro-2", cutter_installed=True)
    assert STX + b"f302" in result.data
    [message] = _messages(result, FindingCode.MODEL_DEFAULTS_APPLIED)
    assert "cutter_stop_position 76.8 mm" in message
    assert "stop_position 66.8 mm" not in message


def test_an_allegro_2_label_that_does_not_cut_stops_at_tear_off_and_names_no_cutter_stop() -> None:
    result = _job(_label(), model="allegro-2", cutter_installed=True)
    assert STX + b"f263" in result.data
    assert STX + b"f302" not in result.data
    assert all("cutter_stop_position" not in m for m in _messages(result, FindingCode.MODEL_DEFAULTS_APPLIED))


def test_a_configured_cutter_stop_wins_over_the_model_default() -> None:
    result = _job(_label(cut=Cut()), model="allegro-2", cutter_installed=True, cutter_stop_position_mm=80)
    assert STX + b"f315" in result.data
    assert all("cutter_stop_position" not in m for m in _messages(result, FindingCode.MODEL_DEFAULTS_APPLIED))


def test_the_label_s_own_stop_wins_over_the_cutter_default() -> None:
    result = _job(_label(cut=Cut(), stop_position=50), model="allegro-2", cutter_installed=True)
    assert STX + b"f302" not in result.data
    assert all("cutter_stop_position" not in m for m in _messages(result, FindingCode.MODEL_DEFAULTS_APPLIED))


def test_a_model_with_no_measured_cutter_stop_sends_none_for_a_cutting_label() -> None:
    result = _job(_label(cut=Cut()), model="dmx-600", cutter_installed=True, label_present=False)
    assert STX + b"f" not in result.data


# ======================== A default cut for labels that set none ========================


def test_cut_every_gives_a_label_without_a_cut_its_cut() -> None:
    result = _job(_label(quantity=3), model="allegro-2", cutter_installed=True, cut_every=3)
    assert STX + b"V1" in result.data
    assert STX + b"f302" in result.data
    assert b":0003" in _lines(result)


def test_the_label_s_own_cut_wins_over_cut_every() -> None:
    result = _job(_label(quantity=4, cut=Cut(every=2)), model="allegro-2", cutter_installed=True, cut_every=3)
    assert b":0002" in _lines(result)
    assert b":0003" not in _lines(result)


def test_without_cut_every_a_label_without_a_cut_is_not_cut() -> None:
    result = _job(_label(quantity=3), model="allegro-2", cutter_installed=True)
    assert STX + b"V0" in result.data
    assert not any(line.startswith(b":") for line in _lines(result))


def test_cut_every_applies_across_the_records_of_a_batch() -> None:
    label = Label.model_validate({"elements": [{"type": "text", "x": 5, "y": 5, "text": "{n}"}]})
    records = [{"n": "1"}, {"n": "2"}, {"n": "3"}]
    batch = render_batch(label, records, RenderOptions(model="allegro-2", cutter_installed=True, cut_every=2))
    assert batch.data.count(CUT_NOW) == 2


@pytest.mark.parametrize("value", [0, 10000])
def test_cut_every_takes_what_a_label_s_cut_takes(value: int) -> None:
    with pytest.raises(ValueError, match="cut_every"):
        RenderOptions(cut_every=value)
