"""Persistent printer settings a job sets: model defaults from the manual, render options over them.

Every job sets the printer state it relies on, since another program or the front panel may have
changed the printer. These tests pin the per-model defaults the manual's tables give (start of
print p.31, maximum label length p.30, slew speed as feed rate Appendix M, software switch bits
p.41), which features each model has, how the render options validate the new settings, and that
an option wins over the model's default in the context a job encodes with.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from datamax_dpl.domain.enums import MediaSensor, PrinterSetting, SpeedCode
from datamax_dpl.domain.label import Label, Text
from datamax_dpl.domain.profiles import MODEL_PROFILES, Feature, PrinterProfile, SoftwareSwitchBits, get_profile
from datamax_dpl.domain.render import job_context
from datamax_dpl.domain.render_context import PrinterState, RenderOptions

# ======================== Model defaults ========================

#: (start of print mm, maximum label length mm, feed rate) per model, from the manual's tables.
_DEFAULTS: dict[str, tuple[float | None, float | None, SpeedCode | None]] = {
    "allegro-2": (55.88, 304.8, SpeedCode.IPS_2_0),
    "prodigy": (63.5, 304.8, SpeedCode.IPS_4_0),
    "prodigy-plus": (27.94, 304.8, SpeedCode.IPS_6_0),
    "dmx-600": (55.88, 406.4, SpeedCode.IPS_8_0),
    # The Prodigy Max's start of print is set by DIP switch (110 or 250): which one is unknown.
    "prodigy-max-203": (None, 406.4, SpeedCode.IPS_8_0),
    "titan-6200": (27.94, 406.4, SpeedCode.IPS_6_0),
    # No common value exists across the models, so the synthetic profiles send none.
    "generic": (None, None, None),
    "permissive": (None, None, None),
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("model", "expected"), sorted(_DEFAULTS.items()))
def test_each_model_ships_the_printer_setting_defaults_of_the_manual(
    model: str, expected: tuple[float | None, float | None, SpeedCode | None]
) -> None:
    profile = get_profile(model)

    assert (profile.start_of_print_mm, profile.max_label_length_mm, profile.feed_rate) == expected


@pytest.mark.os_agnostic
@pytest.mark.parametrize("profile", list(MODEL_PROFILES.values()), ids=list(MODEL_PROFILES))
def test_every_model_feeds_at_its_slew_default(profile: PrinterProfile) -> None:
    assert profile.feed_rate is profile.slew_speed.default


@pytest.mark.os_agnostic
def test_every_model_has_a_maximum_label_length_from_the_manual_table() -> None:
    lengths = {profile.max_label_length_mm for profile in MODEL_PROFILES.values()}

    assert lengths == {304.8, 406.4}


#: The bit layout of <STX>V per model, from the manual's p.41 table.
_SWITCHES: dict[str, SoftwareSwitchBits | None] = {
    "allegro-2": SoftwareSwitchBits(cutter=0, internal_batch=1),
    "dmx-600": SoftwareSwitchBits(cutter=0, internal_batch=1, label_present=2),
    "prodigy-max-203": SoftwareSwitchBits(cutter=0, label_present=2),
    "prodigy": SoftwareSwitchBits(
        cutter=0, internal_batch=3, label_present=2, undescribed=((1, "invert ribbon saver"),)
    ),
    "prodigy-plus": SoftwareSwitchBits(cutter=0, label_present=2, undescribed=((1, "insert ribbon sense"),)),
    # The p.41 table has no DMX 430 column: no layout is guessed for it.
    "dmx-430": None,
    "generic": None,
    "permissive": None,
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("model", "expected"), sorted(_SWITCHES.items()))
def test_each_model_carries_the_software_switch_bits_of_the_manual(
    model: str, expected: SoftwareSwitchBits | None
) -> None:
    assert get_profile(model).software_switches == expected


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("model", "buildable"), [("allegro-2", True), ("dmx-600", True), ("prodigy", False), ("prodigy-plus", False)]
)
def test_a_switch_layout_with_a_bit_no_setting_describes_is_not_buildable(model: str, buildable: bool) -> None:
    switches = get_profile(model).software_switches

    assert switches is not None
    assert switches.buildable is buildable


@pytest.mark.os_agnostic
def test_a_switch_layout_describes_its_bits_for_the_tables() -> None:
    allegro = get_profile("allegro-2").software_switches
    prodigy = get_profile("prodigy").software_switches

    assert allegro is not None
    assert prodigy is not None
    assert allegro.describe() == "0 cutter, 1 internal batch"
    assert prodigy.describe() == "never sent: bit 1 invert ribbon saver"


# ======================== Features ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", ["prodigy", "prodigy-152"])
def test_the_prodigy_has_no_clear_module_but_removes_one_image(model: str) -> None:
    profile = get_profile(model)

    assert (profile.support(Feature.CLEAR_MODULE).available, profile.support(Feature.REMOVE_IMAGE).available) == (
        False,
        True,
    )


@pytest.mark.os_agnostic
def test_only_the_prodigy_family_removes_one_image_and_every_other_model_clears_a_module() -> None:
    removing = sorted(p.id for p in MODEL_PROFILES.values() if p.support(Feature.REMOVE_IMAGE).available)
    not_clearing = sorted(p.id for p in MODEL_PROFILES.values() if not p.support(Feature.CLEAR_MODULE).available)

    assert removing == ["prodigy", "prodigy-152"]
    assert not_clearing == ["prodigy", "prodigy-152"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "feature", [Feature.START_OF_PRINT, Feature.MAX_LABEL_LENGTH, Feature.FEED_RATE, Feature.SOFTWARE_SWITCHES]
)
def test_every_model_has_the_printer_setting_commands(feature: Feature) -> None:
    lacking = [p.id for p in MODEL_PROFILES.values() if not p.support(feature).available]

    assert lacking == []


@pytest.mark.os_agnostic
def test_generic_keeps_only_what_every_model_has_and_permissive_has_everything() -> None:
    generic = get_profile("generic")
    permissive = get_profile("permissive")
    new = (Feature.CLEAR_MODULE, Feature.REMOVE_IMAGE, Feature.START_OF_PRINT, Feature.FEED_RATE)

    assert [generic.support(f).available for f in new] == [False, False, True, True]
    assert all(permissive.support(f).available for f in new)


# ======================== Render options ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("start_of_print_mm", 12.6, "greater than or equal to 12.7"),
        ("start_of_print_mm", 2540.0, "less than or equal to 2539.746"),
        ("max_label_length_mm", -1.0, "greater than or equal to 0"),
        ("max_label_length_mm", 2540.0, "less than or equal to 2539.746"),
    ],
)
def test_a_setting_outside_the_manual_bounds_is_refused_naming_its_field(field: str, value: float, reason: str) -> None:
    with pytest.raises(ValidationError, match=rf"{field}\n\s+Input should be {reason}"):
        RenderOptions.model_validate({field: value})


@pytest.mark.os_agnostic
def test_a_feed_rate_past_the_letter_s_is_refused_naming_its_field() -> None:
    with pytest.raises(ValidationError, match=r"feed_rate\n\s+Input should be 'A', 'B'"):
        RenderOptions.model_validate({"feed_rate": "T"})


@pytest.mark.os_agnostic
def test_an_unknown_printer_setting_is_refused_naming_its_field() -> None:
    with pytest.raises(ValidationError, match=r"send_settings\.0\n\s+Input should be 'media', 'stop'"):
        RenderOptions.model_validate({"send_settings": ["ribbon"]})


@pytest.mark.os_agnostic
def test_the_manual_bounds_themselves_are_accepted() -> None:
    options = RenderOptions(start_of_print_mm=12.7, max_label_length_mm=2539.746)

    assert (options.start_of_print_mm, options.max_label_length_mm) == (12.7, 2539.746)


@pytest.mark.os_agnostic
def test_new_options_default_to_sending_everything_and_clearing_graphics() -> None:
    options = RenderOptions()

    assert options.send_settings is None
    assert (options.clear_graphics_before_label, options.clear_graphics_after_label) == (True, True)


# ======================== Resolution ========================

_LABEL = Label(elements=[Text(x=5, y=5, text="Hi")])


@pytest.mark.os_agnostic
def test_a_job_context_carries_the_model_defaults_when_no_option_is_set() -> None:
    state = job_context(_LABEL, RenderOptions(model="allegro-2")).printer_state

    assert state == PrinterState(
        send=frozenset(PrinterSetting),
        media=None,
        start_of_print_mm=55.88,
        max_label_length_mm=304.8,
        feed_rate=SpeedCode.IPS_2_0,
        label_present=None,
        clear_graphics_before_label=True,
        clear_graphics_after_label=True,
    )


@pytest.mark.os_agnostic
def test_each_option_wins_over_the_model_default() -> None:
    options = RenderOptions(
        model="allegro-2",
        send_settings=frozenset({PrinterSetting.MEDIA}),
        media=MediaSensor.REFLECTIVE,
        start_of_print_mm=40.0,
        max_label_length_mm=500.0,
        feed_rate=SpeedCode.IPS_3_0,
        label_present=True,
        clear_graphics_before_label=False,
        clear_graphics_after_label=False,
    )

    state = job_context(_LABEL, options).printer_state

    assert state == PrinterState(
        send=frozenset({PrinterSetting.MEDIA}),
        media=MediaSensor.REFLECTIVE,
        start_of_print_mm=40.0,
        max_label_length_mm=500.0,
        feed_rate=SpeedCode.IPS_3_0,
        label_present=True,
        clear_graphics_before_label=False,
        clear_graphics_after_label=False,
    )


@pytest.mark.os_agnostic
def test_the_label_s_own_media_wins_over_the_option() -> None:
    label = Label(media=MediaSensor.GAP, elements=[Text(x=5, y=5, text="Hi")])

    state = job_context(label, RenderOptions(model="allegro-2", media=MediaSensor.REFLECTIVE)).printer_state

    assert state.media is MediaSensor.GAP


@pytest.mark.os_agnostic
def test_a_model_whose_start_of_print_is_unknown_sends_none_unless_an_option_sets_it() -> None:
    unset = job_context(_LABEL, RenderOptions(model="prodigy-max-203")).printer_state
    set_ = job_context(_LABEL, RenderOptions(model="prodigy-max-203", start_of_print_mm=27.94)).printer_state

    assert (unset.start_of_print_mm, set_.start_of_print_mm) == (None, 27.94)
