"""One bad row in a large file names its record, with one base everywhere: the envelope's, from 0."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from datamax_dpl import MissingFieldError, check_batch, iter_batch, render_batch
from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.label_io import LabelInputError, load_records
from datamax_dpl.application.use_cases import record_renders
from datamax_dpl.composition import build_production
from datamax_dpl.domain.label import Label, Text
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner

_LABEL = Label(elements=[Text(x=10, y=10, text="{a}"), Text(x=10, y=50, text="{b}")])
_DMX = RenderOptions(model="dmx-600")
_GOOD = {"a": "x", "b": "y"}
_TOO_LONG = "z" * 300
#: The bad record's number, counted from 0 as the envelope's ``record`` is.
_BAD = 2

_ROUTES = [
    pytest.param(("validate",), id="validate"),
    pytest.param(("render",), id="render-held"),
    pytest.param(("render", "-o", "{out}"), id="render-file"),
    pytest.param(("render", "-o", "-"), id="render-stdout"),
    pytest.param(("print", "--printer", "file:{out}"), id="print-file"),
    pytest.param(("print", "--printer", "-"), id="print-stdout"),
]


def _bad_batch(tmp_path: Path, *, bad: str) -> tuple[str, str]:
    label = tmp_path / "label.json"
    label.write_text(_LABEL.model_dump_json(), encoding="utf-8")
    data = tmp_path / "data.jsonl"
    rows = [_GOOD, _GOOD, {"a": "x"} if bad == "missing" else {"a": _TOO_LONG, "b": "y"}, _GOOD]
    data.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return str(label), str(data)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", _ROUTES)
@pytest.mark.parametrize(
    ("bad", "says"),
    [("missing", "no field 'b'"), ("long", "at most 255 characters")],
)
def test_every_route_names_the_record_in_a_failure_of_its_own(
    cli_runner: CliRunner, tmp_path: Path, command: tuple[str, ...], bad: str, says: str
) -> None:
    label, data = _bad_batch(tmp_path, bad=bad)
    verb, *rest = [word.format(out=tmp_path / "out.dpl") for word in command]
    result = cli_runner.invoke(cli_mod.cli, [verb, label, "--data", data, "--model", "dmx-600", *rest], obj=build_production)
    assert result.exit_code == 2, result.output
    assert f"data record {_BAD}" in result.stderr
    assert says in result.stderr
    assert not (tmp_path / "out.dpl").exists()


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "go_over",
    [
        pytest.param(lambda rows: list(iter_batch(_LABEL, rows, _DMX)), id="iter_batch"),
        pytest.param(lambda rows: check_batch(_LABEL, rows, _DMX), id="check_batch"),
        pytest.param(lambda rows: render_batch(_LABEL, rows, _DMX), id="render_batch"),
        pytest.param(lambda rows: record_renders(_LABEL, rows, _DMX), id="record_renders"),
    ],
)
def test_the_library_raises_the_same_types_naming_the_record(go_over: object) -> None:
    assert callable(go_over)
    with pytest.raises(MissingFieldError, match=rf"data record {_BAD} has no field 'b'"):
        go_over([_GOOD, _GOOD, {"a": "x"}])
    with pytest.raises(ValidationError) as caught:
        go_over([_GOOD, _GOOD, {"a": _TOO_LONG, "b": "y"}])
    [error] = caught.value.errors()
    assert error["loc"][0] == f"data record {_BAD}"
    assert error["loc"][1:3] == ("elements", 0)
    assert "at most 255 characters" in error["msg"]


@pytest.mark.os_agnostic
def test_a_finding_and_a_failure_use_one_base(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A finding on the same row as a failure elsewhere reads the same number."""
    label = tmp_path / "label.json"
    label.write_text(Label(elements=[Text(x=100000, y=10, text="{a}")]).model_dump_json(), encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_bytes(b"a\nr0\n")
    args = ["--json-bare", "validate", str(label), "--data", str(data), "--model", "dmx-600"]
    document = json.loads(cli_runner.invoke(cli_mod.cli, args, obj=build_production).stdout)
    assert {finding["record"] for finding in document["data"]["findings"]} == {0}


@pytest.mark.os_agnostic
def test_a_json_array_counts_its_records_from_0(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_text('[{"a": "x"}, 7]', encoding="utf-8")
    with pytest.raises(LabelInputError, match=r"record 1 is not an object"):
        load_records(str(path))


@pytest.mark.os_agnostic
def test_a_json_line_is_named_by_its_line_in_the_file(tmp_path: Path) -> None:
    """A line number is what an editor shows: it stays one, and says so."""
    path = tmp_path / "data.jsonl"
    path.write_text('{"a": "x"}\n\n7\n', encoding="utf-8")
    with pytest.raises(LabelInputError, match=r"line 3 is not an object"):
        load_records(str(path))
