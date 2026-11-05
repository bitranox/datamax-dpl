"""The feed that ends a run on continuous media: back to the label sensor, then to the stop position."""

from __future__ import annotations

import pytest

from datamax_dpl.domain.enums import FindingCode, MediaSensor, PrinterStop
from datamax_dpl.domain.media_feed import media_feed
from datamax_dpl.domain.render_context import RenderOptions


@pytest.mark.os_agnostic
def test_the_allegro_2_leaves_continuous_mode_for_the_gap_sensor_and_stops_at_tear_off() -> None:
    """The sequence measured twice on one Allegro 2 (2026-11-05): the stock stopped at the tear bar."""
    feed = media_feed(MediaSensor.GAP, RenderOptions(model="allegro-2"))

    assert feed.data == b"\x02n\x02c0000\x02e\x02f263\x02F"
    assert feed.errors == ()


@pytest.mark.os_agnostic
def test_reflective_media_selects_the_reflective_sensor() -> None:
    assert (
        media_feed(MediaSensor.REFLECTIVE, RenderOptions(model="allegro-2")).data == b"\x02n\x02c0000\x02r\x02f263\x02F"
    )


@pytest.mark.os_agnostic
def test_a_configured_stop_position_is_the_one_sent() -> None:
    feed = media_feed(MediaSensor.GAP, RenderOptions(model="allegro-2", stop_position_mm=50.8))

    assert feed.data == b"\x02n\x02c0000\x02e\x02f200\x02F"


@pytest.mark.os_agnostic
def test_the_printers_own_stop_sends_no_stop_position() -> None:
    feed = media_feed(MediaSensor.GAP, RenderOptions(model="allegro-2", stop_position_mm=PrinterStop.PRINTER))

    assert feed.data == b"\x02n\x02c0000\x02e\x02F"


@pytest.mark.os_agnostic
def test_the_feed_is_sent_even_where_the_settings_say_send_nothing() -> None:
    """Asking for the media is the point of the command, so send_settings does not hold it back."""
    feed = media_feed(MediaSensor.GAP, RenderOptions(model="allegro-2", send_settings=frozenset()))

    assert feed.data == b"\x02n\x02c0000\x02e\x02f263\x02F"


@pytest.mark.os_agnostic
def test_the_prodigy_ends_continuous_mode_but_cannot_choose_a_sensor_or_a_stop() -> None:
    feed = media_feed(MediaSensor.GAP, RenderOptions(model="prodigy"))

    assert feed.data == b"\x02n\x02c0000\x02F"
    assert FindingCode.CONFIGURED_SENSOR_SELECT_UNSUPPORTED in [finding.code for finding in feed.findings]
    assert feed.errors == ()


@pytest.mark.os_agnostic
def test_continuous_media_is_refused() -> None:
    """Continuous media has no edge to feed to, and its length is what a label sets."""
    with pytest.raises(ValueError, match="continuous"):
        media_feed(MediaSensor.CONTINUOUS, RenderOptions(model="allegro-2"))
