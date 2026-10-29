"""Printer profiles: the calibration defaults a model ships, measured on a real printer."""

from __future__ import annotations

import pytest

from datamax_dpl.domain.profiles import CalibrationDefaults, get_profile


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
    assert _values(calibration) == (1.0, 0.976, 0.0, -0.25, 67.4)
    assert calibration.provenance


@pytest.mark.os_agnostic
def test_other_models_keep_neutral_calibration() -> None:
    calibration = get_profile("dmx-600").calibration
    assert _values(calibration) == (1.0, 1.0, 0.0, 0.0, None)
    assert calibration.provenance == ""
