"""What the CLI refuses in its input before it costs anything: oversized files, broken CSV, huge previews."""

from __future__ import annotations

import csv
import json
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from lib_layered_config import Config

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.label_io import MAX_INPUT_BYTES, LabelInputError, load_records, read_input
from datamax_dpl.adapters.cli.settings import PreviewSettings, preview_settings
from datamax_dpl.adapters.preview import render_preview_png
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.decoder import explain
from datamax_dpl.domain.errors import ConfigurationError, PreviewTooLongError
from datamax_dpl.domain.label import Barcode, Box, Circle, Image, Label, Line, Point, Polygon, Qr, ScalableText, Text
from datamax_dpl.domain.preview import DEFAULT_PREVIEW_MAX_LENGTH_MM, canvas_for, check_preview_length
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.symbology import Symbology

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner

LABEL = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "{name}"}]}


def _invoke(cli_runner: CliRunner, *args: str, stdin: bytes | None = None) -> Any:
    return cli_runner.invoke(cli_mod.cli, list(args), obj=build_testing, input=stdin)


def _write_label(tmp_path: Path, document: object = LABEL) -> str:
    path = tmp_path / "label.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


# ======================== CSV ========================


@pytest.mark.os_agnostic
def test_a_field_over_the_csv_limit_is_an_input_error_with_an_envelope(cli_runner: CliRunner, tmp_path: Path) -> None:
    """csv's own field limit reaches the caller as a refusal naming the file, exit 2, not a bare exit 1."""
    records = tmp_path / "huge.csv"
    records.write_text("name\n" + "x" * (csv.field_size_limit() + 1) + "\n", encoding="utf-8")
    result = _invoke(cli_runner, "--json-bare", "validate", "--data", str(records), _write_label(tmp_path))
    assert result.exit_code == 2, result.output
    document = json.loads(result.stdout)
    assert document["error"] == "LabelInputError"
    assert str(records) in document["message"]


@pytest.mark.os_agnostic
def test_a_stray_quote_is_refused_rather_than_merging_every_row_after_it(tmp_path: Path) -> None:
    """Without strict parsing the rows after a stray quote became one field and printed as one label."""
    records = tmp_path / "stray.csv"
    records.write_text('name\nrow1\n"row2 starts a quote\nrow3\nrow4\n', encoding="utf-8")
    with pytest.raises(LabelInputError, match="not valid CSV"):
        load_records(str(records))


@pytest.mark.os_agnostic
def test_a_quoted_field_may_still_span_lines(tmp_path: Path) -> None:
    """Strict parsing refuses broken quoting only: a closed quote holding a newline stays one value."""
    records = tmp_path / "ok.csv"
    records.write_text('name,sku\n"two\nlines",A1\nplain,"say ""hi"""\n', encoding="utf-8")
    assert load_records(str(records)) == [{"name": "two\nlines", "sku": "A1"}, {"name": "plain", "sku": 'say "hi"'}]


# ======================== the input size cap ========================


@pytest.mark.os_agnostic
def test_a_file_past_the_input_cap_is_refused(tmp_path: Path) -> None:
    """The cap stops a wrong path (a disk image) before it is read whole; exactly the cap still reads."""
    path = tmp_path / "big.json"
    path.write_bytes(b" " * MAX_INPUT_BYTES)
    assert len(read_input(str(path))) == MAX_INPUT_BYTES
    path.write_bytes(b" " * (MAX_INPUT_BYTES + 1))
    with pytest.raises(LabelInputError, match="larger than"):
        read_input(str(path))


@pytest.mark.os_agnostic
def test_stdin_past_the_input_cap_is_refused_with_an_envelope(cli_runner: CliRunner) -> None:
    """The same cap holds on stdin, where no file size can be asked for first."""
    result = _invoke(cli_runner, "--json-bare", "validate", "-", stdin=b" " * (MAX_INPUT_BYTES + 1))
    assert result.exit_code == 2, result.output
    document = json.loads(result.stdout)
    assert document["error"] == "LabelInputError"
    assert "larger than" in document["message"]


# ======================== the preview's size ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "document",
    [
        {"height": 100000, "elements": [{"type": "text", "x": 5, "y": 5, "text": "A"}]},
        {"width": 100000, "elements": [{"type": "text", "x": 5, "y": 5, "text": "A"}]},
        {"elements": [{"type": "text", "x": 5, "y": 100000, "text": "A"}]},
    ],
)
def test_preview_refuses_a_huge_label_before_drawing_it(
    cli_runner: CliRunner, tmp_path: Path, document: dict[str, object]
) -> None:
    """One number in a label JSON decided the canvas size: 118 s and 1.47 GB, or MemoryError."""
    output = tmp_path / "out.png"
    result = _invoke(cli_runner, "--json-bare", "preview", _write_label(tmp_path, document), "-o", str(output))
    assert result.exit_code == 2, result.output
    envelope = json.loads(result.stdout)
    assert envelope["error"] == "LabelInputError"
    assert "[preview] max_length_mm" in envelope["message"]
    assert not output.exists()


@pytest.mark.os_agnostic
def test_the_preview_limit_comes_from_the_settings_key(cli_runner: CliRunner, tmp_path: Path) -> None:
    """--set lowers it, and an inch label is measured in millimetres too."""
    elements = [{"type": "text", "x": 1, "y": 1, "text": "A"}]
    label = _write_label(tmp_path, {"unit": "in", "height": 2, "width": 1, "elements": elements})
    output = str(tmp_path / "out.png")
    assert _invoke(cli_runner, "--set", "preview.max_length_mm=51", "preview", label, "-o", output).exit_code == 0
    assert _invoke(cli_runner, "--set", "preview.max_length_mm=50", "preview", label, "-o", output).exit_code == 2


@pytest.mark.os_agnostic
def test_a_label_of_exactly_the_limit_is_drawn() -> None:
    """The frame's extra dot does not count, so a limit of N mm draws an N mm label."""
    profile = get_profile("dmx-600")
    label = Label(height=100, width=50, elements=[Text(x=5, y=5, text="A")])
    check_preview_length(canvas_for(label, profile), max_length_mm=100)
    with pytest.raises(PreviewTooLongError):
        check_preview_length(canvas_for(label, profile), max_length_mm=99)
    assert DEFAULT_PREVIEW_MAX_LENGTH_MM == 2000.0


@pytest.mark.os_agnostic
def test_the_library_preview_refuses_too_and_takes_its_own_limit() -> None:
    """A library caller gets the same guard, with the limit as a parameter."""
    profile = get_profile("dmx-600")
    long_label = Label(height=2001, elements=[Text(x=5, y=5, text="A")])
    with pytest.raises(PreviewTooLongError):
        render_preview_png(long_label, profile)
    short = Label(height=20, width=20, elements=[Text(x=5, y=5, text="A")])
    assert render_preview_png(short, profile, max_length_mm=20).startswith(b"\x89PNG")
    with pytest.raises(PreviewTooLongError):
        render_preview_png(short, profile, max_length_mm=19)


@pytest.mark.os_agnostic
def test_verify_refuses_a_huge_label_before_anything_is_printed(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The expected picture comes after the print, so the refusal must come before it."""
    label = _write_label(tmp_path, {"height": 100000, "elements": [{"type": "text", "x": 5, "y": 5, "text": "A"}]})
    target = f"file:{tmp_path / 'x.dpl'}"
    result = _invoke(cli_runner, "--json-bare", "--set", "camera.kind=file", "verify", label, "--printer", target)
    assert result.exit_code == 2, result.output
    assert "[preview] max_length_mm" in json.loads(result.stdout)["message"]
    assert not (tmp_path / "x.dpl").exists()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("value", [0, -1, "x", float("inf")])
def test_a_bad_preview_limit_is_refused_naming_its_key(value: object) -> None:
    """A typo in the file is a configuration error naming preview.max_length_mm."""
    with pytest.raises(ConfigurationError, match=r"preview\.max_length_mm"):
        preview_settings(Config({"preview": {"max_length_mm": value}}, {}))


@pytest.mark.os_agnostic
def test_the_preview_limit_defaults_to_2000_mm() -> None:
    """An unset key means the default."""
    assert preview_settings(Config({}, {})) == PreviewSettings(max_length_mm=2000.0)


# ======================== explain on a cut-short job ========================


@pytest.mark.os_agnostic
def test_explain_reports_a_truncated_graphic_record_as_a_problem(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A truncated job is what the tool's own advice describes: it must read, not raise IndexError."""
    path = tmp_path / "crash.dpl"
    path.write_bytes(b"\x02L\r1X1100000030004")
    assert explain(path.read_bytes()).problems
    result = _invoke(cli_runner, "--json-bare", "explain", str(path))
    assert result.exit_code in (0, 1), result.output
    assert json.loads(result.stdout)["data"]["problems"]


def _every_record_kind() -> bytes:
    """A job holding every record kind ``explain`` parses."""
    label = Label(
        elements=[
            Text(x=5, y=5, text="Hi"),
            ScalableText(x=5, y=10, text="Hi", height_points=10),
            Line(x=1, y=1, x2=20, y2=1, thickness=0.5),
            Box(x=1, y=2, width=10, height=5, thickness=0.3),
            Circle(x=20, y=20, radius=5),
            Polygon(points=[Point(x=1, y=1), Point(x=9, y=1), Point(x=5, y=9)]),
            Barcode(x=5, y=20, symbology=Symbology.CODE128, data="AB12", height=8),
            Barcode(x=5, y=40, symbology=Symbology.PDF417, data="AB12", height=8),
            Image(x=1, y=30, name="LOGO", bitmap=["#.", ".#"]),
            Qr(x=30, y=30, data="AB"),
        ]
    )
    return build_job(label, RenderOptions(model="dmx-600")).data


@pytest.mark.os_agnostic
def test_explain_survives_a_job_cut_at_every_byte() -> None:
    """Every truncation point of every record kind reads as commands and problems, never an exception."""
    data = _every_record_kind()
    for end in range(len(data)):
        explain(data[:end])


@pytest.mark.os_agnostic
@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=200))
def test_explain_never_raises_on_arbitrary_bytes(tail: bytes) -> None:
    """Whatever follows a format's start, the answer is commands and problems."""
    explain(b"\x02L\r" + tail)
