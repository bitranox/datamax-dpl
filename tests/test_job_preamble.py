"""Every label's job sets the printer state it relies on and clears the image module around it.

Another program or the front panel may have changed the printer, so each label's bytes run: inch
mode, the persistent settings (media, start of print, maximum label length, feed rate, software
switches, stop position), the clear before its downloads, the downloads, the format, its cut, and
the clear after it. The command syntax is the manual's: ``<STX>c`` p.29, ``<STX>M`` p.30,
``<STX>O`` p.32, ``<STX>q`` p.35 (not on the Prodigy), ``<STX>R<name><CR>`` p.36 (Prodigy only),
``<STX>S`` p.38 and ``<STX>V`` p.41-42 (bit 0 cutter, bit 2 label present).
"""

from __future__ import annotations

import re

import pytest

from datamax_dpl.application.use_cases import render_batch
from datamax_dpl.domain.decoder import explain
from datamax_dpl.domain.enums import (
    CommandKind,
    FallbackPolicy,
    FindingCode,
    ImageModule,
    MediaSensor,
    PrinterSetting,
    Severity,
    SpeedCode,
)
from datamax_dpl.domain.label import Cut, Image, Label, Qr, Text
from datamax_dpl.domain.render import RenderResult, build_job
from datamax_dpl.domain.render_context import RenderOptions

STX = b"\x02"
_BITMAP = ["#.#.#.#.", ".#.#.#.#"]


def _text_label(**fields: object) -> Label:
    return Label.model_validate({"elements": [Text(x=5, y=5, text="Hi")], **fields})


def _image_label(**fields: object) -> Label:
    return Label.model_validate({"elements": [Image(x=1, y=1, name="LOGO", bitmap=_BITMAP)], **fields})


def _job(label: Label, **options: object) -> RenderResult:
    return build_job(label, RenderOptions.model_validate(options))


def _codes(result: RenderResult) -> list[FindingCode]:
    return [finding.code for finding in result.findings]


def _finding(result: RenderResult, code: FindingCode) -> list[Severity]:
    return [finding.severity for finding in result.findings if finding.code is code]


# ======================== Media ========================


def test_a_gap_label_on_the_allegro_2_ends_continuous_mode_and_selects_the_gap_sensor() -> None:
    data = _job(_text_label(media=MediaSensor.GAP), model="allegro-2").data
    assert STX + b"c0000" in data
    assert STX + b"e" in data


def test_a_gap_label_where_the_sensor_cannot_be_chosen_still_ends_continuous_mode() -> None:
    result = _job(_text_label(media=MediaSensor.GAP), model="generic")
    assert STX + b"c0000" in result.data
    assert STX + b"e" not in result.data
    assert FindingCode.SENSOR_SELECT_UNSUPPORTED in _codes(result)


def test_a_label_that_names_no_media_still_ends_continuous_mode() -> None:
    data = _job(_text_label(), model="allegro-2").data
    assert STX + b"c0000" in data
    assert STX + b"e" not in data
    assert STX + b"r" not in data


def test_the_media_option_selects_the_sensor_when_the_label_names_none() -> None:
    data = _job(_text_label(), model="allegro-2", media=MediaSensor.REFLECTIVE).data
    assert STX + b"c0000" + STX + b"r" in data


def test_the_label_s_own_media_wins_over_the_option() -> None:
    label = _text_label(media=MediaSensor.CONTINUOUS, continuous_length=50.8)
    # A nominal model: the continuous length is a label length, printed through the scale.
    data = _job(label, model="dmx-600", media=MediaSensor.GAP).data
    assert STX + b"c0200" in data
    assert STX + b"c0000" not in data


def test_send_settings_without_media_sends_no_continuous_length() -> None:
    settings = frozenset(PrinterSetting) - {PrinterSetting.MEDIA}
    data = _job(_text_label(media=MediaSensor.GAP), model="allegro-2", send_settings=settings).data
    assert STX + b"c" not in data
    assert STX + b"e" not in data


# ======================== Start of print, maximum label length, feed rate ========================


def test_the_allegro_2_job_sends_its_manual_defaults_in_hundredths_of_an_inch() -> None:
    data = _job(_text_label(), model="allegro-2").data
    assert STX + b"O0220" in data
    assert STX + b"M1200" in data
    assert STX + b"SC" in data


def test_configured_values_are_sent_as_configured() -> None:
    data = _job(
        _text_label(), model="allegro-2", start_of_print_mm=25.4, max_label_length_mm=254, feed_rate=SpeedCode("E")
    ).data
    assert STX + b"O0100" in data
    assert STX + b"M1000" in data
    assert STX + b"SE" in data


def test_a_model_without_a_default_sends_no_start_of_print() -> None:
    data = _job(_text_label(), model="prodigy-max-203").data
    assert STX + b"O" not in data
    assert STX + b"M1600" in data


def test_a_setting_left_out_of_send_settings_is_not_sent() -> None:
    settings = frozenset({PrinterSetting.MEDIA})
    data = _job(_text_label(), model="allegro-2", send_settings=settings, cutter_installed=True).data
    for command in (b"O", b"M", b"S", b"V"):
        assert STX + command not in data


def test_a_feed_rate_outside_the_model_s_slew_range_falls_back_to_the_model_default() -> None:
    result = _job(_text_label(), model="allegro-2", feed_rate=SpeedCode("S"))
    assert STX + b"SC" in result.data
    assert STX + b"SS" not in result.data
    assert _finding(result, FindingCode.SPEED_OUT_OF_RANGE) == [Severity.WARNING]


def test_strict_refuses_a_feed_rate_outside_the_model_s_slew_range() -> None:
    result = _job(_text_label(), model="allegro-2", feed_rate=SpeedCode("S"), policy=FallbackPolicy.STRICT)
    assert _finding(result, FindingCode.SPEED_OUT_OF_RANGE) == [Severity.ERROR]
    assert not result.ok


def test_a_feed_rate_out_of_range_on_a_model_without_a_default_sends_none() -> None:
    result = _job(_text_label(), model="generic", feed_rate=SpeedCode("S"))
    assert STX + b"S" not in result.data
    assert _finding(result, FindingCode.SPEED_OUT_OF_RANGE) == [Severity.WARNING]


# ======================== Software switches ========================


@pytest.mark.parametrize(("cutter", "command"), [(True, b"V1"), (False, b"V0")])
def test_the_allegro_2_sets_its_cutter_bit_and_internal_batch_off(cutter: bool, command: bytes) -> None:
    result = _job(_text_label(), model="allegro-2", cutter_installed=cutter)
    assert STX + command in result.data
    assert FindingCode.SOFTWARE_SWITCHES_NOT_SENT not in _codes(result)


def test_an_unknown_cutter_sends_no_software_switches() -> None:
    result = _job(_text_label(), model="allegro-2")
    assert STX + b"V" not in result.data
    assert _finding(result, FindingCode.SOFTWARE_SWITCHES_NOT_SENT) == [Severity.INFO]


@pytest.mark.parametrize(("present", "command"), [(True, b"V5"), (False, b"V1")])
def test_a_model_with_a_label_present_bit_needs_that_setting_too(present: bool, command: bytes) -> None:
    data = _job(_text_label(), model="dmx-600", cutter_installed=True, label_present=present).data
    assert STX + command in data


def test_an_unknown_label_present_sends_no_software_switches_where_the_model_has_the_bit() -> None:
    result = _job(_text_label(), model="dmx-600", cutter_installed=True)
    assert STX + b"V" not in result.data
    assert _finding(result, FindingCode.SOFTWARE_SWITCHES_NOT_SENT) == [Severity.INFO]


@pytest.mark.parametrize("model", ["prodigy-plus", "prodigy", "dmx-430"])
def test_a_model_whose_bits_are_not_all_known_never_sends_software_switches(model: str) -> None:
    result = _job(_text_label(), model=model, cutter_installed=True, label_present=True)
    assert STX + b"V" not in result.data
    assert _finding(result, FindingCode.SOFTWARE_SWITCHES_NOT_SENT) == [Severity.INFO]


# ======================== Clearing the image module ========================


def test_an_allegro_2_job_clears_module_a_before_its_downloads_and_ends_with_a_clear() -> None:
    data = _job(_image_label(), model="allegro-2").data
    assert data.endswith(STX + b"qA")
    assert data.index(STX + b"qA") < data.index(STX + b"IA")
    assert data.count(STX + b"qA") == 2


def test_a_label_without_images_clears_too() -> None:
    data = _job(_text_label(), model="allegro-2").data
    assert data.count(STX + b"qA") == 2
    assert data.endswith(STX + b"qA")


def test_the_clears_follow_their_keys() -> None:
    before_only = _job(_image_label(), model="allegro-2", clear_graphics_after_label=False).data
    after_only = _job(_image_label(), model="allegro-2", clear_graphics_before_label=False).data
    assert before_only.count(STX + b"qA") == 1
    assert before_only.index(STX + b"qA") < before_only.index(STX + b"IA")
    assert after_only.count(STX + b"qA") == 1
    assert after_only.endswith(STX + b"qA")


def test_the_clear_goes_to_the_configured_module() -> None:
    data = _job(_image_label(), model="allegro-2", image_module=ImageModule.B).data
    assert STX + b"qB" in data
    assert STX + b"qA" not in data


def test_a_prodigy_removes_each_name_it_downloads_before_the_download_and_after_the_format() -> None:
    data = _job(_image_label(), model="prodigy", image_module=ImageModule.B).data
    remove = STX + b"RLOGO\r"
    assert data.count(remove) == 2
    assert data.index(remove) < data.index(STX + b"IB")
    assert data.endswith(b"E\r" + remove)
    assert STX + b"q" not in data


def test_a_prodigy_label_without_images_removes_nothing() -> None:
    result = _job(_text_label(), model="prodigy")
    assert STX + b"R" not in result.data
    assert FindingCode.GRAPHICS_CLEAR_UNSUPPORTED not in _codes(result)


def test_permissive_clears_the_module_and_never_removes_by_name() -> None:
    data = _job(_image_label(), model="permissive").data
    assert data.count(STX + b"qA") == 2
    assert STX + b"RLOGO" not in data


def test_a_model_that_can_clear_nothing_reports_it_only_for_a_label_that_downloads() -> None:
    plain = _job(_text_label(), model="generic")
    image = _job(_image_label(), model="generic")
    assert FindingCode.GRAPHICS_CLEAR_UNSUPPORTED not in _codes(plain)
    assert _finding(image, FindingCode.GRAPHICS_CLEAR_UNSUPPORTED) == [Severity.INFO]
    assert STX + b"q" not in image.data


def test_a_model_that_can_clear_nothing_says_nothing_when_both_clears_are_off() -> None:
    result = _job(_image_label(), model="generic", clear_graphics_before_label=False, clear_graphics_after_label=False)
    assert FindingCode.GRAPHICS_CLEAR_UNSUPPORTED not in _codes(result)


# ======================== Order ========================


def _positions(data: bytes, *needles: bytes) -> list[int]:
    return [data.index(needle) for needle in needles]


def test_a_label_s_bytes_run_settings_clear_downloads_format_cut_clear() -> None:
    label = _image_label(media=MediaSensor.GAP, quantity=3, cut=Cut(every=2))
    data = _job(label, model="allegro-2", cutter_installed=True).data
    assert data.startswith(STX + b"n")
    settings = _positions(data, STX + b"c0000", STX + b"e", STX + b"O0220", STX + b"M1200", STX + b"SC", STX + b"V1")
    clear, download, start, end, cut = _positions(data, STX + b"qA", STX + b"IA", STX + b"L", b"E\r", STX + b"o")
    assert max(settings) < clear < download < start < end < cut
    assert data.endswith(STX + b"o" + STX + b"qA")


def test_the_decoder_reads_every_command_of_a_prodigy_job() -> None:
    data = _job(_image_label(), model="prodigy", image_module=ImageModule.B, cutter_installed=True).data
    commands = explain(data).commands
    assert all(command.kind is not CommandKind.UNKNOWN for command in commands)
    assert [command.raw for command in commands].count("<STX>RLOGO<CR>") == 2


# ======================== Batch ========================


def _qr_label(**fields: object) -> Label:
    return Label.model_validate({"elements": [Qr(x=5, y=5, data="{code}")], **fields})


def test_every_record_of_a_batch_clears_before_downloading_its_qr_again() -> None:
    batch = render_batch(_qr_label(), [{"code": "SAME"}, {"code": "SAME"}], RenderOptions(model="allegro-2"))
    data = batch.data
    downloads = [match.start() for match in re.finditer(re.escape(STX + b"IA"), data)]
    clears = [match.start() for match in re.finditer(re.escape(STX + b"qA"), data)]
    assert len(downloads) == 2
    assert len(clears) == 4
    assert clears[0] < downloads[0] < clears[1] < clears[2] < downloads[1] < clears[3]
    assert data.count(STX + b"c0000") == 2


def test_a_batch_cut_goes_before_the_record_s_closing_clear() -> None:
    label = _qr_label(cut=Cut(every=2))
    records = [{"code": "A1"}, {"code": "B2"}, {"code": "C3"}]
    batch = render_batch(label, records, RenderOptions(model="allegro-2", cutter_installed=True))
    jobs = batch.data.split(STX + b"o")
    assert len(jobs) == 3
    assert jobs[1].startswith(STX + b"qA")
    assert jobs[2] == STX + b"qA"
    assert batch.data.endswith(STX + b"o" + STX + b"qA")
