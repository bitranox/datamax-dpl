"""Fixed-shape results come back as named value objects, and the text built from them is unchanged.

A named tuple compares equal to the plain tuple it replaced, so an ``==`` assertion passes whether
or not the named type arrived; these tests check the type itself and read a field by name. The
finding messages built from the records are pinned as exact strings, since they are what a caller
reads.
"""

from __future__ import annotations

import pytest

from datamax_dpl import CalibrationLabels, calibration_labels
from datamax_dpl.domain.batch import BatchCuts, batch_cuts
from datamax_dpl.domain.encode_graphics import qr_symbol_position
from datamax_dpl.domain.enums import FindingCode, Rotation, Unit
from datamax_dpl.domain.geometry import DotPoint, Rect, Span
from datamax_dpl.domain.label import Cut, DotSize, Label, Qr, Text
from datamax_dpl.domain.preview import CellMm, FieldArea, canvas_for, widest_cell_mm
from datamax_dpl.domain.profiles import Feature, Firmware, UndescribedBit, get_profile, parse_firmware
from datamax_dpl.domain.raster import Bitmap
from datamax_dpl.domain.records import Magnification, RecordPosition
from datamax_dpl.domain.render import build_job, job_context
from datamax_dpl.domain.render_context import PlacedImage, RenderOptions
from datamax_dpl.domain.units import Axis

_HI = Label(elements=[Text(x=5, y=5, text="Hi")])


@pytest.mark.os_agnostic
def test_parse_firmware_returns_a_firmware_that_orders_by_major_then_minor() -> None:
    firmware = parse_firmware("2.1")

    assert type(firmware) is Firmware
    assert (firmware.major, firmware.minor) == (2, 10)
    assert Firmware(major=2, minor=9) < firmware < Firmware(major=3, minor=0)


@pytest.mark.os_agnostic
def test_a_firmware_requirement_reads_as_major_dot_two_digit_minor() -> None:
    support = get_profile("prodigy-plus").support(Feature.CUT_NOW)

    assert type(support.min_firmware) is Firmware
    assert support.describe() == "fw 1.18"


@pytest.mark.os_agnostic
def test_the_undescribed_switch_bits_are_named_records() -> None:
    switches = get_profile("prodigy").software_switches

    assert switches is not None
    assert switches.undescribed == (UndescribedBit(bit=1, option="invert ribbon saver"),)
    assert switches.undescribed[0].option == "invert ribbon saver"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("model", "message"),
    [
        (
            "prodigy",
            "software switches (<STX>V) not sent: Prodigy has bits no setting describes (bit 1 invert ribbon saver)",
        ),
        (
            "dmx-430",
            "software switches (<STX>V) not sent: DMX 430 has no known bit layout (manual p.42)",
        ),
        (
            "allegro-2",
            "software switches (<STX>V) not sent: whether the cutter is installed is unknown"
            " (set RenderOptions.cutter_installed)",
        ),
    ],
)
def test_the_reason_the_switches_are_not_sent_reads_as_before(model: str, message: str) -> None:
    findings = build_job(_HI, RenderOptions(model=model)).findings

    assert [f.message for f in findings if f.code is FindingCode.SOFTWARE_SWITCHES_NOT_SENT] == [message]


@pytest.mark.os_agnostic
def test_each_setting_without_a_known_value_is_named_with_the_key_that_sets_it() -> None:
    findings = build_job(_HI, RenderOptions(model="generic")).findings

    assert [f.message for f in findings if f.code is FindingCode.PRINTER_SETTINGS_UNKNOWN] == [
        "not sent, Any model (common subset) has no known value: start of print (set RenderOptions.start_of_print_mm), "
        "maximum label length (set RenderOptions.max_label_length_mm), feed rate (set RenderOptions.feed_rate); "
        "the printer keeps its own"
    ]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("model", "default"), [("dmx-600", DotSize(width=1, height=1)), ("allegro-2", DotSize(width=2, height=2))]
)
def test_a_model_s_default_dot_size_is_the_label_s_dot_size_type(model: str, default: DotSize) -> None:
    assert get_profile(model).default_dot_size == default
    assert type(get_profile(model).default_dot_size) is DotSize


@pytest.mark.os_agnostic
def test_a_placed_image_sends_its_magnification_the_same_across_and_along() -> None:
    dots = PlacedImage(name="LOGO", magnification=3).dots

    assert type(dots) is Magnification
    assert (dots.across, dots.along) == (3, 3)


@pytest.mark.os_agnostic
def test_a_qr_symbol_starts_its_quiet_zone_in_from_the_field_as_a_record_position() -> None:
    qr = Qr(x=0, y=0, data="X", module_dots=3, quiet_zone=4)
    ctx = job_context(Label(unit=Unit.DOTS, elements=[qr]), RenderOptions(model="dmx-600"))

    start = qr_symbol_position(qr, column=10, row=20, ctx=ctx)

    assert type(start) is RecordPosition
    # 4 modules of 3 dots are 4/100 in at 300 dpi, added to the field's row and column.
    assert (start.row, start.column) == (24, 14)


@pytest.mark.os_agnostic
def test_bounds_are_a_rect() -> None:
    bounds = Bitmap.from_rows(["....", ".#..", "..#."]).bounds()

    assert type(bounds) is Rect
    assert (bounds.left, bounds.top, bounds.width, bounds.height) == (1, 1, 2, 2)


@pytest.mark.os_agnostic
def test_a_field_part_is_a_rect_and_takes_its_spans_by_name() -> None:
    area = FieldArea(x=10, y=50, length=30, height=8, rotation=Rotation.DEG_90)

    part = area.part(along=Span(start=4, length=6), up=Span(start=2, length=3))

    assert type(part) is Rect
    assert part == Rect(left=12, top=54, width=3, height=6)


@pytest.mark.os_agnostic
def test_a_canvas_point_is_a_dot_point() -> None:
    canvas = canvas_for(
        Label(unit=Unit.DOTS, width=100, height=50, elements=[Text(x=1, y=1, text="x")]), get_profile("dmx-600")
    )

    point = canvas.point(10, 0)

    assert type(point) is DotPoint
    assert (point.x, point.y) == (canvas.geometry.dots(10, axis=Axis.ACROSS), canvas.height - 1)


@pytest.mark.os_agnostic
def test_the_widest_cell_comes_back_by_name() -> None:
    cell = widest_cell_mm(1)

    assert type(cell) is CellMm
    assert (round(cell.advance, 2), round(cell.height, 2)) == (1.14, 1.63)


@pytest.mark.os_agnostic
def test_batch_cuts_name_the_cuts_and_the_findings() -> None:
    cuts = batch_cuts(3, cut=Cut(every=2), options=RenderOptions(model="dmx-600"))

    assert type(cuts) is BatchCuts
    assert cuts.cut_after == (False, True, True)
    assert [f.code for f in cuts.findings] == [FindingCode.BATCH_CUT_UNVERIFIED]


@pytest.mark.os_agnostic
def test_batch_cuts_on_a_model_without_cut_now_cut_nothing_and_say_why() -> None:
    cuts = batch_cuts(2, cut=Cut(every=2), options=RenderOptions(model="prodigy"))

    assert cuts.cut_after == (False, False)
    assert [f.message for f in cuts.findings] == [
        "cutting every 2 labels of a batch needs the cut-now command: Prodigy does not have it"
    ]


@pytest.mark.os_agnostic
def test_calibration_labels_name_the_across_and_the_along_label() -> None:
    labels = calibration_labels(width=100, height=55, title="LAGER")

    assert type(labels) is CalibrationLabels
    assert (labels.across.height, labels.along.height) == (55.0, 205.0)
