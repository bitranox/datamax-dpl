"""Rendering labels to DPL: byte-exact records from the manual's examples, and the job framing."""

from __future__ import annotations

import pytest

from datamax_dpl.domain.enums import FillPattern, FindingCode, MediaSensor, Rotation, Unit
from datamax_dpl.domain.errors import LabelRejectedError
from datamax_dpl.domain.label import Barcode, Box, Circle, DotSize, Element, Label, Line, Point, Polygon, Text
from datamax_dpl.domain.render import build_job, render
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.symbology import Symbology

STX = b"\x02"


def records(label: Label, model: str = "dmx-600") -> list[bytes]:
    """The record lines (those starting with a rotation digit) of the rendered job."""
    data = build_job(label, RenderOptions(model=model)).data
    return [line for line in data.split(b"\r") if line[:1] in (b"1", b"2", b"3", b"4")]


def inch_label(*elements: Element) -> Label:
    return Label(unit=Unit.INCH, elements=list(elements))


@pytest.mark.os_agnostic
def test_text_record_matches_the_manuals_home_position_example() -> None:
    label = inch_label(Text(x=0.05, y=0, text="HOME POSITION", font=2))
    assert records(label) == [b"121100000000005HOME POSITION"]


@pytest.mark.os_agnostic
def test_box_and_lines_match_the_manuals_examples() -> None:
    label = inch_label(
        Box(x=0, y=0, width=4, height=4, thickness=0.03),
        Line(x=0, y=0.2, x2=4, y2=0.2, thickness=0.01),
        Line(x=2, y=0, x2=2, y2=4, thickness=0.01),
    )
    assert records(label) == [
        b"1X1100000000000B400400003003",
        b"1X1100000200000L400001",
        b"1X1100000000200L001400",
    ]


@pytest.mark.os_agnostic
def test_barcodes_match_the_manuals_examples() -> None:
    label = inch_label(
        Barcode(x=2.6, y=3.0, symbology=Symbology.CODE39, data="123456", height=0.4, wide_dots=3, narrow_dots=1),
        Barcode(
            x=1.4,
            y=2.5,
            rotation=Rotation.DEG_270,
            symbology=Symbology.CODE39,
            data="123456",
            height=1.0,
            wide_dots=6,
            narrow_dots=2,
            human_readable=False,
        ),
    )
    assert records(label) == [b"1A3104003000260123456", b"4a6210002500140123456"]


@pytest.mark.os_agnostic
def test_polygon_and_circle_match_the_manuals_examples() -> None:
    label = inch_label(
        Polygon(points=[Point(x=0.1, y=0.1), Point(x=0.25, y=0.4), Point(x=0.4, y=0.1)]),
        Circle(x=1.0, y=1.0, radius=0.25),
        Circle(x=1.0, y=1.0, radius=0.25, fill=FillPattern.RIGHT_DIAGONAL),
    )
    assert records(label) == [
        b"1X1100000100010P00100010040002500100040",
        b"1X1100001000100C00100010025",
        b"1X1100901000100C00100010025",
    ]


@pytest.mark.os_agnostic
def test_appendix_b_sample_program_renders_byte_for_byte() -> None:
    label = Label(
        unit=Unit.INCH,
        heat=7,
        dot_size=DotSize(width=1, height=1),
        elements=[
            Text(x=0.25, y=1.0, text="10K OHM 1/4 WATT", font=9, points=30),
            Barcode(
                x=0.5,
                y=0,
                symbology=Symbology.CODE39,
                data="590PCS",
                height=1.0,
                wide_dots=6,
                narrow_dots=2,
                human_readable=False,
            ),
        ],
    )
    result = render(label, RenderOptions(model="dmx-400"))
    assert result.data == (
        STX + b"L\rn\rD11\rH07\r191100801000025" + b"10K OHM 1/4 WATT\r1a6210000000050590PCS\rQ0001\rE\r"
    )


@pytest.mark.os_agnostic
def test_metric_label_uses_tenths_of_millimetres_where_the_model_has_metric_formats() -> None:
    result = build_job(Label(elements=[Text(x=10, y=5, text="A")]), RenderOptions(model="dmx-600"))
    assert b"\rm\r" in result.data
    assert b"121100000500100A" in result.data
    assert result.dpl_unit.value == "metric"


@pytest.mark.os_agnostic
def test_metric_label_is_converted_to_inches_where_the_model_has_no_metric_formats() -> None:
    result = build_job(Label(elements=[Text(x=25.4, y=12.7, text="A")]), RenderOptions(model="allegro"))
    assert result.data.startswith(STX + b"n" + STX + b"L\r")
    assert b"121100000500100A" in result.data
    assert FindingCode.INCH_UNITS in {f.code for f in result.findings}


@pytest.mark.os_agnostic
def test_quantity_and_cut_every_n_with_a_final_cut() -> None:
    label = Label.model_validate(
        {"quantity": 21, "cut": {"every": 5}, "elements": [{"type": "text", "x": 1, "y": 1, "text": "X"}]}
    )
    result = render(label, RenderOptions(model="dmx-600", cutter_installed=True))
    assert b"\r:0005\r" in result.data
    assert result.data.endswith(b"Q0021\rE\r" + STX + b"o")


@pytest.mark.os_agnostic
def test_cut_every_n_that_divides_the_quantity_needs_no_final_cut() -> None:
    label = Label.model_validate(
        {"quantity": 20, "cut": {"every": 5}, "elements": [{"type": "text", "x": 1, "y": 1, "text": "X"}]}
    )
    result = render(label, RenderOptions(model="dmx-600", cutter_installed=True))
    assert result.data.endswith(b"Q0020\rE\r")


@pytest.mark.os_agnostic
def test_a_label_that_cuts_is_refused_when_the_printer_has_no_cutter() -> None:
    label = Label.model_validate({"cut": {"every": 1}, "elements": [{"type": "text", "x": 1, "y": 1, "text": "X"}]})
    with pytest.raises(LabelRejectedError, match="without a cutter"):
        render(label, RenderOptions(model="dmx-600", cutter_installed=False))


@pytest.mark.os_agnostic
def test_present_distance_and_media_become_system_commands_in_inch_mode() -> None:
    label = Label(present_distance=25.4, media=MediaSensor.GAP, elements=[Text(x=1, y=1, text="X")])
    data = render(label, RenderOptions(model="pe42")).data
    assert data.startswith(STX + b"n" + STX + b"c0000" + STX + b"e" + STX + b"Kf0100" + STX + b"L\r")


@pytest.mark.os_agnostic
def test_rendering_is_deterministic() -> None:
    label = Label(elements=[Text(x=1, y=1, text="X"), Circle(x=20, y=20, radius=5)])
    assert (
        build_job(label, RenderOptions(model="allegro")).data == build_job(label, RenderOptions(model="allegro")).data
    )


@pytest.mark.os_agnostic
def test_counter_follows_its_field() -> None:
    label = Label.model_validate(
        {"elements": [{"type": "text", "x": 1, "y": 1, "text": "0001", "counter": {"step": 1}}]}
    )
    assert b"0001\r+001\r" in render(label, RenderOptions(model="dmx-600")).data


@pytest.mark.os_agnostic
def test_inverse_text_switches_the_attribute_and_back() -> None:
    label = Label.model_validate({"elements": [{"type": "text", "x": 1, "y": 1, "text": "INV", "attribute": 5}]})
    data = render(label, RenderOptions(model="dmx-600")).data
    assert b"\rA5\r1211" in data
    assert b"INV\rA1\r" in data


@pytest.mark.os_agnostic
def test_downloads_list_stored_and_rasterized_images_in_the_order_they_are_sent() -> None:
    label = Label.model_validate(
        {
            "elements": [
                {"type": "image", "x": 1, "y": 1, "name": "LOGO_1", "bitmap": ["#.", ".#"]},
                {"type": "circle", "x": 20, "y": 20, "radius": 5},
                {"type": "image", "x": 1, "y": 1, "name": "STORED"},
            ]
        }
    )
    result = build_job(label, RenderOptions(model="allegro"))
    assert result.downloads == ("LOGO_1", "DPLR0001")
    assert result.data.index(STX + b"IAAFLOGO_1\r") < result.data.index(STX + b"IAAFDPLR0001\r")
    assert result.data.index(STX + b"IAAFDPLR0001\r") < result.data.index(STX + b"L\r")
    assert STX + b"IAAFSTORED\r" not in result.data


# ======================== dot size ========================


def _format_lines(label: Label, model: str) -> list[bytes]:
    data = build_job(label, RenderOptions(model=model)).data
    start = data.index(STX + b"L")
    return data[start:].split(b"\r")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", ["allegro-2", "prodigy", "dmx-400", "dmx-430", "generic"])
def test_a_model_whose_default_dot_is_2x2_gets_d11_when_the_label_sets_none(model: str) -> None:
    """One label dot is one printhead dot: D11 overrides the D22 default (measured on an Allegro 2)."""
    lines = _format_lines(Label(elements=[Text(x=5, y=5, text="A")]), model)
    assert b"D11" in lines


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", ["dmx-600", "dmx-800"])
def test_a_model_whose_default_dot_is_already_1x1_gets_no_d_command(model: str) -> None:
    lines = _format_lines(Label(elements=[Text(x=5, y=5, text="A")]), model)
    assert not [line for line in lines if line.startswith(b"D") and len(line) == 3]


@pytest.mark.os_agnostic
def test_a_label_dot_size_wins_over_the_default() -> None:
    label = Label(dot_size=DotSize(width=2, height=2), elements=[Text(x=5, y=5, text="A")])
    lines = _format_lines(label, "allegro-2")
    assert b"D22" in lines
    assert b"D11" not in lines


# ======================== scale factors and axes ========================


def _records_with(label: Label, **options: object) -> list[bytes]:
    data = build_job(label, RenderOptions.model_validate({"model": "allegro", **options})).data
    return [line for line in data.split(b"\r") if line[:1] in (b"1", b"2", b"3", b"4")]


def _row_column(record: bytes) -> tuple[int, int]:
    return int(record[7:11]), int(record[11:15])


@pytest.mark.os_agnostic
def test_scale_along_moves_rows_and_stretches_vertical_lines_only() -> None:
    label = Label(elements=[Line(x=10, y=10, x2=10, y2=110, thickness=0.5)])
    (plain,) = _records_with(label)
    (along,) = _records_with(label, scale_along=0.8)
    assert _row_column(plain) == (39, 39)
    assert _row_column(along) == (49, 39)
    assert plain.endswith(b"L002394")
    assert along.endswith(b"L002492")


@pytest.mark.os_agnostic
def test_scale_across_moves_columns_and_stretches_horizontal_lines_only() -> None:
    label = Label(elements=[Line(x=10, y=10, x2=110, y2=10, thickness=0.5)])
    (across,) = _records_with(label, scale_across=0.8)
    assert _row_column(across) == (39, 49)
    assert across.endswith(b"L492002")


@pytest.mark.os_agnostic
def test_box_edge_follows_the_feed_and_box_side_the_web() -> None:
    label = Label(elements=[Box(x=0, y=0, width=50, height=20, thickness=1)])
    (plain,) = _records_with(label)
    (along,) = _records_with(label, scale_along=0.5)
    assert plain.endswith(b"B197079004004")
    assert along.endswith(b"B197157008004")


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("rotation", "height"), [(0, b"079"), (90, b"039"), (180, b"079"), (270, b"039")])
def test_barcode_height_follows_the_feed_unless_turned_a_quarter(rotation: int, height: bytes) -> None:
    label = Label.model_validate(
        {
            "elements": [
                {
                    "type": "barcode",
                    "x": 5,
                    "y": 5,
                    "symbology": "code128",
                    "data": "A1",
                    "height": 10,
                    "rotation": rotation,
                }
            ]
        }
    )
    (record,) = _records_with(label, scale_along=0.5)
    assert record[4:7] == height


@pytest.mark.os_agnostic
def test_a_rasterised_circle_gets_more_rows_when_the_feed_prints_short() -> None:
    label = Label.model_validate({"elements": [{"type": "circle", "x": 20, "y": 20, "radius": 5}]})
    plain = build_job(label, RenderOptions(model="allegro")).data
    along = build_job(label, RenderOptions(model="allegro", scale_along=0.5)).data
    assert along.count(b"\r80") == 2 * plain.count(b"\r80") - 1


@pytest.mark.os_agnostic
def test_a_stop_position_follows_the_feed_scale() -> None:
    label = Label(stop_position=25.4, elements=[Text(x=1, y=1, text="X")])
    plain = build_job(label, RenderOptions(model="allegro")).data
    along = build_job(label, RenderOptions(model="allegro", scale_along=0.5)).data
    assert STX + b"f100" in plain
    assert STX + b"f200" in along


@pytest.mark.os_agnostic
def test_the_allegro_2_prints_one_hundredth_of_an_inch_as_two_whole_dots() -> None:
    """Measured: 787/100 in printed 1574 dot rows, so 100 mm is written as 400, not 394."""
    label = Label(elements=[Line(x=0, y=0, x2=100, y2=0, thickness=0.25), Line(x=0, y=0, x2=0, y2=100, thickness=0.25)])
    across, along = [r for r in build_job(label, RenderOptions(model="allegro-2")).data.split(b"\r") if r[:1] == b"1"]
    assert across.endswith(b"L400001")
    assert along.endswith(b"L001400")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("scale", [0.49, 1.51])
def test_a_scale_outside_half_to_one_and_a_half_is_refused(scale: float) -> None:
    with pytest.raises(ValueError, match="scale_along"):
        RenderOptions(model="allegro", scale_along=scale)


@pytest.mark.os_agnostic
def test_the_print_width_warning_stays_nominal_on_a_printer_that_prints_short() -> None:
    label = Label(width=100, elements=[Text(x=1, y=1, text="X")])
    result = build_job(label, RenderOptions(model="allegro-2", scale_across=0.9))
    assert FindingCode.LABEL_WIDER_THAN_PRINT_WIDTH not in {f.code for f in result.findings}
