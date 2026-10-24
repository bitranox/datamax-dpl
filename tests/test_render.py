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
