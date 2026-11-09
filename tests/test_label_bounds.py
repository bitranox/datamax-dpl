"""A number in a label is refused by name at the model, before any arithmetic or drawing sees it."""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import ValidationError

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.calibration import across_label, along_label, calibration_labels
from datamax_dpl.domain.enums import FillPattern, PrinterStop
from datamax_dpl.domain.label import MAX_LENGTH, Box, Image, Label, Text
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner

TEXT = {"type": "text", "x": 1, "y": 1, "text": "A"}

#: Every length a label document can spell, as (document, the path a refusal names).
LENGTHS: list[tuple[dict[str, Any], str]] = [
    ({"elements": [{**TEXT, "x": "@"}]}, "elements.0.text.x"),
    ({"elements": [{**TEXT, "y": "@"}]}, "elements.0.text.y"),
    ({"elements": [{"type": "line", "x": "@", "y": 0, "x2": 1, "y2": 1, "thickness": 1}]}, "elements.0.line.x"),
    ({"elements": [{"type": "line", "x": 0, "y": "@", "x2": 1, "y2": 1, "thickness": 1}]}, "elements.0.line.y"),
    ({"elements": [{"type": "line", "x": 0, "y": 0, "x2": "@", "y2": 1, "thickness": 1}]}, "elements.0.line.x2"),
    ({"elements": [{"type": "line", "x": 0, "y": 0, "x2": 1, "y2": "@", "thickness": 1}]}, "elements.0.line.y2"),
    ({"elements": [{"type": "line", "x": 0, "y": 0, "x2": 1, "y2": 1, "thickness": "@"}]}, "elements.0.line.thickness"),
    (
        {"elements": [{"type": "box", "x": 0, "y": 0, "width": "@", "height": 1, "thickness": 1}]},
        "elements.0.box.width",
    ),
    (
        {"elements": [{"type": "box", "x": 0, "y": 0, "width": 1, "height": "@", "thickness": 1}]},
        "elements.0.box.height",
    ),
    (
        {"elements": [{"type": "box", "x": 0, "y": 0, "width": 1, "height": 1, "thickness": 1, "side_thickness": "@"}]},
        "elements.0.box.side_thickness",
    ),
    ({"elements": [{"type": "circle", "x": 1, "y": 1, "radius": "@"}]}, "elements.0.circle.radius"),
    (
        {"elements": [{"type": "polygon", "points": [{"x": "@", "y": 0}, {"x": 1, "y": 1}]}]},
        "elements.0.polygon.points.0.x",
    ),
    (
        {"elements": [{"type": "barcode", "x": 1, "y": 1, "symbology": "code128", "data": "A", "height": "@"}]},
        "elements.0.barcode.height",
    ),
    ({"elements": [{"type": "image", "x": "@", "y": 1, "name": "A"}]}, "elements.0.image.x"),
    ({"elements": [{"type": "qr", "x": 1, "y": "@", "data": "A"}]}, "elements.0.qr.y"),
    ({"elements": [TEXT], "width": "@"}, "width"),
    ({"elements": [TEXT], "height": "@"}, "height"),
    ({"elements": [TEXT], "column_offset": "@"}, "column_offset"),
    ({"elements": [TEXT], "row_offset": "@"}, "row_offset"),
    ({"elements": [TEXT], "continuous_length": "@"}, "continuous_length"),
    ({"elements": [TEXT], "present_distance": "@"}, "present_distance"),
]

#: Legal JSON numbers (and Python's spellings of the same) that no printer can take.
HOSTILE_NUMBERS = [1e308, 2**63, 10**400, -(2**63), math.inf, math.nan, MAX_LENGTH + 1]


def _with(document: object, value: object) -> object:
    """Return ``document`` with its ``"@"`` placeholder replaced by ``value``."""
    if isinstance(document, dict):
        items = cast("dict[str, object]", document)
        return {key: _with(item, value) for key, item in items.items()}
    if isinstance(document, list):
        return [_with(item, value) for item in cast("list[object]", document)]
    return value if document == "@" else document


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("document", "path"), LENGTHS, ids=[path for _, path in LENGTHS])
@pytest.mark.parametrize("number", HOSTILE_NUMBERS, ids=repr)
def test_a_hostile_number_is_refused_naming_the_field(document: dict[str, Any], path: str, number: float) -> None:
    with pytest.raises(ValidationError) as caught:
        Label.model_validate(_with(document, number))
    assert path in {".".join(str(part) for part in error["loc"]) for error in caught.value.errors()}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("document", "path"), LENGTHS, ids=[path for _, path in LENGTHS])
def test_the_longest_length_is_still_a_length(document: dict[str, Any], path: str) -> None:
    """The bound refuses past MAX_LENGTH and not at it, or the limit it documents would be wrong."""
    assert path
    Label.model_validate(_with(document, MAX_LENGTH))


@pytest.mark.os_agnostic
@pytest.mark.parametrize("number", ["1e308", "9223372036854775808", "-1e308"])
def test_validate_refuses_a_huge_json_number_with_an_envelope(
    cli_runner: CliRunner, tmp_path: Path, number: str
) -> None:
    path = tmp_path / "label.json"
    path.write_text('{"elements": [{"type": "text", "x": ' + number + ', "y": 1, "text": "A"}]}', encoding="utf-8")
    result = cli_runner.invoke(cli_mod.cli, ["--json-bare", "validate", str(path)], obj=build_testing)
    assert result.exit_code == 2, result.output
    document = json.loads(result.stdout)
    assert document["ok"] is False
    assert "elements.0.text.x" in document["message"]


@pytest.mark.os_agnostic
def test_a_number_past_a_double_is_refused_as_bad_json(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The JSON parser refuses this one itself, before the model, so it names the file and not the field."""
    path = tmp_path / "label.json"
    path.write_text('{"elements": [{"type": "text", "x": 1e400, "y": 1, "text": "A"}]}', encoding="utf-8")
    result = cli_runner.invoke(cli_mod.cli, ["--json-bare", "validate", str(path)], obj=build_testing)
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["error"] == "LabelInputError"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("field", ["stop_position_mm", "cutter_stop_position_mm", "joined_stop_position_mm"])
@pytest.mark.parametrize("number", [1e308, math.inf, math.nan])
def test_render_options_refuse_a_hostile_length(field: str, number: float) -> None:
    with pytest.raises(ValidationError) as caught:
        RenderOptions.model_validate({field: number})
    assert caught.value.errors()[0]["loc"][0] == field


@pytest.mark.os_agnostic
@pytest.mark.parametrize("number", [math.inf, math.nan, 1e308, MAX_LENGTH + 1])
def test_the_calibration_labels_refuse_a_hostile_size_at_once(number: float) -> None:
    """Refused before the ticks and the text are worked out, which is what took minutes at 1e308."""
    with pytest.raises(ValueError, match="width"):
        across_label(width=number, height=55, title="T")
    with pytest.raises(ValueError, match="height"):
        across_label(width=100, height=number, title="T")
    with pytest.raises(ValueError, match="width"):
        along_label(width=number, length=60, title="T")
    with pytest.raises(ValueError, match="length"):
        along_label(width=100, length=number, title="T")
    with pytest.raises(ValueError, match="width"):
        calibration_labels(width=number, height=55, title="T")


#: Never reached: every size below is refused before anything opens.
_PRINTER = "tcp://192.0.2.60:9100"


@pytest.mark.os_agnostic
def test_calibrate_print_keeps_the_bound_for_each_size(cli_runner: CliRunner) -> None:
    for option, value in (("--width", "inf"), ("--width", "1e308"), ("--height", "inf"), ("--height", "nan")):
        other = ["--height", "55"] if option == "--width" else ["--width", "100"]
        result = cli_runner.invoke(
            cli_mod.cli,
            ["--json-bare", "calibrate", "print", option, value, *other, "--axis", "across", "--printer", _PRINTER],
            obj=build_testing,
        )
        assert result.exit_code == 2, result.output
        document = json.loads(result.stdout)
        assert document["error"] == "ValueError"
        assert option.lstrip("-") in document["message"]


# ======================== Numbers in a label are numbers ========================

NUMBERS_AND_BOOLS: list[tuple[dict[str, Any], str]] = [
    *LENGTHS,
    ({"elements": [{**TEXT, "font": "@"}]}, "elements.0.text.font"),
    ({"elements": [{**TEXT, "font": 9, "points": "@"}]}, "elements.0.text.points"),
    ({"elements": [{**TEXT, "width_multiplier": "@"}]}, "elements.0.text.width_multiplier"),
    ({"elements": [{**TEXT, "height_multiplier": "@"}]}, "elements.0.text.height_multiplier"),
    ({"elements": [{**TEXT, "counter": {"step": "@"}}]}, "elements.0.text.counter.step"),
    (
        {"elements": [{"type": "scalable_text", "x": 1, "y": 1, "text": "A", "height_points": "@"}]},
        "elements.0.scalable_text.height_points",
    ),
    (
        {"elements": [{"type": "barcode", "x": 1, "y": 1, "symbology": "code128", "data": "A", "narrow_dots": "@"}]},
        "elements.0.barcode.narrow_dots",
    ),
    (
        {
            "elements": [
                {"type": "barcode", "x": 1, "y": 1, "symbology": "pdf417", "data": "A", "pdf417": {"rows": "@"}}
            ]
        },
        "elements.0.barcode.pdf417.rows",
    ),
    (
        {
            "elements": [
                {"type": "barcode", "x": 1, "y": 1, "symbology": "datamatrix", "data": "A", "datamatrix": {"rows": "@"}}
            ]
        },
        "elements.0.barcode.datamatrix.rows",
    ),
    (
        {"elements": [{"type": "image", "x": 1, "y": 1, "name": "A", "magnification": "@"}]},
        "elements.0.image.magnification",
    ),
    ({"elements": [{"type": "qr", "x": 1, "y": 1, "data": "A", "module_dots": "@"}]}, "elements.0.qr.module_dots"),
    ({"elements": [TEXT], "quantity": "@"}, "quantity"),
    ({"elements": [TEXT], "heat": "@"}, "heat"),
    ({"elements": [TEXT], "count_by": "@"}, "count_by"),
    ({"elements": [TEXT], "cut": {"every": "@"}}, "cut.every"),
    ({"elements": [TEXT], "dot_size": {"width": "@", "height": 1}}, "dot_size.width"),
]

BOOLEANS: list[tuple[dict[str, Any], str]] = [
    ({"elements": [TEXT], "mirror": "@"}, "mirror"),
    ({"elements": [TEXT], "pause_each_label": "@"}, "pause_each_label"),
    ({"elements": [TEXT], "plain_zero": "@"}, "plain_zero"),
    ({"elements": [TEXT], "cut": {"at_end": "@"}}, "cut.at_end"),
    ({"elements": [{**TEXT, "counter": {"decrement": "@"}}]}, "elements.0.text.counter.decrement"),
    ({"elements": [{**TEXT, "counter": {"alphanumeric": "@"}}]}, "elements.0.text.counter.alphanumeric"),
    (
        {"elements": [{"type": "barcode", "x": 1, "y": 1, "symbology": "code128", "data": "A", "human_readable": "@"}]},
        "elements.0.barcode.human_readable",
    ),
    (
        {
            "elements": [
                {"type": "barcode", "x": 1, "y": 1, "symbology": "pdf417", "data": "A", "pdf417": {"truncated": "@"}}
            ]
        },
        "elements.0.barcode.pdf417.truncated",
    ),
]


def _refused_at(document: dict[str, Any], value: object) -> set[str]:
    with pytest.raises(ValidationError) as caught:
        Label.model_validate(_with(document, value))
    return {".".join(str(part) for part in error["loc"]) for error in caught.value.errors()}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("document", "path"), NUMBERS_AND_BOOLS, ids=[path for _, path in NUMBERS_AND_BOOLS])
@pytest.mark.parametrize("value", [True, False, "10", "1.5", "inf", "1e3", "1_000", ""], ids=repr)
def test_a_number_in_a_label_refuses_text_and_booleans(document: dict[str, Any], path: str, value: object) -> None:
    """JSON is typed and the schema says number: `"x": "10"` and `"x": true` were read as 10 and 1."""
    assert path in _refused_at(document, value)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("document", "path"), BOOLEANS, ids=[path for _, path in BOOLEANS])
@pytest.mark.parametrize("value", [0, 1, "true", "yes", "1", 1.0], ids=repr)
def test_a_boolean_in_a_label_refuses_numbers_and_text(document: dict[str, Any], path: str, value: object) -> None:
    assert path in _refused_at(document, value)


@pytest.mark.os_agnostic
def test_a_whole_float_is_no_integer_field_value_and_an_integer_is_still_a_length() -> None:
    assert "quantity" in _refused_at({"elements": [TEXT], "quantity": "@"}, 2.0)
    assert Label.model_validate({"elements": [{**TEXT, "x": 5}]}).elements[0].model_dump()["x"] == 5.0


@pytest.mark.os_agnostic
@pytest.mark.parametrize("value", [True, "2.5", "inf", "1e3", "abc"], ids=repr)
def test_the_label_stop_position_takes_a_number_or_its_one_word(value: object) -> None:
    assert "stop_position" in _refused_at({"elements": [TEXT], "stop_position": "@"}, value)


@pytest.mark.os_agnostic
def test_the_label_stop_position_refusal_of_text_names_the_word() -> None:
    with pytest.raises(ValidationError, match="Input should be 'printer'"):
        Label.model_validate({"elements": [TEXT], "stop_position": "2.5"})


@pytest.mark.os_agnostic
def test_the_label_stop_position_keeps_its_number_and_word() -> None:
    assert Label.model_validate({"elements": [TEXT], "stop_position": 2.5}).stop_position == 2.5
    assert Label.model_validate({"elements": [TEXT], "stop_position": 2}).stop_position == 2.0
    assert Label.model_validate({"elements": [TEXT], "stop_position": "printer"}).stop_position == PrinterStop.PRINTER


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "field",
    [
        "scale_across",
        "scale_along",
        "offset_across",
        "offset_along",
        "stop_position_mm",
        "cutter_stop_position_mm",
        "joined_stop_position_mm",
        "image_memory_bytes",
        "max_format_rows",
        "start_of_print_mm",
        "max_label_length_mm",
    ],
)
@pytest.mark.parametrize("value", [True, "1"], ids=repr)
def test_render_options_refuse_text_and_booleans_as_numbers(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as caught:
        RenderOptions.model_validate({field: value})
    assert caught.value.errors()[0]["loc"][0] == field


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "field", ["cutter_installed", "model_image_budget", "label_present", "clear_graphics_before_label"]
)
@pytest.mark.parametrize("value", [1, "true", "yes"], ids=repr)
def test_render_options_refuse_numbers_and_text_as_booleans(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as caught:
        RenderOptions.model_validate({field: value})
    assert caught.value.errors()[0]["loc"][0] == field


@pytest.mark.os_agnostic
def test_placeholder_filling_never_produces_a_numeric_string_in_a_number_field(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Filling replaces text alone: a record's "12" stays the text of a text field, with --data too."""
    label = tmp_path / "label.json"
    label.write_text('{"quantity": 2, "elements": [{"type": "text", "x": 1, "y": 1, "text": "{n}"}]}', encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_text("n\n12\n34\n", encoding="utf-8")
    result = cli_runner.invoke(
        cli_mod.cli, ["--json-bare", "validate", "--data", str(data), str(label)], obj=build_testing
    )
    assert result.exit_code == 0, result.output


_TEXT = '{"type": "text", "x": 5, "y": 5, "text": "X"'
#: Every coded field as a label with VALUE in its place, and the one code that field certainly holds.
_CODED: list[tuple[str, int]] = [
    (f'{{"attribute": VALUE, "elements": [{_TEXT}}}]}}', 1),
    (f'{{"elements": [{_TEXT}, "attribute": VALUE}}]}}', 1),
    (f'{{"elements": [{_TEXT}, "rotation": VALUE}}]}}', 0),
    ('{"elements": [{"type": "box", "x": 5, "y": 5, "width": 9, "height": 9, "thickness": 1, "fill": VALUE}]}', 0),
    ('{"elements": [{"type": "circle", "x": 5, "y": 5, "radius": 1, "fill": VALUE}]}', 0),
    (
        '{"elements": [{"type": "polygon", "fill": VALUE, '
        '"points": [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 0, "y": 5}]}]}',
        0,
    ),
    (
        '{"elements": [{"type": "scalable_text", "x": 5, "y": 5, "text": "X", "height_points": 10, '
        '"attribute": VALUE}]}',
        1,
    ),
    (
        '{"elements": [{"type": "barcode", "x": 5, "y": 5, "symbology": "datamatrix", "data": "D", '
        '"datamatrix": {"ecc": VALUE}}]}',
        0,
    ),
]
_CODED_IDS = ["label-attribute", "attribute", "rotation", "box-fill", "circle-fill", "polygon-fill", "scalable", "ecc"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("template", "_code"), _CODED, ids=_CODED_IDS)
@pytest.mark.parametrize("value", ["true", "false", '"1"', '"0"', '""', "1.0"])
def test_a_coded_field_refuses_what_lax_validation_would_read_as_a_number(
    template: str, _code: int, value: str
) -> None:
    with pytest.raises(ValidationError, match="needs a whole number"):
        Label.model_validate(json.loads(template.replace("VALUE", value)))


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("template", "code"), _CODED, ids=_CODED_IDS)
def test_a_coded_field_still_takes_its_code_from_json(template: str, code: int) -> None:
    document = template.replace("VALUE", str(code))
    assert Label.model_validate(json.loads(document)) == Label.model_validate_json(document)


@pytest.mark.os_agnostic
def test_a_coded_field_takes_the_enum_member_a_library_caller_passes() -> None:
    box = Box(x=0, y=0, width=9, height=9, thickness=1, fill=FillPattern.SHADE_6)
    assert box.fill is FillPattern.SHADE_6


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("bitmap", "message"),
    [(["#", "##"], "same, non-zero length"), ([""], "same, non-zero length"), (["#x"], "may only contain")],
)
def test_a_malformed_bitmap_is_refused(bitmap: list[str], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Image(x=1, y=1, name="A", bitmap=bitmap)


@pytest.mark.os_agnostic
def test_font_9_needs_points_and_no_other_font_takes_them() -> None:
    with pytest.raises(ValidationError, match="needs 'points'"):
        Text(x=1, y=1, text="A", font=9)
    with pytest.raises(ValidationError, match="apply to font 9 only"):
        Text(x=1, y=1, text="A", font=2, points=8)


@pytest.mark.os_agnostic
def test_a_mirrored_label_sends_its_mirror_command_on_a_model_that_has_it() -> None:
    result = build_job(Label(mirror=True, elements=[Text(x=1, y=1, text="A")]), RenderOptions(model="dmx-800"))
    assert result.ok
    assert b"\rM\r" in result.data
