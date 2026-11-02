"""Printer profiles: the calibration defaults a model ships, measured on a real printer."""

from __future__ import annotations

import copy
import dataclasses
import pickle
from typing import cast

import pytest

from datamax_dpl.domain.detect import MEMORY_REPORT_MODELS, Evidence, suggest_profiles
from datamax_dpl.domain.enums import ImageModule, ModelFamily
from datamax_dpl.domain.profiles import (
    MODEL_PROFILES,
    NO,
    YES,
    CalibrationDefaults,
    Feature,
    PrinterProfile,
    Support,
    get_profile,
    list_profiles,
)


def _values(calibration: CalibrationDefaults) -> tuple[float, float, float, float, float | None]:
    return (
        calibration.scale_across,
        calibration.scale_along,
        calibration.offset_across,
        calibration.offset_along,
        calibration.stop_position_mm,
    )


@pytest.mark.os_agnostic
def test_allegro_2_ships_the_measured_calibration_defaults() -> None:
    calibration = get_profile("allegro-2").calibration
    assert _values(calibration) == (1.0, 0.976, 0.0, -0.25, 66.8)
    assert calibration.provenance


@pytest.mark.os_agnostic
def test_allegro_2_holds_the_format_length_measured_row_by_row() -> None:
    assert get_profile("allegro-2").max_format_rows == 1648


@pytest.mark.os_agnostic
def test_other_models_keep_neutral_calibration() -> None:
    calibration = get_profile("dmx-600").calibration
    assert _values(calibration) == (1.0, 1.0, 0.0, 0.0, None)
    assert calibration.provenance == ""


@pytest.mark.os_agnostic
def test_only_a_model_that_has_a_stop_position_ships_a_default_for_it() -> None:
    shipping = [p for p in list_profiles() if p.calibration.stop_position_mm is not None]
    assert shipping
    assert all(p.support(Feature.STOP_POSITION).available for p in shipping)


#: The module each model's built-in RAM is, from the manual's Appendix L: A on most models, E on
#: the DMX 600/800 (A and B there are plug-ins, D is non-volatile), none on the Prodigy and
#: Prodigy Plus (A and B are plug-ins). The two synthetic profiles keep A.
_IMAGE_MODULES: dict[str, ImageModule | None] = {
    "generic": ImageModule.A,
    "permissive": ImageModule.A,
    "allegro": ImageModule.A,
    "allegro-2": ImageModule.A,
    "dmx-400": ImageModule.A,
    "dmx-430": ImageModule.A,
    "prodigy-max-203": ImageModule.A,
    "prodigy-max-300": ImageModule.A,
    "pe42": ImageModule.A,
    "pe43": ImageModule.A,
    "ovation": ImageModule.A,
    "ovation-300": ImageModule.A,
    "ovation-2": ImageModule.A,
    "titan-6200": ImageModule.A,
    "dmx-600": ImageModule.E,
    "dmx-800": ImageModule.E,
    "prodigy": None,
    "prodigy-152": None,
    "prodigy-plus": None,
    "prodigy-plus-152": None,
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("model", "letter"), sorted(_IMAGE_MODULES.items()))
def test_each_model_downloads_images_to_its_built_in_module(model: str, letter: ImageModule | None) -> None:
    assert get_profile(model).image_module is letter


@pytest.mark.os_agnostic
def test_the_image_module_table_names_every_profile() -> None:
    assert {p.id for p in list_profiles()} == set(_IMAGE_MODULES)


@pytest.mark.os_agnostic
def test_every_ram_module_is_an_image_module_member() -> None:
    """A clear compares the job's module with these, so a letter held as text would never match."""
    for profile in list_profiles():
        assert all(type(module) is ImageModule for module in profile.ram_modules), profile.id


@pytest.mark.os_agnostic
def test_every_real_model_names_its_family_and_the_synthetic_profiles_none() -> None:
    for profile in list_profiles():
        assert (profile.family is None) is profile.synthetic, profile.id
    assert get_profile("prodigy-152").family is ModelFamily.PRODIGY


#: The models whose manual entry lists the <STX>KQ memory report, as the manual names them.
_MEMORY_REPORT_MODELS = {
    "dmx-600",
    "dmx-800",
    "prodigy-max-203",
    "prodigy-max-300",
    "pe42",
    "pe43",
    "ovation",
    "ovation-300",
    "ovation-2",
    "titan-6200",
}


@pytest.mark.os_agnostic
def test_the_memory_report_models_follow_from_their_families_as_the_manual_lists_them() -> None:
    assert set(MEMORY_REPORT_MODELS) == _MEMORY_REPORT_MODELS


@pytest.mark.os_agnostic
def test_a_serial_test_answer_rules_out_exactly_the_standard_prodigy_models() -> None:
    candidates = suggest_profiles(Evidence(serial_test=True)).candidates
    assert set(MODEL_PROFILES) - set(candidates) == {"prodigy", "prodigy-152"}


@pytest.mark.os_agnostic
def test_every_model_family_has_a_model() -> None:
    assert {profile.family for profile in MODEL_PROFILES.values()} == set(ModelFamily)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("profile", list_profiles(), ids=lambda profile: profile.id)
def test_every_profile_states_its_support_for_every_feature(profile: PrinterProfile) -> None:
    assert set(profile.features) == set(Feature)


@pytest.mark.os_agnostic
def test_a_profile_feature_table_refuses_a_write() -> None:
    features = cast("dict[Feature, Support]", get_profile("allegro-2").features)
    with pytest.raises(TypeError):
        features[Feature.MIRROR] = YES
    assert get_profile("allegro-2").support(Feature.MIRROR) == NO


@pytest.mark.os_agnostic
def test_a_profile_keeps_the_features_it_was_made_with_when_the_given_dict_changes() -> None:
    given = {Feature.MIRROR: NO}
    profile = dataclasses.replace(get_profile("generic"), features=given)
    given[Feature.MIRROR] = YES
    assert profile.support(Feature.MIRROR) == NO


@pytest.mark.os_agnostic
@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_a_profile_pickles_by_every_protocol(protocol: int) -> None:
    """``copy.deepcopy`` below rebuilds through the same ``__reduce__`` the pickled bytes hold."""
    assert pickle.dumps(get_profile("dmx-600"), protocol=protocol)


@pytest.mark.os_agnostic
def test_a_profile_deep_copies_and_dataclasses_asdict_reads_its_features() -> None:
    profile = get_profile("allegro")
    copied = copy.deepcopy(profile)
    assert copied == profile
    assert copied.features is not profile.features
    assert dataclasses.asdict(profile)["features"] == profile.features
