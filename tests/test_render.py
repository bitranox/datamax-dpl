"""Rendering labels to DPL: byte-exact records from the manual's examples, and the job framing."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.domain.enums import FillPattern, FindingCode, ImageModule, MediaSensor, Rotation, Severity, Unit
from datamax_dpl.domain.errors import LabelRejectedError
from datamax_dpl.domain.label import Barcode, Box, Circle, DotSize, Element, Image, Label, Line, Point, Polygon, Text
from datamax_dpl.domain.render import RenderResult, build_job, effective_calibration, render
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.symbology import Symbology

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from datamax_dpl.domain.findings import Finding

STX = b"\x02"


def image_placements(data: bytes) -> list[bytes]:
    """The image placing records (``1Y...``) of a job."""
    return [line for line in data.split(b"\r") if line.startswith(b"1Y")]


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
    logo, drawn = result.downloads
    assert logo == "LOGO_1"
    circle = STX + b"IAAF" + drawn.encode("ascii") + b"\r"
    assert result.data.index(STX + b"IAAFLOGO_1\r") < result.data.index(circle)
    assert result.data.index(circle) < result.data.index(STX + b"L\r")
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
def test_offsets_move_every_field_without_changing_its_size() -> None:
    label = Label(elements=[Line(x=10, y=10, x2=110, y2=10, thickness=0.5)])
    (moved,) = _records_with(label, offset_across=2.54, offset_along=-2.54)
    # 12.54 mm across is 49 units, 7.46 mm along is 29; the 100 mm line stays 394 units long
    assert _row_column(moved) == (29, 49)
    assert moved.endswith(b"L394002")


@pytest.mark.os_agnostic
def test_an_offset_is_in_millimetres_whatever_unit_the_label_uses() -> None:
    label = Label(unit=Unit.INCH, elements=[Line(x=1, y=1, x2=2, y2=1, thickness=0.02)])
    (moved,) = _records_with(label, offset_across=2.54)
    assert _row_column(moved) == (100, 110)


@pytest.mark.os_agnostic
def test_a_rasterised_circle_moves_with_the_offset() -> None:
    label = Label.model_validate({"elements": [{"type": "circle", "x": 20, "y": 20, "radius": 5}]})
    (plain,) = _records_with(label)
    (moved,) = _records_with(label, offset_across=2.54, offset_along=2.54)
    assert _row_column(moved) == (_row_column(plain)[0] + 10, _row_column(plain)[1] + 10)


@pytest.mark.os_agnostic
def test_a_field_the_offset_pushes_past_the_label_edge_is_refused() -> None:
    label = Label(elements=[Text(x=5, y=0.5, text="EDGE")])
    result = build_job(label, RenderOptions(model="allegro", offset_along=-1))
    (error,) = result.errors
    assert error.code is FindingCode.OFFSET_PAST_LABEL_EDGE
    assert "0.5 mm past the label edge" in error.message


@pytest.mark.os_agnostic
@pytest.mark.parametrize("offset", [-10.01, 10.01])
def test_an_offset_over_ten_millimetres_is_refused(offset: float) -> None:
    with pytest.raises(ValueError, match=r"(less|greater) than or equal to -?10"):
        RenderOptions(offset_along=offset)


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
    nominal = RenderOptions(model="allegro-2", scale_along=1.0, offset_along=0.0)
    across, along = [r for r in build_job(label, nominal).data.split(b"\r") if r[:1] == b"1"]
    assert across.endswith(b"L400001")
    assert along.endswith(b"L001400")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("scale", [0.49, 1.51])
def test_a_scale_outside_half_to_one_and_a_half_is_refused(scale: float) -> None:
    with pytest.raises(ValueError, match=r"scale_along\n\s+Input should be (greater|less) than or equal to"):
        RenderOptions(model="allegro", scale_along=scale)


@pytest.mark.os_agnostic
def test_the_print_width_warning_stays_nominal_on_a_printer_that_prints_short() -> None:
    label = Label(width=100, elements=[Text(x=1, y=1, text="X")])
    result = build_job(label, RenderOptions(model="allegro-2", scale_across=0.9))
    assert FindingCode.LABEL_WIDER_THAN_PRINT_WIDTH not in {f.code for f in result.findings}


# ======================== calibration defaults of the model ========================


def _vertical_line_record(options: RenderOptions) -> bytes:
    """The record of a 50 mm line along the feed, rendered with ``options``."""
    label = Label(elements=[Line(x=10, y=10, x2=10, y2=60, thickness=0.5)])
    (record,) = [r for r in build_job(label, options).data.split(b"\r") if r[:1] == b"1"]
    return record


def _model_defaults_findings(options: RenderOptions) -> list[Finding]:
    result = build_job(Label(elements=[Text(x=5, y=5, text="X")]), options)
    return [f for f in result.findings if f.code is FindingCode.MODEL_DEFAULTS_APPLIED]


@pytest.mark.os_agnostic
def test_an_allegro_2_without_options_prints_along_the_feed_with_its_measured_scale() -> None:
    defaulted = _vertical_line_record(RenderOptions(model="allegro-2"))
    nominal = _vertical_line_record(RenderOptions(model="allegro-2", scale_along=1.0))
    assert _row_column(defaulted)[0] != _row_column(nominal)[0]
    assert defaulted[-6:] != nominal[-6:]


@pytest.mark.os_agnostic
def test_an_allegro_2_without_options_reports_the_model_defaults_it_applied() -> None:
    (finding,) = _model_defaults_findings(RenderOptions(model="allegro-2"))
    assert finding.severity is Severity.INFO
    assert finding.message.startswith("model defaults (measured on one Allegro 2")
    assert "scale_along 0.976" in finding.message
    assert "offset_along -0.25 mm" in finding.message
    assert "stop_position 67.4 mm" in finding.message
    assert "scale_across" not in finding.message
    assert "offset_across" not in finding.message


@pytest.mark.os_agnostic
def test_an_explicit_option_wins_over_the_model_default_and_is_not_reported() -> None:
    explicit = RenderOptions(model="allegro-2", scale_along=1.0, stop_position_mm=40.0)
    (finding,) = _model_defaults_findings(explicit)
    assert "scale_along" not in finding.message
    assert "stop_position" not in finding.message
    assert "offset_along -0.25 mm" in finding.message
    assert _vertical_line_record(explicit) == _vertical_line_record(
        RenderOptions(model="allegro-2", scale_along=1.0, offset_along=-0.25)
    )


@pytest.mark.os_agnostic
def test_explicit_options_for_every_model_default_leave_nothing_to_report() -> None:
    options = RenderOptions(model="allegro-2", scale_along=0.976, offset_along=-0.25, stop_position_mm=67.4)
    assert _model_defaults_findings(options) == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", ["dmx-600", "allegro", "generic", "permissive"])
def test_a_model_without_measured_defaults_reports_none(model: str) -> None:
    assert _model_defaults_findings(RenderOptions(model=model)) == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize("field", ["stop_position_mm", "cutter_stop_position_mm"])
def test_a_stop_position_option_of_zero_or_less_is_refused(field: str) -> None:
    with pytest.raises(ValueError, match=rf"{field}\n\s+Input should be greater than 0"):
        RenderOptions.model_validate({"model": "allegro-2", field: 0})


def _edge_line_result(**options: object) -> RenderResult:
    """A horizontal line on the label's bottom edge, rendered on the Allegro 2."""
    label = Label(elements=[Line(x=5, y=0, x2=45, y2=0, thickness=0.5)])
    return build_job(label, RenderOptions.model_validate({"model": "allegro-2", **options}))


def _clamped(result: RenderResult) -> list[Finding]:
    return [f for f in result.findings if f.code is FindingCode.MODEL_OFFSET_CLAMPED]


@pytest.mark.os_agnostic
def test_a_field_the_models_default_offset_pushes_past_the_edge_is_placed_on_the_edge() -> None:
    result = _edge_line_result()
    (clamped,) = _clamped(result)
    assert clamped.severity is Severity.WARNING
    assert clamped.element == 0
    assert clamped.fallback is not None
    assert "-0.25 mm" in clamped.fallback
    assert "offset_along = 0" in clamped.fallback
    assert result.errors == ()
    (record,) = [r for r in result.data.split(b"\r") if r[:1] == b"1"]
    assert _row_column(record)[0] == 0


@pytest.mark.os_agnostic
def test_under_strict_the_models_default_offset_past_the_edge_is_refused() -> None:
    result = _edge_line_result(policy="strict")
    assert [f.code for f in result.errors] == [FindingCode.MODEL_OFFSET_CLAMPED]


@pytest.mark.os_agnostic
def test_a_user_set_offset_past_the_edge_is_still_refused() -> None:
    result = _edge_line_result(offset_along=-0.25)
    assert [f.code for f in result.errors] == [FindingCode.OFFSET_PAST_LABEL_EDGE]
    assert _clamped(result) == []


@pytest.mark.os_agnostic
def test_a_field_clear_of_the_edge_needs_no_clamp_under_the_models_default_offset() -> None:
    label = Label(elements=[Line(x=5, y=10, x2=45, y2=10, thickness=0.5)])
    result = build_job(label, RenderOptions(model="allegro-2"))
    assert _clamped(result) == []
    assert result.errors == ()


# ======================== stop position from the options and the model ========================


def _stop_job(label_fields: dict[str, object] | None = None, **options: object) -> RenderResult:
    """A one-text label with ``label_fields``, rendered with ``options`` (default model allegro-2)."""
    label = Label.model_validate({"elements": [{"type": "text", "x": 5, "y": 5, "text": "X"}], **(label_fields or {})})
    return build_job(label, RenderOptions.model_validate({"model": "allegro-2", **options}))


def _stop_commands(result: RenderResult) -> list[bytes]:
    return re.findall(rb"\x02f[0-9]{3}", result.data)


def _names_the_model_stop(result: RenderResult) -> bool:
    return any("stop_position" in f.message for f in result.findings if f.code is FindingCode.MODEL_DEFAULTS_APPLIED)


@pytest.mark.os_agnostic
def test_an_allegro_2_without_options_stops_where_the_user_measured_it() -> None:
    """67.4 mm is what ``<STX>f265`` stopped at on the printer, so no scale or offset may move it."""
    result = _stop_job()
    assert result.data.startswith(STX + b"n" + STX + b"f265" + STX + b"L\r")
    assert _names_the_model_stop(result)


@pytest.mark.os_agnostic
def test_a_stop_position_option_is_sent_as_configured_without_the_printers_scale() -> None:
    assert _stop_commands(_stop_job(stop_position_mm=40)) == [STX + b"f157"]


@pytest.mark.os_agnostic
def test_the_labels_own_stop_position_wins_over_the_model_default() -> None:
    result = _stop_job({"stop_position": 60})
    assert _stop_commands(result) == [STX + b"f246"]
    assert not _names_the_model_stop(result)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("tear_off", [{}, {"stop_position_mm": 40}])
def test_a_job_that_cuts_sends_no_stop_position_without_a_cutter_stop_position(tear_off: dict[str, object]) -> None:
    result = _stop_job({"cut": {"every": 1}}, cutter_installed=True, **tear_off)
    assert _stop_commands(result) == []
    assert result.errors == ()
    assert not _names_the_model_stop(result)


@pytest.mark.os_agnostic
def test_a_job_that_cuts_sends_the_cutter_stop_position() -> None:
    result = _stop_job({"cut": {"every": 1}}, cutter_installed=True, cutter_stop_position_mm=80)
    assert _stop_commands(result) == [STX + b"f315"]


@pytest.mark.os_agnostic
def test_a_job_that_does_not_cut_ignores_the_cutter_stop_position() -> None:
    assert _stop_commands(_stop_job(cutter_stop_position_mm=80)) == [STX + b"f265"]


@pytest.mark.os_agnostic
def test_a_model_without_a_measured_stop_position_sends_none() -> None:
    assert _stop_commands(_stop_job(model="dmx-600")) == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("label_fields", "options"),
    [
        ({}, {"stop_position_mm": 40}),
        ({"cut": {"every": 1}}, {"cutter_installed": True, "cutter_stop_position_mm": 80}),
    ],
)
def test_a_configured_stop_position_on_a_model_without_one_is_reported_and_still_prints_under_strict(
    label_fields: dict[str, object], options: dict[str, object]
) -> None:
    """A stop position from the config applies to every job, so a model without one is no reason to refuse."""
    result = _stop_job(label_fields, model="prodigy", policy="strict", **options)
    assert _stop_commands(result) == []
    assert result.errors == ()
    assert result.warnings == ()
    (info,) = [f for f in result.findings if f.code is FindingCode.CONFIGURED_STOP_POSITION_UNSUPPORTED]
    assert info.severity is Severity.INFO
    assert "not sent" in info.message
    assert "no stop-position command" in info.message


@pytest.mark.os_agnostic
@pytest.mark.parametrize("policy", ["degrade", "strict"])
def test_the_labels_own_stop_position_on_a_model_without_one_is_a_deviation(policy: str) -> None:
    result = _stop_job({"stop_position": 60}, model="prodigy", policy=policy)
    assert _stop_commands(result) == []
    (deviation,) = [f for f in result.findings if f.code is FindingCode.STOP_POSITION_UNSUPPORTED]
    assert deviation.fallback == "labels stop at the printer's default position"
    assert deviation.severity is (Severity.ERROR if policy == "strict" else Severity.WARNING)
    assert FindingCode.CONFIGURED_STOP_POSITION_UNSUPPORTED not in {f.code for f in result.findings}


@pytest.mark.os_agnostic
def test_the_labels_own_stop_position_wins_over_the_cutter_stop_position() -> None:
    result = _stop_job({"stop_position": 60, "cut": {"every": 1}}, cutter_installed=True, cutter_stop_position_mm=80)
    assert _stop_commands(result) == [STX + b"f246"]


@pytest.mark.os_agnostic
def test_a_label_with_both_stop_position_and_present_distance_sends_both_as_stop_commands() -> None:
    """The cell D12 leaves as it was: the label's stop, then its present distance sent as a stop."""
    result = _stop_job({"stop_position": 60, "present_distance": 30})
    assert _stop_commands(result) == [STX + b"f246", STX + b"f123"]


@pytest.mark.os_agnostic
def test_a_stop_position_that_rounds_to_zero_is_sent_as_f000() -> None:
    """The manual (``<STX>f``, page 22) takes any three-digit distance and names no minimum.

    It adds only that the distance "must be greater than the start of print position to have any
    effect", and that position is set on the printer, so no bound here could tell an effective
    value from an ineffective one.
    """
    assert _stop_commands(_stop_job(stop_position_mm=0.1)) == [STX + b"f000"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("label_fields", "options"),
    [
        ({}, {"stop_position_mm": 254}),
        ({"cut": {"every": 1}}, {"cutter_installed": True, "cutter_stop_position_mm": 254}),
    ],
)
def test_a_configured_stop_position_past_the_command_limit_is_refused(
    label_fields: dict[str, object], options: dict[str, object]
) -> None:
    result = _stop_job(label_fields, **options)
    assert [f.code for f in result.errors] == [FindingCode.STOP_POSITION_TOO_LARGE]
    assert _stop_commands(result) == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("label_fields", "options"),
    [
        ({"present_distance": 30}, {}),
        ({"present_distance": 30}, {"stop_position_mm": 40}),
        ({"present_distance": 30, "cut": {"every": 1}}, {"cutter_installed": True, "cutter_stop_position_mm": 80}),
    ],
)
def test_a_present_distance_sent_as_the_stop_position_is_the_labels_own_stop(
    label_fields: dict[str, object], options: dict[str, object]
) -> None:
    """The Allegro 2 has no present-distance command, so the label's present distance is its stop."""
    result = _stop_job(label_fields, **options)
    assert _stop_commands(result) == [STX + b"f123"]
    assert FindingCode.PRESENT_DISTANCE_UNSUPPORTED in {f.code for f in result.findings}
    assert not _names_the_model_stop(result)


@pytest.mark.os_agnostic
def test_a_model_with_a_present_distance_command_sends_it_beside_the_configured_stop() -> None:
    result = _stop_job({"present_distance": 30}, model="pe42", stop_position_mm=40)
    assert result.data.startswith(STX + b"n" + STX + b"f157" + STX + b"Kf0118" + STX + b"L\r")
    assert result.findings == ()


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("label_fields", "options", "stop"),
    [
        ({"stop_position": 60}, {}, b"f236"),
        ({"cut": {"every": 1}}, {"cutter_installed": True, "cutter_stop_position_mm": 80}, b"f315"),
    ],
    ids=["label-stop", "cutter-stop"],
)
def test_a_present_distance_command_goes_out_beside_whichever_stop_the_job_sends(
    label_fields: dict[str, object], options: dict[str, object], stop: bytes
) -> None:
    """<STX>Kf is added to the stop, never chosen instead of it: the label's own and the cutter's too."""
    result = _stop_job({"present_distance": 30, **label_fields}, model="pe42", **options)
    assert result.data.startswith(STX + b"n" + STX + stop + STX + b"Kf0118" + STX + b"L\r")
    assert result.findings == ()


@pytest.mark.os_agnostic
def test_a_present_distance_on_a_model_with_neither_command_is_ignored() -> None:
    """The Prodigy has no ``<STX>Kf`` and no ``<STX>f``, so there is nothing to send it as."""
    result = _stop_job({"present_distance": 30}, model="prodigy")
    assert _stop_commands(result) == []
    assert STX + b"Kf" not in result.data
    (deviation,) = [f for f in result.findings if f.code is FindingCode.PRESENT_DISTANCE_UNSUPPORTED]
    assert (deviation.fallback, deviation.severity) == ("ignored", Severity.WARNING)


def _present_commands(result: RenderResult) -> list[bytes]:
    return re.findall(rb"\x02Kf[0-9]+", result.data)


def _present_job(model: str, unit: str, distance: float) -> RenderResult:
    """A one-text label in ``unit`` whose text sits near the origin, so the unit alone moves nothing else."""
    text = {"type": "text", "x": 0.1, "y": 0.1, "text": "X"}
    return build_job(
        Label.model_validate({"unit": unit, "present_distance": distance, "elements": [text]}),
        RenderOptions(model=model),
    )


# System commands go out in inch mode (<STX>n), so <STX>Kf always carries hundredths of an inch:
# 99.99 in and 2539.746 mm are 9999, the largest four digits; 100 in and 2540 mm are 10000.
@pytest.mark.os_agnostic
@pytest.mark.parametrize(("unit", "distance"), [("in", 99.99), ("mm", 2539.746)])
@pytest.mark.parametrize("model", ["pe42", "ovation", "permissive"])
def test_the_largest_four_digit_present_distance_is_sent(model: str, unit: str, distance: float) -> None:
    result = _present_job(model, unit, distance)
    assert _present_commands(result) == [STX + b"Kf9999"]
    assert result.errors == ()


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("unit", "distance"), [("in", 100.0), ("mm", 2540.0)])
@pytest.mark.parametrize("model", ["pe42", "ovation", "permissive"])
def test_a_present_distance_past_four_digits_is_refused(model: str, unit: str, distance: float) -> None:
    """The manual (``<STX>Kf``, page 53) takes a four-digit present distance."""
    result = _present_job(model, unit, distance)
    assert _present_commands(result) == []
    (error,) = result.errors
    assert error.code is FindingCode.PRESENT_DISTANCE_TOO_LARGE
    assert error.message == "present distance takes at most 9999/100 in"


# The bound applies to the rounded hundredths, not to the millimetres: 2539.87 mm is 9999.49/100 in
# and still rounds to 9999, while 2539.88 mm (9999.53) rounds to 10000.
@pytest.mark.os_agnostic
def test_a_present_distance_that_rounds_to_four_digits_is_sent() -> None:
    result = _present_job("pe42", "mm", 2539.87)
    assert _present_commands(result) == [STX + b"Kf9999"]
    assert result.errors == ()


@pytest.mark.os_agnostic
def test_a_present_distance_that_rounds_past_four_digits_is_refused() -> None:
    result = _present_job("pe42", "mm", 2539.88)
    assert _present_commands(result) == []
    assert [error.code for error in result.errors] == [FindingCode.PRESENT_DISTANCE_TOO_LARGE]


# ======================== the effective calibration, shared with the CLI ========================


@pytest.mark.os_agnostic
def test_the_effective_calibration_takes_the_models_measured_default_where_nothing_is_set() -> None:
    calibration = effective_calibration(RenderOptions(model="allegro-2"))
    assert (calibration.scale_across, calibration.scale_along) == (1.0, 0.976)
    assert (calibration.offset_across, calibration.offset_along) == (0.0, -0.25)
    assert calibration.stop_position_mm == 67.4


@pytest.mark.os_agnostic
def test_the_effective_calibration_keeps_every_value_the_options_set_even_a_neutral_one() -> None:
    options = RenderOptions(model="allegro-2", scale_along=1.0, offset_along=0.0, stop_position_mm=60)
    calibration = effective_calibration(options)
    assert (calibration.scale_along, calibration.offset_along, calibration.stop_position_mm) == (1.0, 0.0, 60)


@pytest.mark.os_agnostic
def test_the_effective_calibration_of_a_model_without_measurements_is_neutral() -> None:
    calibration = effective_calibration(RenderOptions(model="dmx-600"))
    assert (calibration.scale_across, calibration.scale_along, calibration.offset_along) == (1.0, 1.0, 0.0)
    assert calibration.stop_position_mm is None


# ======================== images wider than one image row ========================

_ROW_LIMIT_PHRASE = "one image row holds at most 2040 dots"


@pytest.mark.os_agnostic
def test_a_raster_fallback_wider_than_an_image_row_is_refused_not_raised() -> None:
    """A patterned box 20 in wide rasterises to 4061 dots on an Allegro 2; one row holds 2040."""
    label = Label.model_validate(
        {
            "unit": "in",
            "elements": [{"type": "box", "x": 1, "y": 1, "width": 20, "height": 1, "thickness": 0.5, "fill": 4}],
        }
    )
    result = build_job(label, RenderOptions(model="allegro-2"))
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_TOO_WIDE]
    assert (finding.severity, finding.element) == (Severity.ERROR, 0)
    assert result.downloads == ()
    with pytest.raises(LabelRejectedError, match=_ROW_LIMIT_PHRASE):
        render(label, RenderOptions(model="allegro-2"))


@pytest.mark.os_agnostic
def test_a_user_image_wider_than_an_image_row_is_refused_not_raised() -> None:
    label = Label.model_validate(
        {
            "elements": [
                {"type": "text", "x": 1, "y": 1, "text": "x"},
                {"type": "image", "x": 0, "y": 0, "name": "WIDE", "bitmap": ["#" * 2041]},
            ]
        }
    )
    result = build_job(label, RenderOptions(model="permissive"))
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_TOO_WIDE]
    assert (finding.severity, finding.element) == (Severity.ERROR, 1)
    assert result.downloads == ()
    assert build_job(
        label.model_copy(update={"elements": [label.elements[1].model_copy(update={"bitmap": ["#" * 2040]})]}),
        RenderOptions(model="permissive"),
    ).ok


@pytest.mark.os_agnostic
def test_a_refused_image_places_nothing_and_claims_no_fallback() -> None:
    """A refused download leaves no placing record behind and no "drawn as downloaded image"."""
    label = Label.model_validate(
        {
            "unit": "in",
            "elements": [
                {"type": "box", "x": 1, "y": 1, "width": 20, "height": 1, "thickness": 0.5, "fill": 4},
                {"type": "image", "x": 0, "y": 0, "name": "WIDE", "bitmap": ["#" * 2041]},
            ],
        }
    )
    result = build_job(label, RenderOptions(model="allegro-2"))
    assert not image_placements(result.data)
    assert result.downloads == ()
    assert FindingCode.BOX_RASTERIZED not in {f.code for f in result.findings}
    too_wide = [f.element for f in result.findings if f.code is FindingCode.IMAGE_TOO_WIDE]
    assert too_wide == [0, 1]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("box_dots", "fits"), [(2039, True), (2040, False)])
def test_a_raster_fallback_of_exactly_2040_dots_fits_and_2041_is_refused(box_dots: int, fits: bool) -> None:
    """A rasterised box is its width plus one dot wide: 2039 dots -> 2040, 2040 dots -> 2041."""
    label = Label.model_validate(
        {
            "unit": "dots",
            "elements": [{"type": "box", "x": 0, "y": 5, "width": box_dots, "height": 10, "thickness": 2, "fill": 4}],
        }
    )
    result = build_job(label, RenderOptions(model="allegro-2"))
    codes = {f.code for f in result.findings}
    assert (FindingCode.IMAGE_TOO_WIDE in codes) is not fits
    assert (FindingCode.BOX_RASTERIZED in codes) is fits
    # 2040 dots also pass the Allegro 2's 832-dot print width; only an image that is placed is checked.
    assert (FindingCode.IMAGE_PAST_PRINT_WIDTH in codes) is fits
    assert len(result.downloads) == (1 if fits else 0)
    assert len(image_placements(result.data)) == (1 if fits else 0)


_DRAWN_NAME = re.compile(r"D[0-9A-F]{7}")


def _too_wide_message(elements: Sequence[Mapping[str, object]]) -> str:
    """The ``image_too_wide`` message an Allegro 2 gives the one over-wide image, element 3."""
    result = build_job(Label.model_validate({"unit": "dots", "elements": elements}), RenderOptions(model="allegro-2"))
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_TOO_WIDE]
    assert finding.element == 3
    return finding.message


@pytest.mark.os_agnostic
def test_an_over_wide_drawn_image_names_its_element_and_the_name_it_would_be_queued_under() -> None:
    """A polygon 2100 dots wide on an Allegro 2 (no polygon record) is drawn 2100 dots wide.

    With its content name taken by a label image, the job would queue it under the next salted
    name, so that is the name the message gives, not the unsalted content hash.
    """
    polygon = {"type": "polygon", "points": [{"x": 0, "y": 5}, {"x": 2099, "y": 5}, {"x": 0, "y": 40}]}
    text = {"type": "text", "x": 1, "y": 1, "text": "x"}
    free = _too_wide_message([text, text, text, polygon])
    [content_name] = _DRAWN_NAME.findall(free)
    assert free == f"element 3 (polygon) drawn as image {content_name} is 2100 dots wide; {_ROW_LIMIT_PHRASE}"
    taken = _too_wide_message([{"type": "image", "x": 0, "y": 0, "name": content_name}, text, text, polygon])
    [salted_name] = _DRAWN_NAME.findall(taken)
    assert salted_name != content_name
    assert taken == f"element 3 (polygon) drawn as image {salted_name} is 2100 dots wide; {_ROW_LIMIT_PHRASE}"


@pytest.mark.os_agnostic
def test_an_over_wide_user_image_names_the_image_and_its_element() -> None:
    wide = {"type": "image", "x": 0, "y": 0, "name": "WIDE", "bitmap": ["#" * 2041]}
    result = build_job(Label.model_validate({"elements": [wide]}), RenderOptions(model="permissive"))
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_TOO_WIDE]
    assert finding.message == f"element 0 (image WIDE) is 2041 dots wide; {_ROW_LIMIT_PHRASE}"


# ======================== images past the print width ========================

_PAST_PRINT_WIDTH_PHRASE = "passes the 832-dot print width of Allegro 2"


def _image_label(x: float, *, width: int, unit: str = "mm", magnification: int = 1, rows: int = 2) -> Label:
    return Label.model_validate(
        {
            "unit": unit,
            "elements": [
                {
                    "type": "image",
                    "x": x,
                    "y": 5,
                    "name": "WIDE",
                    "magnification": magnification,
                    "bitmap": ["#" * width] * rows,
                }
            ],
        }
    )


def _past_width(result: RenderResult) -> list[Finding]:
    return [f for f in result.findings if f.code is FindingCode.IMAGE_PAST_PRINT_WIDTH]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("policy", ["degrade", "strict"])
def test_an_image_ending_past_the_print_width_is_refused(policy: str) -> None:
    """Allegro 2: 4.10 in at 203 dpi is 832 dots; x = 10 mm is column 40, dot 80, so 800 dots end at dot 880."""
    options = RenderOptions.model_validate({"model": "allegro-2", "policy": policy})
    result = build_job(_image_label(10, width=800), options)
    [finding] = _past_width(result)
    assert (finding.severity, finding.element) == (Severity.ERROR, 0)
    assert "ends at dot 880" in finding.message
    assert FindingCode.OUTSIDE_PRINT_WIDTH not in {f.code for f in result.findings}
    with pytest.raises(LabelRejectedError, match=_PAST_PRINT_WIDTH_PHRASE):
        render(_image_label(10, width=800), options)


@pytest.mark.os_agnostic
def test_the_same_image_at_the_left_edge_renders() -> None:
    result = build_job(_image_label(0, width=800), RenderOptions(model="allegro-2"))
    assert result.ok, result.findings
    assert _past_width(result) == []


@pytest.mark.os_agnostic
def test_an_image_is_measured_at_its_magnification() -> None:
    """400 dots at magnification 2 print 800 dots: inside at x = 0, past at x = 10 mm."""
    assert build_job(_image_label(0, width=400, magnification=2), RenderOptions(model="allegro-2")).ok
    past = build_job(_image_label(10, width=400, magnification=2), RenderOptions(model="allegro-2"))
    assert [f.element for f in _past_width(past)] == [0]


@pytest.mark.os_agnostic
def test_a_halved_image_whose_doubled_magnification_passes_the_edge_is_refused() -> None:
    """271 dots at magnification 3 from dot 18 end at dot 831; halved, 136 dots at 6 end at dot 834."""
    label = _image_label(18, width=271, unit="dots", magnification=3, rows=10)
    whole = build_job(label, RenderOptions(model="allegro-2"))
    assert whole.ok, whole.findings
    halved = build_job(label, RenderOptions(model="allegro-2", image_memory_bytes=100))
    assert halved.halved_elements == (0,)
    [finding] = _past_width(halved)
    assert finding.element == 0
    assert "ends at dot 834" in finding.message


@pytest.mark.os_agnostic
def test_an_image_starting_past_the_last_column_gets_one_finding_not_two() -> None:
    result = build_job(_image_label(105, width=8), RenderOptions(model="allegro-2"))
    assert FindingCode.OUTSIDE_PRINT_WIDTH in {f.code for f in result.findings}
    assert _past_width(result) == []


@pytest.mark.os_agnostic
def test_a_stored_image_of_unknown_size_is_not_checked() -> None:
    label = Label.model_validate({"elements": [{"type": "image", "x": 100, "y": 5, "name": "LOGO"}]})
    result = build_job(label, RenderOptions(model="allegro-2"))
    assert result.ok, result.findings
    assert _past_width(result) == []


def _polygon_label(left: float, right: float) -> Label:
    points = [{"x": left, "y": 5}, {"x": right, "y": 5}, {"x": left, "y": 15}]
    return Label.model_validate({"elements": [{"type": "polygon", "points": points, "fill": 1}]})


@pytest.mark.os_agnostic
def test_a_rasterized_polygon_inside_the_print_width_is_untouched() -> None:
    result = build_job(_polygon_label(10, 50), RenderOptions(model="allegro-2"))
    assert result.ok, result.findings
    assert FindingCode.POLYGON_RASTERIZED in {f.code for f in result.findings}
    assert _past_width(result) == []


@pytest.mark.os_agnostic
def test_a_rasterized_polygon_past_the_print_width_is_refused() -> None:
    """D31: a raster fallback is a drawn image like any other; 90 mm to 110 mm ends past 104 mm."""
    result = build_job(_polygon_label(90, 110), RenderOptions(model="allegro-2"))
    assert [f.element for f in _past_width(result)] == [0]
    assert not result.ok


# ======================== the memory module images download to ========================

_EIGHT_BY_EIGHT = ["#......#"] * 8


def label_with_image() -> Label:
    return Label(elements=[Image(x=1, y=1, name="LOGO", bitmap=_EIGHT_BY_EIGHT)])


def text_only_label() -> Label:
    return Label(elements=[Text(x=5, y=5, text="Hi")])


def module_findings(result: RenderResult) -> list[Finding]:
    return [f for f in result.findings if f.code is FindingCode.IMAGE_MODULE_UNKNOWN]


@pytest.mark.os_agnostic
def test_an_allegro_2_job_downloads_its_image_to_module_a() -> None:
    job = build_job(label_with_image(), RenderOptions(model="allegro-2")).data
    assert b"\x02IAAFLOGO\r" in job


@pytest.mark.os_agnostic
def test_a_dmx_600_job_downloads_its_image_to_module_e() -> None:
    job = build_job(label_with_image(), RenderOptions(model="dmx-600")).data
    assert b"\x02IEAFLOGO\r" in job
    assert b"\x02IAAF" not in job


@pytest.mark.os_agnostic
def test_the_configured_module_wins_over_the_model() -> None:
    job = build_job(label_with_image(), RenderOptions(model="dmx-600", image_module=ImageModule.C)).data
    assert b"\x02ICAFLOGO\r" in job


@pytest.mark.os_agnostic
def test_a_drawn_image_downloads_to_the_models_module_too() -> None:
    circle = Label(elements=[Circle(x=20, y=20, radius=5)])
    result = build_job(circle, RenderOptions(model="dmx-400"))
    [name] = result.downloads
    assert b"\x02IAAF" + name.encode("ascii") + b"\r" in result.data
    configured = build_job(circle, RenderOptions(model="dmx-400", image_module=ImageModule.B))
    assert b"\x02IBAF" + name.encode("ascii") + b"\r" in configured.data


@pytest.mark.os_agnostic
def test_a_prodigy_without_a_module_refuses_a_job_with_images_and_names_the_setting() -> None:
    result = build_job(label_with_image(), RenderOptions(model="prodigy"))
    assert not result.ok
    assert any(f.code is FindingCode.IMAGE_MODULE_UNKNOWN and "image_module" in f.message for f in result.findings)


@pytest.mark.os_agnostic
def test_a_prodigy_label_without_images_still_renders() -> None:
    assert build_job(text_only_label(), RenderOptions(model="prodigy")).ok


@pytest.mark.os_agnostic
def test_a_stored_only_image_on_a_prodigy_downloads_nothing_and_is_not_refused() -> None:
    stored = Label(elements=[Image(x=1, y=1, name="LOGO")])
    result = build_job(stored, RenderOptions(model="prodigy"))
    assert result.ok, [f.message for f in result.errors]
    assert module_findings(result) == []


#: Every way a label downloads an image: its own bitmap, a QR Code, a raster fallback (a circle
#: on a model without circles) and overlapping fields drawn together for transparency.
_DOWNLOADING_LABELS: dict[str, dict[str, object]] = {
    "image": {"elements": [{"type": "image", "x": 1, "y": 1, "name": "LOGO", "bitmap": _EIGHT_BY_EIGHT}]},
    "qr": {"elements": [{"type": "qr", "x": 5, "y": 5, "data": "HELLO"}]},
    "raster fallback": {"elements": [{"type": "circle", "x": 20, "y": 20, "radius": 5}]},
    "emulated group": {
        "attribute": 2,
        "elements": [
            {"type": "box", "x": 10, "y": 10, "width": 30, "height": 20, "thickness": 1},
            {"type": "line", "x": 5, "y": 20, "x2": 50, "y2": 20, "thickness": 1},
        ],
    },
}


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", ["prodigy", "prodigy-152", "prodigy-plus", "prodigy-plus-152"])
@pytest.mark.parametrize("kind", sorted(_DOWNLOADING_LABELS))
def test_every_kind_of_download_is_refused_on_a_model_without_a_module(model: str, kind: str) -> None:
    label = Label.model_validate(_DOWNLOADING_LABELS[kind])
    result = build_job(label, RenderOptions(model=model))
    assert [f.severity for f in module_findings(result)] == [Severity.ERROR]
    assert result.downloads == ()
    assert STX + b"I" not in result.data


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", ["prodigy", "prodigy-plus-152"])
@pytest.mark.parametrize("kind", sorted(_DOWNLOADING_LABELS))
def test_a_configured_module_lets_a_model_without_one_download(model: str, kind: str) -> None:
    label = Label.model_validate(_DOWNLOADING_LABELS[kind])
    result = build_job(label, RenderOptions(model=model, image_module=ImageModule.A))
    assert module_findings(result) == []
    assert result.downloads
    assert result.data.count(STX + b"IAAF") == len(result.downloads)


@pytest.mark.os_agnostic
def test_a_job_with_several_downloads_is_refused_once_on_the_first_downloading_element() -> None:
    label = Label.model_validate(
        {
            "elements": [
                {"type": "text", "x": 5, "y": 5, "text": "Hi"},
                {"type": "qr", "x": 5, "y": 5, "data": "HELLO"},
                {"type": "image", "x": 1, "y": 1, "name": "LOGO", "bitmap": _EIGHT_BY_EIGHT},
                {"type": "circle", "x": 20, "y": 20, "radius": 5},
            ]
        }
    )
    result = build_job(label, RenderOptions(model="prodigy"))
    assert [f.element for f in module_findings(result)] == [1]
    assert "Prodigy" in module_findings(result)[0].message
