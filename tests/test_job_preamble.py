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
from datamax_dpl.domain.label import Cut, Image, Label, Qr, ScalableText, Text
from datamax_dpl.domain.profiles import PROFILES, get_profile
from datamax_dpl.domain.raster import Bitmap
from datamax_dpl.domain.render import RenderResult, build_job
from datamax_dpl.domain.render_context import RenderOptions, drawn_image_name

STX = b"\x02"
_BITMAP = ["#.#.#.#.", ".#.#.#.#"]
_ICON_BITMAP = ["##..##..", "..##..##"]
#: The printer names the two bitmaps download under: their content names.
_LOGO = drawn_image_name(Bitmap.from_rows(_BITMAP)).encode("ascii")
_ICON = drawn_image_name(Bitmap.from_rows(_ICON_BITMAP)).encode("ascii")


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


def test_a_configured_sensor_the_model_cannot_choose_is_info_and_strict_still_prints() -> None:
    result = _job(_text_label(), model="generic", media=MediaSensor.GAP, policy=FallbackPolicy.STRICT)
    assert STX + b"c0000" in result.data
    assert STX + b"e" not in result.data
    assert _finding(result, FindingCode.CONFIGURED_SENSOR_SELECT_UNSUPPORTED) == [Severity.INFO]
    assert FindingCode.SENSOR_SELECT_UNSUPPORTED not in _codes(result)
    assert result.ok


def test_the_label_s_own_sensor_the_model_cannot_choose_still_deviates() -> None:
    result = _job(_text_label(media=MediaSensor.GAP), model="generic", media=MediaSensor.GAP)
    assert _finding(result, FindingCode.SENSOR_SELECT_UNSUPPORTED) == [Severity.WARNING]
    assert FindingCode.CONFIGURED_SENSOR_SELECT_UNSUPPORTED not in _codes(result)


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
    assert _finding(result, FindingCode.FEED_RATE_OUT_OF_RANGE) == [Severity.INFO]


def test_strict_prints_with_a_configured_feed_rate_outside_the_slew_range() -> None:
    """The feed rate is a printer setting, not what the label asks for (D51a)."""
    result = _job(_text_label(), model="allegro-2", feed_rate=SpeedCode("S"), policy=FallbackPolicy.STRICT)
    assert _finding(result, FindingCode.FEED_RATE_OUT_OF_RANGE) == [Severity.INFO]
    assert result.ok


def test_a_feed_rate_out_of_range_on_a_model_without_a_default_sends_none() -> None:
    result = _job(_text_label(), model="generic", feed_rate=SpeedCode("S"))
    assert STX + b"S" not in result.data
    assert _finding(result, FindingCode.FEED_RATE_OUT_OF_RANGE) == [Severity.INFO]


@pytest.mark.parametrize("model", sorted(PROFILES))
def test_every_model_s_default_feed_rate_lies_in_its_slew_range(model: str) -> None:
    """So a feed rate out of range can only come from the options, never from the model."""
    profile = get_profile(model)
    assert profile.feed_rate is None or profile.slew_speed.contains(profile.feed_rate)


def test_a_dmx_400_job_under_strict_is_not_refused_for_its_feed_rate() -> None:
    """Appendix M p.194 gives the DMX 400 slew range C-I with default K: no S, the key is named."""
    result = _job(_text_label(), model="dmx-400", policy=FallbackPolicy.STRICT)
    assert result.ok
    assert STX + b"S" not in result.data
    assert FindingCode.FEED_RATE_OUT_OF_RANGE not in _codes(result)
    unknown = [f.message for f in result.findings if f.code is FindingCode.PRINTER_SETTINGS_UNKNOWN]
    assert len(unknown) == 1
    assert "feed_rate" in unknown[0]


def test_a_model_without_a_known_value_names_the_settings_it_does_not_send() -> None:
    result = _job(_text_label(), model="generic")
    unknown = [f for f in result.findings if f.code is FindingCode.PRINTER_SETTINGS_UNKNOWN]
    assert [f.severity for f in unknown] == [Severity.INFO]
    for key in ("start_of_print_mm", "max_label_length_mm", "feed_rate"):
        assert key in unknown[0].message


def test_the_prodigy_max_names_only_its_start_of_print() -> None:
    result = _job(_text_label(), model="prodigy-max-203")
    unknown = [f.message for f in result.findings if f.code is FindingCode.PRINTER_SETTINGS_UNKNOWN]
    assert len(unknown) == 1
    assert "start_of_print_mm" in unknown[0]
    assert "max_label_length_mm" not in unknown[0]


def test_settings_left_out_of_send_settings_are_not_named_as_unknown() -> None:
    settings = frozenset(PrinterSetting) - {PrinterSetting.START_OF_PRINT}
    result = _job(_text_label(), model="prodigy-max-203", send_settings=settings)
    assert FindingCode.PRINTER_SETTINGS_UNKNOWN not in _codes(result)


def test_a_model_with_every_value_known_names_nothing() -> None:
    assert FindingCode.PRINTER_SETTINGS_UNKNOWN not in _codes(_job(_text_label(), model="allegro-2"))


# ======================== Maximum label length against the label ========================


def test_a_label_as_long_as_the_maximum_label_length_sends_no_m() -> None:
    result = _job(_text_label(media=MediaSensor.GAP, height=420), model="dmx-600")
    assert STX + b"M" not in result.data
    found = [f for f in result.findings if f.code is FindingCode.MAX_LABEL_LENGTH_TOO_SHORT]
    assert [f.severity for f in found] == [Severity.WARNING]
    assert "420" in found[0].message
    assert "406.4" in found[0].message
    assert "model default" in found[0].message


def test_strict_refuses_a_label_too_long_for_the_maximum_label_length() -> None:
    label = _text_label(media=MediaSensor.GAP, height=420)
    result = _job(label, model="dmx-600", policy=FallbackPolicy.STRICT)
    assert _finding(result, FindingCode.MAX_LABEL_LENGTH_TOO_SHORT) == [Severity.ERROR]


def test_the_message_says_when_the_maximum_label_length_was_configured() -> None:
    result = _job(_text_label(height=200), model="dmx-600", max_label_length_mm=201)
    found = [f.message for f in result.findings if f.code is FindingCode.MAX_LABEL_LENGTH_TOO_SHORT]
    assert len(found) == 1
    assert "configured" in found[0]


@pytest.mark.parametrize(("height", "sent"), [(403.8, True), (403.9, False)])
def test_the_label_needs_a_tenth_of_an_inch_below_the_maximum_label_length(height: float, sent: bool) -> None:
    result = _job(_text_label(height=height), model="dmx-600")
    assert (STX + b"M1600" in result.data) is sent


def test_the_height_is_checked_against_the_value_the_four_digits_send() -> None:
    """200.004 mm goes out as M0787, 199.898 mm: within 2.54 mm of a 197.46 mm label."""
    label = _text_label(media=MediaSensor.GAP, height=197.46)
    result = _job(label, model="dmx-600", max_label_length_mm=200.004)
    assert STX + b"M" not in result.data
    found = [f.message for f in result.findings if f.code is FindingCode.MAX_LABEL_LENGTH_TOO_SHORT]
    assert len(found) == 1
    assert "199.898" in found[0]


def test_a_label_exactly_the_margin_below_the_maximum_label_length_faults() -> None:
    """Within 0.1 in of M includes 0.1 in itself: 406.4 - 2.54 = 403.86 mm on the DMX 600."""
    result = _job(_text_label(media=MediaSensor.GAP, height=403.86), model="dmx-600")
    assert STX + b"M" not in result.data
    assert _finding(result, FindingCode.MAX_LABEL_LENGTH_TOO_SHORT) == [Severity.WARNING]


def test_continuous_media_is_not_checked_against_the_maximum_label_length() -> None:
    result = _job(_text_label(media=MediaSensor.CONTINUOUS, height=420), model="dmx-600")
    assert FindingCode.MAX_LABEL_LENGTH_TOO_SHORT not in _codes(result)


# ======================== Label settings send_settings leaves out ========================


def test_a_label_s_own_media_left_out_of_send_settings_is_named() -> None:
    settings = frozenset(PrinterSetting) - {PrinterSetting.MEDIA}
    result = _job(_text_label(media=MediaSensor.CONTINUOUS, height=40), model="allegro-2", send_settings=settings)
    found = [f for f in result.findings if f.code is FindingCode.LABEL_SETTING_NOT_SENT]
    assert [f.severity for f in found] == [Severity.INFO]
    assert "media" in found[0].message
    assert "send_settings" in found[0].message


def test_a_label_s_own_stop_and_present_distance_left_out_of_send_settings_are_named() -> None:
    settings = frozenset(PrinterSetting) - {PrinterSetting.STOP}
    label = _text_label(stop_position=10, present_distance=5)
    result = _job(label, model="dmx-600", send_settings=settings)
    found = [f.message for f in result.findings if f.code is FindingCode.LABEL_SETTING_NOT_SENT]
    assert len(found) == 2
    assert "stop_position" in found[0]
    assert "present_distance" in found[1]


def test_a_label_that_sets_nothing_left_out_gets_no_such_finding() -> None:
    settings = frozenset(PrinterSetting) - {PrinterSetting.STOP, PrinterSetting.MEDIA}
    result = _job(_text_label(), model="dmx-600", send_settings=settings)
    assert FindingCode.LABEL_SETTING_NOT_SENT not in _codes(result)


# ======================== Software switches ========================


@pytest.mark.parametrize(
    ("cutter", "cut", "command"), [(True, Cut(), b"V1"), (True, None, b"V0"), (False, None, b"V0")]
)
def test_the_allegro_2_turns_its_cutter_on_only_for_a_label_that_cuts(
    cutter: bool, cut: Cut | None, command: bytes
) -> None:
    """Measured 2026-11-06 on one Allegro 2: with V1 every label is cut, with V0 none is."""
    result = _job(_text_label(cut=cut), model="allegro-2", cutter_installed=cutter)
    assert STX + command in result.data
    assert FindingCode.SOFTWARE_SWITCHES_NOT_SENT not in _codes(result)


def test_an_unknown_cutter_sends_no_software_switches() -> None:
    result = _job(_text_label(), model="allegro-2")
    assert STX + b"V" not in result.data
    assert _finding(result, FindingCode.SOFTWARE_SWITCHES_NOT_SENT) == [Severity.INFO]


@pytest.mark.parametrize(("present", "command"), [(True, b"V5"), (False, b"V1")])
def test_a_model_with_a_label_present_bit_needs_that_setting_too(present: bool, command: bytes) -> None:
    data = _job(_text_label(cut=Cut()), model="dmx-600", cutter_installed=True, label_present=present).data
    assert STX + command in data


def test_an_unknown_label_present_sends_no_software_switches_where_the_model_has_the_bit() -> None:
    result = _job(_text_label(), model="dmx-600", cutter_installed=True)
    assert STX + b"V" not in result.data
    assert _finding(result, FindingCode.SOFTWARE_SWITCHES_NOT_SENT) == [Severity.INFO]


@pytest.mark.parametrize(
    ("cutter", "cut", "present", "command"),
    [(True, Cut(), True, b"V5"), (True, Cut(), False, b"V1"), (True, None, True, b"V4"), (False, None, True, b"V4")],
)
def test_the_max_pe_ovation_column_sets_cutter_and_label_present(
    cutter: bool, cut: Cut | None, present: bool, command: bytes
) -> None:
    """Manual p.42: Prodigy Max, Ovation, Ovation 2, PE42 and PE43 have bit 0 cutter, bit 2 label present."""
    data = _job(_text_label(cut=cut), model="pe42", cutter_installed=cutter, label_present=present).data
    assert STX + command in data


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


def test_a_configured_plug_in_module_is_never_cleared_and_says_so_once() -> None:
    """Module B is a plug-in on every model (Appendix L) and may be flash, which each clear wears."""
    result = _job(_image_label(), model="allegro-2", image_module=ImageModule.B)
    assert STX + b"q" not in result.data
    assert _finding(result, FindingCode.GRAPHICS_CLEAR_SKIPPED) == [Severity.INFO]


def test_a_prodigy_removes_each_name_it_downloads_before_the_download_and_after_the_format() -> None:
    data = _job(_image_label(), model="prodigy", image_module=ImageModule.B).data
    remove = STX + b"R" + _LOGO + b"\r"
    assert data.count(remove) == 2
    assert data.index(remove) < data.index(STX + b"IB")
    assert data.endswith(b"E\r" + remove)
    assert STX + b"q" not in data


def test_a_prodigy_removes_each_of_two_names_before_its_download_and_both_after_the_format() -> None:
    label = Label(
        elements=[Image(x=1, y=1, name="LOGO", bitmap=_BITMAP), Image(x=1, y=9, name="ICON", bitmap=_ICON_BITMAP)]
    )
    data = _job(label, model="prodigy", image_module=ImageModule.B).data
    head = data[: data.index(STX + b"L\r")]
    order = re.findall(rb"\x02R[0-9A-Za-z]+\r|\x02IBAF[0-9A-Za-z]+\r", head)
    assert order == [
        STX + b"R" + _LOGO + b"\r",
        STX + b"IBAF" + _LOGO + b"\r",
        STX + b"R" + _ICON + b"\r",
        STX + b"IBAF" + _ICON + b"\r",
    ]
    assert data.endswith(b"E\r" + STX + b"R" + _LOGO + b"\r" + STX + b"R" + _ICON + b"\r")


@pytest.mark.parametrize("name", ["Y", "N"])
@pytest.mark.parametrize("policy", list(FallbackPolicy))
def test_a_prodigy_image_named_like_the_ribbon_saver_is_removed_by_its_content_name(
    name: str, policy: FallbackPolicy
) -> None:
    """``<STX>RY``/``<STX>RN`` switch the ribbon saver (manual p.36); the remove names the content name instead."""
    label = Label(elements=[Image(x=1, y=1, name=name, bitmap=_BITMAP)])
    result = _job(label, model="prodigy", image_module=ImageModule.B, policy=policy)
    assert result.ok, result.findings
    assert STX + b"R" + name.encode() + b"\r" not in result.data
    assert result.data.count(STX + b"R" + _LOGO + b"\r") == 2


def test_a_prodigy_plus_has_no_ram_module_so_it_never_clears_and_never_removes_by_name() -> None:
    data = _job(_image_label(), model="prodigy-plus", image_module=ImageModule.B).data
    assert STX + b"q" not in data
    assert STX + b"R" not in data
    assert STX + b"IBAF" + _LOGO + b"\r" in data


def test_a_prodigy_label_without_images_removes_nothing() -> None:
    result = _job(_text_label(), model="prodigy")
    assert STX + b"R" not in result.data
    assert FindingCode.GRAPHICS_CLEAR_UNSUPPORTED not in _codes(result)


def test_permissive_clears_the_module_and_never_removes_by_name() -> None:
    data = _job(_image_label(), model="permissive").data
    assert data.count(STX + b"qA") == 2
    assert STX + b"R" not in data


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
    assert [command.raw for command in commands].count(f"<STX>R{_LOGO.decode()}<CR>") == 2


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
    # The cutter left unknown: no <STX>V can be built, so this batch is the one that sends <STX>o.
    batch = render_batch(label, records, RenderOptions(model="allegro-2"))
    jobs = batch.data.split(STX + b"o")
    assert len(jobs) == 3
    assert jobs[1].startswith(STX + b"qA")
    assert jobs[2] == STX + b"qA"
    assert batch.data.endswith(STX + b"o" + STX + b"qA")


# ======================== Symbol set ========================


def test_scalable_text_without_a_symbol_set_states_the_assumed_one() -> None:
    """A symbol set persists (manual p.48), so the format names the PC set the text was encoded for."""
    label = Label(elements=[ScalableText(x=5, y=5, text=chr(0xE4) + "x", height_points=12)])
    # The DMX 600 has scalable fonts from firmware 2.01 on.
    data = _job(label, model="dmx-600", firmware="2.01").data
    assert b"\rySPC\r" in data
    assert b"\x84x" in data


def test_the_label_s_own_symbol_set_is_sent_as_before() -> None:
    label = Label(symbol_set="W1", elements=[ScalableText(x=5, y=5, text="Ax", height_points=12)])
    data = _job(label, model="dmx-600", firmware="2.01").data
    assert b"\rySW1\r" in data
    assert b"ySPC" not in data


def test_a_label_without_scalable_text_names_no_symbol_set() -> None:
    assert b"yS" not in _job(_text_label(), model="dmx-600", firmware="2.01").data


@pytest.mark.parametrize(("model", "firmware"), [("allegro-2", ""), ("dmx-600", "")])
def test_a_model_without_scalable_fonts_names_no_symbol_set(model: str, firmware: str) -> None:
    """The text falls back to font 9 there, as before, with its scalable_font_unsupported finding."""
    label = Label(elements=[ScalableText(x=5, y=5, text="Ax", height_points=12)])
    result = _job(label, model=model, firmware=firmware)
    assert b"yS" not in result.data
    assert FindingCode.SCALABLE_FONT_UNSUPPORTED in _codes(result)
