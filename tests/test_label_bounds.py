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
from datamax_dpl.domain.label import MAX_LENGTH, Label
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
    ({"elements": [{"type": "box", "x": 0, "y": 0, "width": "@", "height": 1, "thickness": 1}]}, "elements.0.box.width"),
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
    path.write_text('{"elements": [{"type": "text", "x": %s, "y": 1, "text": "A"}]}' % number, encoding="utf-8")
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
@pytest.mark.parametrize("field", ["stop_position_mm", "cutter_stop_position_mm"])
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


@pytest.mark.os_agnostic
def test_calibrate_print_keeps_the_bound_for_each_size(cli_runner: CliRunner) -> None:
    for option, value in (("--width", "inf"), ("--width", "1e308"), ("--height", "inf"), ("--height", "nan")):
        other = ["--height", "55"] if option == "--width" else ["--width", "100"]
        result = cli_runner.invoke(
            cli_mod.cli,
            ["--json-bare", "calibrate", "print", option, value, *other, "--axis", "across", "--printer", "file:/opt/x"],
            obj=build_testing,
        )
        assert result.exit_code == 2, result.output
        document = json.loads(result.stdout)
        assert document["error"] == "ValueError"
        assert option.lstrip("-") in document["message"]
