"""Reading DPL back: command explanations and label round trips through the encoder."""

from __future__ import annotations

import string

import pytest

from datamax_dpl.domain.decoder import explain, readable
from datamax_dpl.domain.enums import CommandKind, Rotation, SpeedCode, Unit
from datamax_dpl.domain.label import Barcode, Box, Circle, Counter, Cut, Image, Label, Line, Point, Polygon, Text
from datamax_dpl.domain.render import render
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.symbology import Symbology

APPENDIX_B = b"\x02L\rH07\rD11\r19110080100002510K OHM 1/4 WATT\r1a6210000000050590PCS\rE\r"


@pytest.mark.os_agnostic
def test_the_manuals_sample_program_is_explained_command_by_command() -> None:
    result = explain(APPENDIX_B)
    assert [c.kind for c in result.commands] == [
        CommandKind.FORMAT,
        CommandKind.FORMAT,
        CommandKind.FORMAT,
        CommandKind.RECORD,
        CommandKind.RECORD,
        CommandKind.END,
    ]
    assert all(type(c.kind) is CommandKind for c in result.commands)
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
@pytest.mark.parametrize(
    "counter",
    [Counter(step=1), Counter(step=5, decrement=True, alphanumeric=True, fill=" "), Counter(step=12, decrement=True)],
)
def test_a_counting_field_keeps_its_counter_through_a_round_trip(counter: Counter) -> None:
    original = Label(
        unit=Unit.INCH,
        quantity=3,
        elements=[
            Text(x=0.25, y=1.0, text="0001", counter=counter),
            Barcode(x=0.5, y=0.2, symbology=Symbology.CODE128, data="A001", height=0.5, counter=counter),
        ],
    )
    result = explain(render(original, RenderOptions(model="dmx-600")).data)
    assert result.label is not None
    assert [e.model_dump() for e in result.label.elements] == [e.model_dump() for e in original.elements]
    assert result.problems == ()


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "stream",
    [
        b"\x02L\r+001\rE\r",
        render(
            Label(elements=[Box(x=1.0, y=1.0, width=10.0, height=5.0, thickness=0.5)]), RenderOptions(model="dmx-600")
        ).data.replace(b"E\r", b"+001\rE\r"),
    ],
    ids=["no-field-before-it", "after-a-box"],
)
def test_a_counter_line_with_no_counting_field_before_it_is_a_problem(stream: bytes) -> None:
    result = explain(stream)
    assert any(problem.startswith("+001: ") and "no text or barcode" in problem for problem in result.problems)


@pytest.mark.os_agnostic
def test_a_stream_with_two_formats_says_why_no_label_is_rebuilt() -> None:
    one = render(Label(elements=[Text(x=1.0, y=1.0, text="A")]), RenderOptions(model="dmx-600")).data
    result = explain(one + one)
    assert result.label is None
    assert "2 label formats: only a stream with exactly one is rebuilt as a label" in result.problems


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
        (CommandKind.IMMEDIATE, "status request"),
        (CommandKind.SYSTEM, "inch mode"),
        (CommandKind.SYSTEM, "present distance"),
        (CommandKind.SYSTEM, "stop position (backfeed)"),
        (CommandKind.SYSTEM, "cut now"),
    ]


@pytest.mark.os_agnostic
def test_an_image_download_is_one_command_and_its_rows_are_skipped() -> None:
    data = b"\x02IAAFLOGO\r8001FF\r0000FF02\rFFFF\r\x02L\r1Y1100000000000LOGO\rE\r"
    result = explain(data)
    assert [c.kind for c in result.commands] == [
        CommandKind.DOWNLOAD,
        CommandKind.FORMAT,
        CommandKind.RECORD,
        CommandKind.END,
    ]


@pytest.mark.os_agnostic
def test_job_settings_are_rebuilt_into_the_label() -> None:
    label = explain(b"\x02L\rQ0003\rH12\rPC\rSE\r:0002\r121100000500025Hi\rE\r").label
    assert label is not None
    assert (label.quantity, label.heat, label.print_speed, label.slew_speed, label.cut) == (
        3,
        12,
        SpeedCode.IPS_2_0,
        SpeedCode.IPS_3_0,
        Cut(every=2),
    )


@pytest.mark.os_agnostic
def test_without_settings_the_label_has_the_printer_defaults() -> None:
    label = explain(b"\x02L\r121100000500025Hi\rE\r").label
    assert label is not None
    assert (label.quantity, label.heat, label.print_speed, label.slew_speed, label.cut) == (1, None, None, None, None)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("settings", "errors"),
    [
        (b":0000\r", 1),
        (b"Q0000\r:0000\r", 2),
        (b"Q0000\rH99\r", 2),
        (b":0002\r:0000\r", 1),
    ],
)
def test_an_invalid_setting_is_counted_with_the_label_and_leaves_no_label(settings: bytes, errors: int) -> None:
    result = explain(b"\x02L\r" + settings + b"121100000500025Hi\rE\r")
    assert result.label is None
    assert result.problems == (f"label: {errors} validation errors",)


_NO_SPEED = next(letter for letter in reversed(string.ascii_uppercase) if letter not in {c.value for c in SpeedCode})


@pytest.mark.os_agnostic
@pytest.mark.parametrize("setting", [b"Qab12", b"Hxx", b":", b"P" + _NO_SPEED.encode(), b"S" + _NO_SPEED.encode()])
def test_an_unreadable_setting_is_a_problem_and_the_rest_is_still_explained(setting: bytes) -> None:
    result = explain(b"\x02L\r" + setting + b"\r121100000500025Hi\rE\r")
    assert result.problems == (f"{readable(setting)}: ValueError",)
    bad = next(c for c in result.commands if c.raw == readable(setting))
    assert bad.kind is CommandKind.FORMAT
    assert bad.meaning.endswith("(not understood)")
    assert [c.kind for c in result.commands][-2:] == [CommandKind.RECORD, CommandKind.END]
    assert result.label is not None
    assert [e.text for e in result.label.elements if isinstance(e, Text)] == ["Hi"]
    assert (result.label.quantity, result.label.heat, result.label.cut) == (1, None, None)
    assert (result.label.print_speed, result.label.slew_speed) == (None, None)


@pytest.mark.os_agnostic
def test_a_later_valid_cut_replaces_an_invalid_one() -> None:
    result = explain(b"\x02L\r:0000\r:0004\r121100000500025Hi\rE\r")
    assert result.problems == ()
    assert result.label is not None and result.label.cut == Cut(every=4)


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


# ======================== image magnification round trip ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize("magnification", [1, 3])
def test_explain_rebuilds_an_images_square_magnification(magnification: int) -> None:
    source = Label(elements=[Image(x=5, y=5, name="LOGO", magnification=magnification)])
    explanation = explain(render(source, RenderOptions(model="dmx-600")).data)
    assert explanation.label is not None
    [element] = explanation.label.elements
    assert isinstance(element, Image)
    assert element.magnification == magnification
    assert not any("magnified" in command.meaning for command in explanation.commands)


@pytest.mark.os_agnostic
def test_explain_names_a_non_square_image_magnification_the_image_cannot_hold() -> None:
    data = render(Label(elements=[Image(x=5, y=5, name="LOGO")]), RenderOptions(model="dmx-600")).data
    explanation = explain(data.replace(b"1Y11000", b"1Y23000"))
    assert "image, magnified 2x3" in [command.meaning for command in explanation.commands]
    assert explanation.label is not None
    [element] = explanation.label.elements
    assert isinstance(element, Image)
    assert element.magnification == 1
