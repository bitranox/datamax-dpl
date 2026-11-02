"""A few bytes of label cannot make the encoder allocate or draw more than a job could ever send."""

from __future__ import annotations

import time
import tracemalloc
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.domain.enums import Attribute, FillPattern, FindingCode, Unit
from datamax_dpl.domain.label import Box, Circle, Element, Label, Line, Point, Polygon, Text
from datamax_dpl.domain.render import RenderResult, build_job
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from collections.abc import Callable

#: Far more than a refused drawing needs and far less than one drawn first: 2.2 GB at radius 10000.
PEAK_BYTES_LIMIT = 256 * 1024 * 1024
#: Generous for a loaded runner; the same inputs drawn first took tens of seconds and more.
SECONDS_LIMIT = 30.0

#: The inputs measured to cost gigabytes or minutes, each a line or two of JSON. All take the
#: raster fallback on the Allegro 2, which has no circle or polygon record.
OVERSIZED: list[tuple[str, Element, FindingCode]] = [
    ("circle-10000", Circle(x=20000, y=20000, radius=10000), FindingCode.IMAGE_TOO_WIDE),
    ("circle-100000", Circle(x=100000, y=100000, radius=100000), FindingCode.IMAGE_TOO_WIDE),
    (
        "polygon-10000",
        Polygon(points=[Point(x=0, y=0), Point(x=10000, y=0), Point(x=0, y=10000)]),
        FindingCode.IMAGE_TOO_WIDE,
    ),
    (
        "polygon-100000",
        Polygon(points=[Point(x=0, y=0), Point(x=100000, y=0), Point(x=0, y=100000)]),
        FindingCode.IMAGE_TOO_WIDE,
    ),
    ("slant-line-across", Line(x=0, y=0, x2=100000, y2=1, thickness=1), FindingCode.IMAGE_TOO_WIDE),
    ("slant-line-along", Line(x=0, y=0, x2=1, y2=100000, thickness=1), FindingCode.DISTANCE_TOO_LARGE),
    (
        "patterned-box",
        Box(x=0, y=0, width=100000, height=100000, thickness=1, fill=FillPattern.SHADE_6),
        FindingCode.IMAGE_TOO_WIDE,
    ),
]


def _bounded(work: Callable[[], RenderResult]) -> RenderResult:
    """Run ``work`` and hold it to the limits; the assertion names what was over."""
    started = time.monotonic()
    tracemalloc.start()
    try:
        result = work()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < PEAK_BYTES_LIMIT, f"{peak} bytes at peak"
    assert time.monotonic() - started < SECONDS_LIMIT
    return result


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("element", "code"), [case[1:] for case in OVERSIZED], ids=[case[0] for case in OVERSIZED])
def test_an_oversized_drawing_is_refused_before_it_is_drawn(element: Element, code: FindingCode) -> None:
    result = _bounded(lambda: build_job(Label(elements=[element]), RenderOptions(model="allegro-2")))
    assert code in {finding.code for finding in result.errors}
    assert all(finding.element == 0 for finding in result.errors if finding.code is code)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("element", [case[1] for case in OVERSIZED], ids=[case[0] for case in OVERSIZED])
@pytest.mark.parametrize("model", ["generic", "dmx-600", "prodigy"])
def test_every_model_keeps_the_same_bound_on_the_work(element: Element, model: str) -> None:
    """A model with native records draws nothing to send, and may still not draw to compare."""
    _bounded(lambda: build_job(Label(elements=[element]), RenderOptions(model=model)))


@pytest.mark.os_agnostic
def test_a_box_past_the_format_length_is_refused_without_being_drawn() -> None:
    """The check of the format length asks for a field's dots: a native box must not be drawn for it."""
    label = Label(elements=[Box(x=0, y=0, width=100000, height=100000, thickness=1)])
    result = _bounded(lambda: build_job(label, RenderOptions(model="allegro-2")))
    assert FindingCode.FORMAT_PAST_MAX_LENGTH in {finding.code for finding in result.errors}


@pytest.mark.os_agnostic
def test_a_huge_transparent_label_is_bounded_too() -> None:
    """The overlap check draws every exact field; an oversized one takes part by its rectangle."""
    label = Label(
        attribute=Attribute.TRANSPARENT,
        elements=[Box(x=0, y=0, width=100000, height=100000, thickness=1), Text(x=5, y=5, text="A")],
    )
    result = _bounded(lambda: build_job(label, RenderOptions(model="allegro-2")))
    assert not result.ok


@pytest.mark.os_agnostic
def test_the_widest_graphic_one_row_record_holds_is_still_drawn() -> None:
    """2040 dots is one row record's width: the bound refuses past it, not at it."""
    label = Label(unit=Unit.DOTS, elements=[Circle(x=1000, y=1000, radius=1000)])
    codes = {finding.code for finding in build_job(label, RenderOptions(model="allegro-2")).findings}
    assert FindingCode.IMAGE_TOO_WIDE not in codes
    assert FindingCode.CIRCLE_RASTERIZED in codes
