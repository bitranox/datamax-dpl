"""Cutting: which labels the cutter cuts, where it stops, and how copies are grouped between cuts.

Measured 2026-11-06 on one Allegro 2 (firmware CA02 03.04) with its cutter fitted, one print per
setting: ``<STX>V1`` cut every label and ``<STX>V0`` cut none; the stop ``<STX>f302`` (76.8 mm)
cut in the gap where the tear-off stop ``<STX>f263`` cut 10 mm short; and ``:0003`` with three
copies (``Q0003``) cut once, after the third, although the manual (p.64) asks for an amount
smaller than the quantity.

Measured 2026-11-09 on the same printer, five records, one format each: ``<STX>V1`` on every record
cut every label, whatever ``<STX>o`` followed; ``<STX>V0`` on every record with ``<STX>o`` after some
cut nothing; ``<STX>V1`` right before ``<STX>o`` cut twice; ``<STX>V1`` only on the records a cut
should follow, and no ``<STX>o``, cut once after each of them (printed twice, 2 + 2 + 1 labels).
"""

from __future__ import annotations

import pytest

from datamax_dpl.application.use_cases import check_batch, record_renders, render_batch
from datamax_dpl.domain.enums import FindingCode, PrinterSetting, Severity
from datamax_dpl.domain.label import Cut, JoinedLabel, Label, Text
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
    result = _job(_label(), model="allegro-2", cutter_installed=True, cut_after_print=False)
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


def test_without_cut_every_or_cut_after_print_a_label_without_a_cut_is_not_cut() -> None:
    result = _job(_label(quantity=3), model="allegro-2", cutter_installed=True, cut_after_print=False)
    assert STX + b"V0" in result.data
    assert not any(line.startswith(b":") for line in _lines(result))


def test_cut_every_applies_across_the_records_of_a_batch() -> None:
    label = Label.model_validate({"elements": [{"type": "text", "x": 5, "y": 5, "text": "{n}"}]})
    batch = render_batch(label, _records(3), RenderOptions(model="allegro-2", cutter_installed=True, cut_every=2))
    assert _switches(batch.data) == [b"V0", b"V1", b"V1"]


# ======================== A batch cutting across its records ========================

_ALLEGRO_WITH_CUTTER = RenderOptions(model="allegro-2", cutter_installed=True)


def _records(count: int) -> list[dict[str, str]]:
    return [{"n": str(number)} for number in range(1, count + 1)]


def _batch_label(**cut: object) -> Label:
    return Label.model_validate({"elements": [{"type": "text", "x": 5, "y": 5, "text": "{n}"}], "cut": cut})


def _switches(job: bytes) -> list[bytes]:
    """Each record's ``<STX>V`` command, in record order."""
    return [record.split(STX + b"V", 1)[1][:1].join((b"V", b"")) for record in job.split(STX + b"n")[1:]]


@pytest.mark.parametrize(
    ("cut", "records", "expected"),
    [
        ({"every": 2}, 5, [b"V0", b"V1", b"V0", b"V1", b"V1"]),
        ({"every": 2}, 4, [b"V0", b"V1", b"V0", b"V1"]),
        ({"every": 3}, 2, [b"V0", b"V1"]),
        ({"every": 1}, 3, [b"V1", b"V1", b"V1"]),
    ],
)
def test_a_batch_switches_the_cutter_on_only_for_the_records_a_cut_follows(
    cut: dict[str, object], records: int, expected: list[bytes]
) -> None:
    batch = render_batch(_batch_label(**cut), _records(records), _ALLEGRO_WITH_CUTTER)
    assert _switches(batch.data) == expected


def test_a_batch_that_switches_the_cutter_sends_no_cut_command() -> None:
    # V1 right before <STX>o cut twice on paper, and V1 alone already cut once.
    batch = render_batch(_batch_label(every=2), _records(5), _ALLEGRO_WITH_CUTTER)
    assert CUT_NOW not in batch.data


def _stops(job: bytes) -> list[bytes]:
    """Each record's ``<STX>f`` command, in record order."""
    return [record.split(STX + b"f", 1)[1][:3].join((b"f", b"")) for record in job.split(STX + b"n")[1:]]


def test_the_records_a_cut_follows_stop_where_the_cutter_cuts_and_the_others_run_on() -> None:
    batch = render_batch(_batch_label(every=2), _records(5), _ALLEGRO_WITH_CUTTER)
    assert _stops(batch.data) == [b"f100", b"f302", b"f100", b"f302", b"f302"]


def test_a_record_with_the_cutter_off_carries_no_cut_by() -> None:
    batch = render_batch(_batch_label(every=2), _records(5), _ALLEGRO_WITH_CUTTER)
    assert b"\r:0" not in batch.data


def test_a_batch_that_switches_the_cutter_has_no_unverified_finding() -> None:
    batch = render_batch(_batch_label(every=2), _records(5), _ALLEGRO_WITH_CUTTER)
    assert FindingCode.BATCH_CUT_UNVERIFIED not in {finding.code for finding in batch.batch_findings}


def test_checking_a_batch_gives_the_bytes_rendering_it_gives() -> None:
    label = _batch_label(every=2)
    whole = render_batch(label, _records(5), _ALLEGRO_WITH_CUTTER)
    checked = check_batch(label, _records(5), _ALLEGRO_WITH_CUTTER)
    assert b"".join(checked.record_jobs()) == whole.data
    assert checked.job_bytes == len(whole.data)


def test_rendering_a_checked_record_again_keeps_its_cutter_setting() -> None:
    # A send renders a label again for what the printer holds: the cutter must not change there.
    checked = check_batch(_batch_label(every=2), _records(5), _ALLEGRO_WITH_CUTTER)
    assert _switches(b"".join(render.first().data for render in checked.renders())) == [
        b"V0",
        b"V1",
        b"V0",
        b"V1",
        b"V1",
    ]


def test_the_renders_of_a_batch_held_whole_keep_each_cutter_setting() -> None:
    renders = record_renders(_batch_label(every=2), _records(5), _ALLEGRO_WITH_CUTTER)
    assert _switches(b"".join(render.first().data for render in renders)) == [b"V0", b"V1", b"V0", b"V1", b"V1"]


def test_a_batch_whose_job_sends_no_cutter_bit_still_cuts_with_the_cut_command() -> None:
    # With the cutter unknown no <STX>V can be built, so the printer's own setting stands.
    options = RenderOptions(model="allegro-2")
    batch = render_batch(_batch_label(every=2), _records(3), options)
    assert [job.count(CUT_NOW) for job in batch.record_jobs()] == [0, 1, 1]
    assert FindingCode.BATCH_CUT_UNVERIFIED in {finding.code for finding in batch.batch_findings}


def test_a_batch_left_without_software_switches_still_cuts_with_the_cut_command() -> None:
    options = RenderOptions(model="allegro-2", cutter_installed=True, send_settings=frozenset())
    batch = render_batch(_batch_label(every=2), _records(3), options)
    assert [job.count(CUT_NOW) for job in batch.record_jobs()] == [0, 1, 1]


def test_copies_of_one_label_in_a_batch_keep_their_cut_by() -> None:
    # quantity 5 with cut-by and the rest cut was measured right: one format counts its own copies.
    batch = render_batch(_batch_label(every=3).model_copy(update={"quantity": 5}), _records(1), _ALLEGRO_WITH_CUTTER)
    assert b":0003" in batch.data
    assert _switches(batch.data) == [b"V1"]
    assert batch.data.count(CUT_NOW) == 1


@pytest.mark.parametrize("value", [0, 10000])
def test_cut_every_takes_what_a_label_s_cut_takes(value: int) -> None:
    with pytest.raises(ValueError, match="cut_every"):
        RenderOptions(cut_every=value)


# ======================== The end of every print is cut ========================
#
# With a cutter installed the stock stops at the cutter and cannot be torn off, so the last label
# of a print (one label, or a whole --data series) is cut (user, 2026-11-09). The labels before
# it stay joined unless a cut says otherwise.


def _uncut_label(**fields: object) -> Label:
    return Label.model_validate({"elements": [{"type": "text", "x": 5, "y": 5, "text": "{n}"}], **fields})


def test_a_plain_label_is_cut_at_the_cutter_stop() -> None:
    result = _job(_label(), model="allegro-2", cutter_installed=True)
    assert STX + b"V1" in result.data
    assert STX + b"f302" in result.data
    assert not any(line.startswith(b":") for line in _lines(result))
    assert CUT_NOW not in result.data
    assert result.ok


def test_copies_of_a_plain_label_are_cut_once_after_the_last() -> None:
    # Measured 2026-11-06: ":0003" with Q0003 cut once, after the third copy.
    result = _job(_label(quantity=3), model="allegro-2", cutter_installed=True)
    assert STX + b"V1" in result.data
    assert b":0003" in _lines(result)
    assert CUT_NOW not in result.data


@pytest.mark.parametrize("model", ["allegro", "allegro-2", "dmx-400", "dmx-600", "dmx-800", "ovation", "titan-6200"])
def test_the_cut_after_the_print_needs_no_fallback_on_a_model_that_sets_its_cutter(model: str) -> None:
    result = _job(_label(quantity=3), model=model, cutter_installed=True, label_present=False)
    assert STX + b"V" in result.data
    assert b":0003" in _lines(result)
    assert all(finding.severity is Severity.INFO for finding in result.findings)


def test_a_series_stays_joined_and_is_cut_after_its_last_record() -> None:
    batch = render_batch(_uncut_label(), _records(3), _ALLEGRO_WITH_CUTTER)
    assert _switches(batch.data) == [b"V0", b"V0", b"V1"]
    assert _stops(batch.data) == [b"f100", b"f100", b"f302"]
    assert b"\r:0" not in batch.data
    assert CUT_NOW not in batch.data


def test_a_series_of_copies_is_cut_once_after_the_last_copy_of_its_last_record() -> None:
    batch = render_batch(_uncut_label(quantity=2), _records(3), _ALLEGRO_WITH_CUTTER)
    records = batch.data.split(STX + b"n")[1:]
    assert _switches(batch.data) == [b"V0", b"V0", b"V1"]
    assert [b"\r:0002\r" in record for record in records] == [False, False, True]


def test_checking_and_rendering_a_series_agree_on_its_end_cut() -> None:
    label = _uncut_label()
    checked = check_batch(label, _records(3), _ALLEGRO_WITH_CUTTER)
    assert b"".join(checked.record_jobs()) == render_batch(label, _records(3), _ALLEGRO_WITH_CUTTER).data
    renders = record_renders(label, _records(3), _ALLEGRO_WITH_CUTTER)
    assert _switches(b"".join(render.first().data for render in renders)) == [b"V0", b"V0", b"V1"]


def test_a_series_of_one_record_is_cut() -> None:
    batch = render_batch(_uncut_label(), _records(1), _ALLEGRO_WITH_CUTTER)
    assert _switches(batch.data) == [b"V1"]


def test_a_batch_cut_without_at_end_still_cuts_the_end_and_says_so() -> None:
    batch = render_batch(_batch_label(every=2, at_end=False), _records(5), _ALLEGRO_WITH_CUTTER)
    assert _switches(batch.data) == [b"V0", b"V1", b"V0", b"V1", b"V1"]
    [message] = [f.message for f in batch.batch_findings if f.code is FindingCode.CUT_AT_END_KEPT]
    assert "at_end" in message
    assert "cut_after_print" in message


def test_copies_cut_without_at_end_still_cut_the_rest_and_say_so() -> None:
    result = _job(_label(quantity=5, cut=Cut(every=2, at_end=False)), model="allegro-2", cutter_installed=True)
    assert b":0002" in _lines(result)
    assert result.data.count(CUT_NOW) == 1
    assert [f.severity for f in result.findings if f.code is FindingCode.CUT_AT_END_KEPT] == [Severity.INFO]


def test_cut_every_still_decides_the_cuts_between_labels() -> None:
    batch = render_batch(_uncut_label(), _records(5), _ALLEGRO_WITH_CUTTER.model_copy(update={"cut_every": 2}))
    assert _switches(batch.data) == [b"V0", b"V1", b"V0", b"V1", b"V1"]


@pytest.mark.parametrize(
    "options",
    [
        {"model": "allegro-2", "cutter_installed": True, "cut_after_print": False},
        {"model": "allegro-2", "cutter_installed": False},
        {"model": "allegro-2"},
        {"model": "generic", "cutter_installed": True},
        {"model": "allegro-2", "cutter_installed": True, "send_settings": frozenset[PrinterSetting]()},
    ],
    ids=["turned-off", "no-cutter", "cutter-unknown", "generic", "no-software-switches"],
)
def test_a_print_is_not_cut_where_its_job_does_not_set_an_installed_cutter(options: dict[str, object]) -> None:
    result = _job(_label(quantity=3), **options)
    assert STX + b"V1" not in result.data
    assert not any(line.startswith(b":") for line in _lines(result))
    assert CUT_NOW not in result.data
    assert FindingCode.NO_CUTTER not in {finding.code for finding in result.findings}
    batch = render_batch(_uncut_label(), _records(3), RenderOptions.model_validate(options))
    assert STX + b"V1" not in batch.data
    assert CUT_NOW not in batch.data


def test_turned_off_a_batch_cut_without_at_end_leaves_the_end_uncut_as_before() -> None:
    options = _ALLEGRO_WITH_CUTTER.model_copy(update={"cut_after_print": False})
    batch = render_batch(_batch_label(every=2, at_end=False), _records(5), options)
    assert _switches(batch.data) == [b"V0", b"V1", b"V0", b"V1", b"V0"]
    assert FindingCode.CUT_AT_END_KEPT not in {finding.code for finding in batch.batch_findings}


# ======================== A label the next one follows uncut ========================
#
# A stop past the start of print makes the printer pull the stock back before the next format
# (manual p.22). Measured 2026-11-09 on one Allegro 2 with its cutter: a 3-record series with the
# cutter stop f302 on every record pulled back between its labels; with f100 on the first two and
# f302 on the last it ran on, was cut once after the last, and a frame 2 mm in from every edge
# printed the same in both arms.


def _cutter_stop_options(**options: object) -> RenderOptions:
    return RenderOptions.model_validate(
        {"model": "dmx-600", "cutter_installed": True, "label_present": False, **options}
    )


def test_a_configured_joined_stop_wins_over_the_model_default() -> None:
    options = _ALLEGRO_WITH_CUTTER.model_copy(update={"joined_stop_position_mm": 20.0})
    batch = render_batch(_uncut_label(), _records(3), options)
    assert _stops(batch.data) == [b"f079", b"f079", b"f302"]


def test_without_a_joined_stop_every_record_keeps_the_cutter_stop() -> None:
    batch = render_batch(_uncut_label(), _records(3), _cutter_stop_options(cutter_stop_position_mm=80.0))
    assert _stops(batch.data) == [b"f315", b"f315", b"f315"]


def test_a_last_record_left_uncut_keeps_the_cutter_stop() -> None:
    # Nothing follows it in the print, so it runs on into nothing: it stops at the cutter as before.
    options = _ALLEGRO_WITH_CUTTER.model_copy(update={"cut_after_print": False})
    batch = render_batch(_batch_label(every=2, at_end=False), _records(5), options)
    assert _switches(batch.data) == [b"V0", b"V1", b"V0", b"V1", b"V0"]
    assert _stops(batch.data) == [b"f100", b"f302", b"f100", b"f302", b"f302"]


def test_a_joined_label_names_the_joined_stop_default_and_not_the_cutter_stop() -> None:
    result = _job(_label().model_copy(update={"cut": JoinedLabel()}), model="allegro-2", cutter_installed=True)
    assert STX + b"f100" in result.data
    [message] = _messages(result, FindingCode.MODEL_DEFAULTS_APPLIED)
    assert "joined_stop_position 25.4 mm (measured" in message
    assert "cutter_stop_position" not in message


def test_a_configured_joined_stop_is_not_named_as_a_model_default() -> None:
    label = _label().model_copy(update={"cut": JoinedLabel()})
    result = _job(label, model="allegro-2", cutter_installed=True, joined_stop_position_mm=20.0)
    assert all("joined_stop_position" not in m for m in _messages(result, FindingCode.MODEL_DEFAULTS_APPLIED))


def test_a_label_that_cuts_names_no_joined_stop() -> None:
    result = _job(_label(), model="allegro-2", cutter_installed=True)
    assert all("joined_stop_position" not in m for m in _messages(result, FindingCode.MODEL_DEFAULTS_APPLIED))


def test_the_label_s_own_stop_wins_over_the_joined_stop() -> None:
    label = _label(stop_position=50).model_copy(update={"cut": JoinedLabel()})
    result = _job(label, model="allegro-2", cutter_installed=True)
    assert STX + b"f100" not in result.data


def test_cut_after_print_is_a_bool() -> None:
    with pytest.raises(ValueError, match="cut_after_print"):
        RenderOptions.model_validate({"cut_after_print": "yes"})
