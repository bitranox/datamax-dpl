"""The feed that ends a run on continuous media: back to the label sensor, then to the stop position."""

from __future__ import annotations

import pytest

from datamax_dpl.domain.enums import FindingCode, MediaSensor, PrinterStop
from datamax_dpl.domain.media_feed import media_feed, plain_feed
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


@pytest.mark.os_agnostic
def test_with_a_cutter_installed_the_feed_turns_it_off_so_the_fed_label_is_not_cut() -> None:
    """With the cutter on, an Allegro 2 cut the fed label at the tear-off stop, 10 mm short (2026-11-06)."""
    feed = media_feed(MediaSensor.GAP, RenderOptions(model="allegro-2", cutter_installed=True))

    assert feed.data == b"\x02n\x02c0000\x02e\x02V0\x02f263\x02F"


@pytest.mark.os_agnostic
def test_a_default_cut_does_not_make_the_feed_cut() -> None:
    feed = media_feed(MediaSensor.GAP, RenderOptions(model="allegro-2", cutter_installed=True, cut_every=2))

    assert feed.data == b"\x02n\x02c0000\x02e\x02V0\x02f263\x02F"


@pytest.mark.os_agnostic
def test_a_plain_feed_turns_an_installed_cutter_off_first() -> None:
    """The user's rule on site: a fed label is not cut, the knife stays free of the labels' adhesive."""
    feed = plain_feed(RenderOptions(model="allegro-2", cutter_installed=True))

    assert feed.data == b"\x02V0\x02F"


@pytest.mark.os_agnostic
def test_a_default_cut_does_not_make_a_plain_feed_cut() -> None:
    feed = plain_feed(RenderOptions(model="allegro-2", cutter_installed=True, cut_every=2))

    assert feed.data == b"\x02V0\x02F"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("cut_every", [None, 2], ids=["cut after print", "cut every"])
@pytest.mark.parametrize("feed", ["media", "plain"])
def test_a_feed_reports_no_cutter_stop_since_it_is_never_cut(feed: str, cut_every: int | None) -> None:
    """A feed's label is never cut, so its findings name no cutter stop, whatever cut the options give a job."""
    options = RenderOptions(model="allegro-2", cutter_installed=True, cut_every=cut_every)

    result = media_feed(MediaSensor.GAP, options) if feed == "media" else plain_feed(options)

    assert [f.message for f in result.findings if "cutter_stop_position" in f.message] == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "options",
    [RenderOptions(model="allegro-2"), RenderOptions(model="generic", cutter_installed=True)],
    ids=["cutter unknown", "no bit layout"],
)
def test_a_plain_feed_that_cannot_set_the_cutter_says_so_and_still_feeds(options: RenderOptions) -> None:
    feed = plain_feed(options)

    assert feed.data == b"\x02F"
    assert FindingCode.SOFTWARE_SWITCHES_NOT_SENT in {finding.code for finding in feed.findings}


@pytest.mark.os_agnostic
def test_a_plain_feed_reports_nothing_it_does_not_send() -> None:
    """It sends only <STX>V and the form feed: no stop, no format, so no model default and no unit."""
    feed = plain_feed(RenderOptions(model="allegro-2", cutter_installed=True))

    assert feed.data == b"\x02V0\x02F"
    assert feed.findings == ()


@pytest.mark.os_agnostic
def test_a_plain_feed_that_cannot_set_the_cutter_reports_only_that() -> None:
    feed = plain_feed(RenderOptions(model="allegro-2"))

    assert [finding.code for finding in feed.findings] == [FindingCode.SOFTWARE_SWITCHES_NOT_SENT]


@pytest.mark.os_agnostic
def test_a_media_feed_names_the_model_stop_it_sends_and_no_format_default() -> None:
    """The feed sends the model's stop (f263) but no format, so scale, offset and units do not apply."""
    feed = media_feed(MediaSensor.GAP, RenderOptions(model="allegro-2"))

    defaults = [f.message for f in feed.findings if f.code is FindingCode.MODEL_DEFAULTS_APPLIED]
    assert len(defaults) == 1
    assert defaults[0].endswith(": stop_position 66.8 mm")
    assert FindingCode.INCH_UNITS not in {finding.code for finding in feed.findings}


@pytest.mark.os_agnostic
def test_a_media_feed_with_a_configured_stop_names_no_model_default() -> None:
    feed = media_feed(MediaSensor.GAP, RenderOptions(model="allegro-2", stop_position_mm=50.8, cutter_installed=True))

    assert feed.findings == ()
