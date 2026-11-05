"""The library's calibration labels follow the model's row limit when given render options.

The user's decision of 2026-11-01 ([225], "Follow the model"): ``along_label`` and ``calibration_labels``
take an optional ``RenderOptions`` and, when given one, default the along bar to the longest that one format
holds (``default_along_length``, 190 mm on the Allegro 2), as ``calibrate print`` does. Without it the
200 mm default stays.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from datamax_dpl.domain.calibration import ALONG_LENGTH_MM, along_label, calibration_labels, default_along_length
from datamax_dpl.domain.enums import FindingCode
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from datamax_dpl.domain.label import Label

pytestmark = pytest.mark.os_agnostic

_ALLEGRO_2 = RenderOptions(model="allegro-2")


def _refused_for_length(label: Label, options: RenderOptions) -> bool:
    return any(f.code is FindingCode.FORMAT_PAST_MAX_LENGTH for f in build_job(label, options).findings)


def test_without_options_the_along_bar_keeps_the_200_mm_default() -> None:
    assert along_label(width=100, title="X").height == ALONG_LENGTH_MM + 5
    assert calibration_labels(width=100, height=55, title="X").along.height == ALONG_LENGTH_MM + 5


def test_the_200_mm_default_is_refused_on_the_allegro_2() -> None:
    """The case the options fix: without them the library's label does not print there."""
    assert _refused_for_length(along_label(width=100, title="X"), _ALLEGRO_2)


@pytest.mark.parametrize("model", ["allegro-2", "dmx-600", "generic"])
def test_with_options_the_along_bar_is_the_longest_the_model_holds(model: str) -> None:
    options = RenderOptions(model=model)
    expected = default_along_length(width=100, title="X", options=options)

    label = along_label(width=100, title="X", options=options)

    assert label.height == expected + 5
    assert not _refused_for_length(label, options)


def test_the_allegro_2_bar_is_190_mm() -> None:
    assert along_label(width=100, title="X", options=_ALLEGRO_2).height == 195


def test_calibration_labels_pass_the_options_to_the_along_label() -> None:
    labels = calibration_labels(width=100, height=55, title="X", options=_ALLEGRO_2)

    assert labels.along.height == 195
    assert labels.across.height == 55
    assert not _refused_for_length(labels.along, _ALLEGRO_2)


def test_a_length_given_wins_over_the_options() -> None:
    """As in ``calibrate print --along-length``: the caller's length is built, refused or not."""
    assert along_label(width=100, length=60, title="X", options=_ALLEGRO_2).height == 65
    assert calibration_labels(width=100, height=55, title="X", along_length=200, options=_ALLEGRO_2).along.height == 205


def test_the_title_the_bar_is_sized_with_is_the_title_printed() -> None:
    """A long title takes more rows; the length that fits is found for the title the label carries."""
    title = "ALLEGRO 2 LAGER SCALE 0.976"
    options = RenderOptions(model="allegro-2")

    label = along_label(width=100, title=title, options=options)

    assert label.height == default_along_length(width=100, title=title, options=options) + 5
    assert not _refused_for_length(label, options)
