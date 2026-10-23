"""Reading DPL back: command explanations and label round trips through the encoder."""

from __future__ import annotations

import pytest

from datamax_dpl.domain.decoder import explain, readable
from datamax_dpl.domain.enums import Rotation, Unit
from datamax_dpl.domain.label import Barcode, Box, Circle, Label, Line, Point, Polygon, Text
from datamax_dpl.domain.render import render
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.symbology import Symbology

APPENDIX_B = b"\x02L\rH07\rD11\r19110080100002510K OHM 1/4 WATT\r1a6210000000050590PCS\rE\r"


@pytest.mark.os_agnostic
def test_the_manuals_sample_program_is_explained_command_by_command() -> None:
    result = explain(APPENDIX_B)
    assert [c.kind for c in result.commands] == ["format", "format", "format", "record", "record", "end"]
    assert result.commands[3].meaning == "text font 9"
    assert result.commands[4].meaning == "barcode code39"
    assert result.problems == ()


@pytest.mark.os_agnostic
def test_the_manuals_sample_program_rebuilds_its_label() -> None:
    label = explain(APPENDIX_B).label
    assert label is not None
    text, barcode = label.elements
    assert isinstance(text, Text) and text.text == "10K OHM 1/4 WATT" and text.points == 30
    assert isinstance(barcode, Barcode) and barcode.data == "590PCS" and not barcode.human_readable
    assert (barcode.wide_dots, barcode.narrow_dots, barcode.height) == (6, 2, 1.0)
    assert label.heat == 7


@pytest.mark.os_agnostic
def test_rendered_label_survives_a_round_trip() -> None:
    original = Label(
        unit=Unit.INCH,
        quantity=3,
        elements=[
            Text(x=0.25, y=1.0, text="HELLO", font=3, width_multiplier=2, rotation=Rotation.DEG_90),
            Barcode(x=0.5, y=0.2, symbology=Symbology.CODE128, data="ABC123", height=0.5, narrow_dots=2, wide_dots=2),
            Box(x=0.1, y=0.1, width=3.0, height=2.0, thickness=0.02, side_thickness=0.03),
            Line(x=0.1, y=1.5, x2=3.0, y2=1.5, thickness=0.01),
            Circle(x=2.0, y=1.0, radius=0.3),
            Polygon(points=[Point(x=0.1, y=0.1), Point(x=0.5, y=0.9), Point(x=0.9, y=0.1)]),
        ],
    )
    rebuilt = explain(render(original, RenderOptions(model="dmx-600")).data).label
    assert rebuilt is not None
    assert rebuilt.quantity == 3
    assert [e.model_dump() for e in rebuilt.elements] == [e.model_dump() for e in original.elements]


@pytest.mark.os_agnostic
def test_metric_job_rebuilds_in_millimetres() -> None:
    original = Label(elements=[Text(x=12.5, y=3.0, text="mm")])
    rebuilt = explain(render(original, RenderOptions(model="dmx-600")).data).label
    assert rebuilt is not None and rebuilt.unit is Unit.MM
    assert rebuilt.elements[0].model_dump() == original.elements[0].model_dump()


@pytest.mark.os_agnostic
def test_system_and_immediate_commands_are_named() -> None:
    kinds = [(c.kind, c.meaning) for c in explain(b"\x01A\x02n\x02Kf0100\x02f230\x02o").commands]
    assert kinds == [
        ("immediate", "status request"),
        ("system", "inch mode"),
        ("system", "present distance"),
        ("system", "stop position (backfeed)"),
        ("system", "cut now"),
    ]


@pytest.mark.os_agnostic
def test_an_image_download_is_one_command_and_its_rows_are_skipped() -> None:
    data = b"\x02IAAFLOGO\r8001FF\r0000FF02\rFFFF\r\x02L\r1Y1100000000000LOGO\rE\r"
    result = explain(data)
    assert [c.kind for c in result.commands] == ["download", "format", "record", "end"]


@pytest.mark.os_agnostic
def test_garbage_is_reported_not_guessed() -> None:
    result = explain(b"\x02L\r1Z99XXXXXXXXXXXXXXXX\rE\r")
    assert result.problems
    assert result.label is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize("data", [b"", b"\x02", b"\x01", b"\x02L", b"\x02L\r1", b"\xff\xfe\x00", b"\x02IAAF"])
def test_truncated_or_binary_input_never_raises(data: bytes) -> None:
    explain(data)


@pytest.mark.os_agnostic
def test_readable_names_control_bytes() -> None:
    assert readable(b"\x01A\x02L\r\x1b\x7f") == "<SOH>A<STX>L<CR><ESC><7F>"


@pytest.mark.os_agnostic
def test_lower_case_text_in_a_capitals_only_font_is_reported() -> None:
    result = render(Label(elements=[Text(x=1, y=1, text="Hello", font=3)]), RenderOptions(model="dmx-600"))
    assert b"HELLO" in result.data
    assert "text_uppercased" in {f.code for f in result.findings}
