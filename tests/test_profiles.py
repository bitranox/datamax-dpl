"""Printer profiles: the calibration defaults a model ships, measured on a real printer."""

from __future__ import annotations

import pytest

from datamax_dpl.domain.profiles import CalibrationDefaults, Feature, get_profile, list_profiles


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
_IMAGE_MODULES: dict[str, str | None] = {
    "generic": "A",
    "permissive": "A",
    "allegro": "A",
    "allegro-2": "A",
    "dmx-400": "A",
    "dmx-430": "A",
    "prodigy-max-203": "A",
    "prodigy-max-300": "A",
    "pe42": "A",
    "pe43": "A",
    "ovation": "A",
    "ovation-300": "A",
    "ovation-2": "A",
    "titan-6200": "A",
    "dmx-600": "E",
    "dmx-800": "E",
    "prodigy": None,
    "prodigy-152": None,
    "prodigy-plus": None,
    "prodigy-plus-152": None,
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("model", "letter"), sorted(_IMAGE_MODULES.items()))
def test_each_model_downloads_images_to_its_built_in_module(model: str, letter: str | None) -> None:
    assert get_profile(model).image_module == letter


@pytest.mark.os_agnostic
def test_the_image_module_table_names_every_profile() -> None:
    assert {p.id for p in list_profiles()} == set(_IMAGE_MODULES)
