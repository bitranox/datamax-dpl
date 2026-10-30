"""Which fields share printed dots (overlap detection) and how a transparent label is emulated."""

from __future__ import annotations

import pytest

from datamax_dpl.domain.decoder import explain
from datamax_dpl.domain.enums import FillPattern, Unit
from datamax_dpl.domain.label import Box, Element, Image, Label, Line, Text
from datamax_dpl.domain.overlap import DotArea, OverlapReport, find_overlaps, printed_dots
from datamax_dpl.domain.render import build_job, job_context
from datamax_dpl.domain.render_context import RenderOptions

#: The Allegro 2 prints 1/100 in as two whole dots; without its default along offset every field
#: lands on the dot its record names.
ALLEGRO_2 = RenderOptions(model="allegro-2", offset_along=0.0)


def overlaps(*elements: Element, options: RenderOptions = ALLEGRO_2) -> OverlapReport:
    """Find the overlaps of a label in printer dots."""
    label = Label(unit=Unit.DOTS, elements=list(elements))
    return find_overlaps(label.elements, job_context(label, options))


def hollow_box() -> Box:
    return Box(x=20, y=20, width=100, height=60, thickness=4)


@pytest.mark.os_agnostic
def test_a_line_inside_a_hollow_box_shares_no_dot_with_it() -> None:
    assert overlaps(hollow_box(), Line(x=40, y=50, x2=100, y2=50, thickness=2)) == OverlapReport()


@pytest.mark.os_agnostic
def test_a_line_crossing_the_box_edge_overlaps_it() -> None:
    assert overlaps(hollow_box(), Line(x=40, y=50, x2=160, y2=50, thickness=2)).groups == ((0, 1),)


@pytest.mark.os_agnostic
def test_a_chain_of_three_overlaps_is_one_group() -> None:
    report = overlaps(
        Line(x=300, y=10, x2=300, y2=200, thickness=4),
        hollow_box(),
        Line(x=110, y=50, x2=310, y2=50, thickness=2),
    )
    assert report.groups == ((0, 1, 2),)


@pytest.mark.os_agnostic
def test_fields_apart_do_not_overlap() -> None:
    assert overlaps(hollow_box(), Line(x=200, y=50, x2=300, y2=50, thickness=2)) == OverlapReport()


@pytest.mark.os_agnostic
def test_two_boxes_that_touch_without_sharing_a_dot_do_not_overlap() -> None:
    left = Box(x=20, y=20, width=40, height=40, thickness=2, fill=FillPattern.SOLID)
    right = Box(x=60, y=20, width=40, height=40, thickness=2, fill=FillPattern.SOLID)
    assert overlaps(left, right) == OverlapReport()
    assert overlaps(left, right.model_copy(update={"x": 58})).groups == ((0, 1),)


@pytest.mark.os_agnostic
def test_a_text_over_one_member_leaves_the_group_mergeable_and_is_reported_inexact() -> None:
    report = overlaps(
        hollow_box(),
        Line(x=40, y=50, x2=160, y2=50, thickness=2),
        Text(x=22, y=70, text="HELLO", font=2),
    )
    assert report == OverlapReport(groups=((0, 1),), inexact=(2,))


@pytest.mark.os_agnostic
def test_a_text_inside_the_hollow_of_a_box_meets_no_dot_of_it() -> None:
    """The text's rectangle lies in the box's white interior: no dot of the box is under it."""
    assert overlaps(hollow_box(), Text(x=40, y=40, text="HI", font=2)) == OverlapReport()


@pytest.mark.os_agnostic
def test_two_texts_whose_rectangles_meet_are_both_inexact() -> None:
    report = overlaps(Text(x=20, y=20, text="HELLO", font=2), Text(x=30, y=25, text="WORLD", font=2))
    assert report == OverlapReport(inexact=(0, 1))


@pytest.mark.os_agnostic
def test_a_stored_image_takes_part_by_the_preview_s_placeholder_square() -> None:
    report = overlaps(Image(x=0, y=0, name="LOGO"), Line(x=30, y=30, x2=200, y2=30, thickness=2))
    assert report == OverlapReport(inexact=(0,))


@pytest.mark.os_agnostic
def test_an_image_shares_dots_by_its_bitmap_not_its_rectangle() -> None:
    """A ring image around a dot-sized box: their rectangles meet, their dots do not."""
    ring = Image(x=20, y=20, name="RING", bitmap=["#####", "#...#", "#...#", "#...#", "#####"], magnification=4)
    inside = Box(x=28, y=28, width=4, height=4, thickness=2, fill=FillPattern.SOLID)
    assert overlaps(ring, inside) == OverlapReport()
    assert overlaps(ring, inside.model_copy(update={"x": 22})).groups == ((0, 1),)


@pytest.mark.os_agnostic
def test_many_small_fields_apart_overlap_nowhere() -> None:
    """200 fields in a grid; the sweep compares only neighbours whose rectangles meet."""
    elements: list[Element] = [
        Box(x=10 + 20 * (index % 20), y=10 + 20 * (index // 20), width=10, height=10, thickness=2)
        for index in range(200)
    ]
    assert overlaps(*elements) == OverlapReport()


@pytest.mark.os_agnostic
def test_lines_and_box_outlines_are_the_dots_their_records_print() -> None:
    """Pinned against the decoded job: each record's units times the Allegro 2's 2 dots per unit."""
    label = Label(
        unit=Unit.INCH,
        elements=[
            Box(x=0.21, y=0.33, width=1.37, height=0.59, thickness=0.03, side_thickness=0.05),
            Line(x=0.5, y=0.13, x2=1.77, y2=0.13, thickness=0.02),
            Line(x=1.9, y=0.4, x2=1.9, y2=0.07, thickness=0.04),
        ],
    )
    decoded = explain(build_job(label, ALLEGRO_2).data).label
    assert decoded is not None
    ctx = job_context(label, ALLEGRO_2)
    for element, record in zip(label.elements, decoded.elements, strict=True):
        dots = printed_dots(element, ctx)
        assert dots is not None
        assert dots.area == _record_area(record)
        assert dots.bitmap.black_pixels() == _record_black_dots(record)


def _units(value: float) -> int:
    return round(value * 100)


def _record_area(record: Element) -> DotArea:
    """The rectangle a decoded line or box record prints on the Allegro 2 (2 dots per 1/100 in)."""
    if isinstance(record, Line):
        width, height = _units(record.x2 - record.x), _units(record.y2 - record.y)
        width, height = (width, _units(record.thickness)) if height == 0 else (_units(record.thickness), height)
    else:
        assert isinstance(record, Box)
        width, height = _units(record.width), _units(record.height)
    return DotArea(left=2 * _units(record.x), bottom=2 * _units(record.y), width=2 * width, height=2 * height)


def _record_black_dots(record: Element) -> int:
    area = _record_area(record)
    if isinstance(record, Line):
        return area.width * area.height
    assert isinstance(record, Box)
    edge, side = 2 * _units(record.thickness), 2 * _units(record.side_thickness or record.thickness)
    return area.width * area.height - (area.width - 2 * side) * (area.height - 2 * edge)
