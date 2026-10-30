"""Which fields share printed dots (overlap detection) and how a transparent label is emulated."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from datamax_dpl.application.use_cases import render_batch
from datamax_dpl.domain.decoder import explain
from datamax_dpl.domain.enums import CombineMode, FallbackPolicy, FillPattern, FindingCode, Severity, Unit
from datamax_dpl.domain.errors import LabelRejectedError
from datamax_dpl.domain.image_budget import halve
from datamax_dpl.domain.image_records import decode_image
from datamax_dpl.domain.label import Box, Element, Image, Label, Line, Text
from datamax_dpl.domain.overlap import DotArea, OverlapReport, find_overlaps, printed_dots
from datamax_dpl.domain.preview import canvas_for, preview_bitmap
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.raster import Bitmap
from datamax_dpl.domain.render import RenderResult, build_job, job_context, render
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from datamax_dpl.domain.findings import Finding

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


# ======================== emulating a transparent label ========================

STX = b"\x02"
#: 40 x 20 dots, a checkerboard of 2-dot squares; the box below covers its edge.
CHECKER = ["".join("#" if (x // 2 + y // 2) % 2 else "." for x in range(40)) for y in range(20)]
LOGO = {"type": "image", "x": 10, "y": 10, "name": "LOGO", "bitmap": CHECKER}
FRAME = {"type": "box", "x": 10, "y": 10, "width": 40, "height": 20, "thickness": 2}
NOTE = {"type": "text", "x": 12, "y": 24, "text": "HI", "font": 1}
APART = {"type": "box", "x": 200, "y": 10, "width": 40, "height": 20, "thickness": 2}


def transparent(*elements: dict[str, Any], attribute: int | None = 2) -> Label:
    """A label in dots that asks for transparency (attribute 2) unless told otherwise."""
    return Label.model_validate({"unit": "dots", "attribute": attribute, "elements": list(elements)})


def codes(result: RenderResult) -> list[FindingCode]:
    return [finding.code for finding in result.findings]


def only(result: RenderResult, code: FindingCode) -> Finding:
    [finding] = [f for f in result.findings if f.code is code]
    return finding


def format_records(data: bytes) -> list[bytes]:
    return [line for line in data[data.index(STX + b"L\r") :].split(b"\r") if line[:1] in (b"1", b"2", b"3", b"4")]


def downloaded(data: bytes, name: str) -> Bitmap:
    """Decode the image the job downloads under ``name``, bottom row first as the printer reads it."""
    head = STX + b"IAAF" + name.encode("ascii") + b"\r"
    start = data.index(head) + len(head)
    end = data.index(b"\rFFFF\r", start - 1) + len(b"\rFFFF\r")
    return decode_image(data[start:end])


def black_set(bitmap: Bitmap, *, width: int | None = None) -> set[tuple[int, int]]:
    columns = bitmap.width if width is None else width
    return {(x, y) for y in range(bitmap.height) for x in range(columns) if bitmap.get(x, y)}


def outline() -> Bitmap:
    """The 2-dot outline of a box the checkerboard's size."""
    bands = Bitmap(40, 20)
    for x, y, w, h in ((0, 0, 40, 2), (0, 18, 40, 2), (0, 0, 2, 20), (38, 0, 2, 20)):
        bands.fill_rect(x, y, w, h)
    return bands


def frame_over_checker(*, mode: CombineMode = CombineMode.OR) -> Bitmap:
    """The checkerboard and the outline combined, OR (transparent) unless told otherwise."""
    picture = Bitmap.from_rows(CHECKER)
    picture.combine(outline(), 0, 0, mode=mode)
    return picture


@pytest.mark.os_agnostic
def test_a_box_over_an_image_prints_as_one_downloaded_image_holding_both() -> None:
    result = build_job(transparent(LOGO, FRAME), ALLEGRO_2)
    [name] = result.downloads
    assert name != "LOGO"
    assert format_records(result.data) == [f"1Y1100000050005{name}".encode("ascii")]
    assert black_set(downloaded(result.data, name), width=40) == black_set(frame_over_checker())
    assert result.emulated_groups == ((0, 1),)
    finding = only(result, FindingCode.TRANSPARENCY_EMULATED)
    assert finding.severity is Severity.WARNING
    assert "elements 0, 1" in finding.message
    assert finding.fallback is not None
    assert f"{name} (40x20 dots, 100 bytes)" in finding.fallback
    assert FindingCode.ATTRIBUTE_UNSUPPORTED not in codes(result)


@pytest.mark.os_agnostic
def test_a_text_over_a_merged_group_is_reported_as_printing_xor() -> None:
    result = build_job(transparent(LOGO, FRAME, NOTE), ALLEGRO_2)
    assert result.emulated_groups == ((0, 1),)
    finding = only(result, FindingCode.TRANSPARENCY_NOT_EMULATED)
    assert (finding.element, finding.severity) == (2, Severity.WARNING)
    assert "those fields print XOR on Allegro 2" in finding.message
    assert [record[:2] for record in format_records(result.data)] == [b"1Y", b"11"]


@pytest.mark.os_agnostic
def test_strict_refuses_an_emulated_transparency() -> None:
    strict = ALLEGRO_2.model_copy(update={"policy": FallbackPolicy.STRICT})
    result = build_job(transparent(LOGO, FRAME, NOTE), strict)
    assert {f.code for f in result.errors} == {FindingCode.TRANSPARENCY_EMULATED, FindingCode.TRANSPARENCY_NOT_EMULATED}
    with pytest.raises(LabelRejectedError, match="those fields print XOR on Allegro 2"):
        render(transparent(LOGO, FRAME, NOTE), strict)


@pytest.mark.os_agnostic
def test_a_model_with_the_attribute_command_gets_a2_and_no_merge() -> None:
    result = build_job(transparent(LOGO, FRAME), RenderOptions(model="dmx-600"))
    assert b"\rA2\r" in result.data
    assert result.downloads == ("LOGO",)
    assert result.emulated_groups == ()
    assert [record[:2] for record in format_records(result.data)] == [b"1Y", b"1X"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("attribute", [None, 1, 3])
def test_without_transparency_the_allegro_2_job_is_unchanged(attribute: int | None) -> None:
    result = build_job(transparent(LOGO, FRAME, attribute=attribute), ALLEGRO_2)
    assert result.downloads == ("LOGO",)
    assert format_records(result.data) == [b"1Y1100000050005LOGO", b"1X1100000050005B020010001001"]
    assert result.emulated_groups == ()
    transparency = {c for c in codes(result) if c.value.startswith("transparency")}
    assert transparency == set()
    assert (FindingCode.ATTRIBUTE_UNSUPPORTED in codes(result)) is (attribute is not None)


@pytest.mark.os_agnostic
def test_a_transparent_label_without_overlaps_prints_the_same_and_says_so() -> None:
    plain = build_job(transparent(LOGO, APART, attribute=None), ALLEGRO_2)
    result = build_job(transparent(LOGO, APART), ALLEGRO_2)
    assert result.data == plain.data
    finding = only(result, FindingCode.TRANSPARENCY_NOT_NEEDED)
    assert finding.severity is Severity.INFO
    assert "no fields overlap" in finding.message
    assert [f for f in result.findings if f.severity is not Severity.INFO] == []


@pytest.mark.os_agnostic
def test_a_merged_image_past_the_budget_is_halved_like_any_other() -> None:
    options = ALLEGRO_2.model_copy(update={"image_memory_bytes": 60})
    result = build_job(transparent(LOGO, FRAME), options)
    [name] = result.downloads
    assert format_records(result.data) == [f"1Y2200000050005{name}".encode("ascii")]
    assert result.halved_elements == (0, 1)
    assert "elements 0, 1 sent at half resolution" in (only(result, FindingCode.IMAGE_HALVED).fallback or "")
    halved = halve(frame_over_checker())
    assert black_set(downloaded(result.data, name), width=halved.width) == black_set(halved)


@pytest.mark.os_agnostic
def test_a_qr_code_in_a_group_is_never_halved() -> None:
    qr = {"type": "qr", "x": 10, "y": 10, "data": "HELLO", "module_dots": 2, "quiet_zone": 0}
    options = ALLEGRO_2.model_copy(update={"image_memory_bytes": 60})
    result = build_job(transparent(qr, FRAME), options)
    assert result.emulated_groups == ((0, 1),)
    assert result.halved_elements == ()
    assert FindingCode.IMAGE_MEMORY_EXCEEDED in {f.code for f in result.errors}


@pytest.mark.os_agnostic
def test_each_record_of_a_batch_merges_its_own_qr_code() -> None:
    qr = {"type": "qr", "x": 10, "y": 10, "data": "{sku}", "module_dots": 2, "quiet_zone": 0}
    batch = render_batch(transparent(qr, FRAME), [{"sku": "A-1"}, {"sku": "B-2"}], ALLEGRO_2)
    names = [result.downloads for result in batch.results]
    assert [len(downloads) for downloads in names] == [1, 1]
    assert names[0] != names[1]
    assert [result.emulated_groups for result in batch.results] == [((0, 1),), ((0, 1),)]


@pytest.mark.os_agnostic
def test_a_member_keeps_its_own_refusal() -> None:
    """A QR Code past the print width is refused whether or not it is merged."""
    qr = {"type": "qr", "x": 800, "y": 10, "data": "HELLO", "module_dots": 2, "quiet_zone": 0}
    box = {"type": "box", "x": 790, "y": 10, "width": 40, "height": 20, "thickness": 2}
    result = build_job(transparent(qr, box), ALLEGRO_2)
    assert result.emulated_groups == ((0, 1),)
    assert only(result, FindingCode.QR_PAST_PRINT_WIDTH).element == 0


@pytest.mark.os_agnostic
def test_a_merged_image_another_field_places_by_name_is_still_downloaded() -> None:
    stored = {"type": "image", "x": 300, "y": 10, "name": "LOGO"}
    result = build_job(transparent(LOGO, FRAME, stored), ALLEGRO_2)
    assert result.emulated_groups == ((0, 1),)
    assert "LOGO" in result.downloads
    assert format_records(result.data)[-1] == b"1Y1100000050150LOGO"


@pytest.mark.os_agnostic
def test_the_preview_draws_an_emulated_group_transparent() -> None:
    document = transparent(LOGO, FRAME)
    profile = get_profile("allegro-2")
    result = build_job(document, ALLEGRO_2)
    canvas = canvas_for(document, profile)
    left, bottom = canvas.point(10, 10)

    def drawn(picture: Bitmap) -> set[tuple[int, int]]:
        top = bottom - 19
        return {
            (x - left, y - top) for x in range(left, left + 40) for y in range(top, bottom + 1) if picture.get(x, y)
        }

    assert drawn(preview_bitmap(document, profile, sent_as=result.sent_as)) == black_set(frame_over_checker())
    assert drawn(preview_bitmap(document, profile)) == black_set(frame_over_checker(mode=CombineMode.XOR))


@pytest.mark.os_agnostic
def test_a_merged_image_at_magnification_9_is_halved_with_its_group_not_refused() -> None:
    """Alone it could not be halved (magnification 18); merged, the group's image is placed at 2."""
    big = {"type": "image", "x": 10, "y": 10, "name": "BIG", "bitmap": ["#.", ".#"], "magnification": 9}
    box = {"type": "box", "x": 10, "y": 10, "width": 18, "height": 18, "thickness": 2}
    options = ALLEGRO_2.model_copy(update={"image_memory_bytes": 20})
    result = build_job(transparent(big, box), options)
    assert [f.message for f in result.errors] == []
    assert result.halved_elements == (0, 1)
