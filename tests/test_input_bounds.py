"""What the CLI refuses in its input before it costs anything: oversized files, broken CSV, huge previews."""

from __future__ import annotations

import csv
import json
import tracemalloc
from typing import TYPE_CHECKING, Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from lib_layered_config import Config
from pydantic import ValidationError

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.label_io import MAX_INPUT_BYTES, LabelInputError, load_records, read_input
from datamax_dpl.adapters.cli.settings import PreviewSettings, preview_settings
from datamax_dpl.adapters.preview import render_preview_png
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.decoder import MAX_EXPLAINED_COMMANDS, explain
from datamax_dpl.domain.errors import ConfigurationError, PreviewTooLongError
from datamax_dpl.domain.label import (
    MAX_FIELD_DATA,
    MAX_PDF417_DATA,
    MAX_QR_DATA,
    Barcode,
    Box,
    Circle,
    Element,
    Image,
    Label,
    Line,
    Point,
    Polygon,
    Qr,
    ScalableText,
    Text,
)
from datamax_dpl.domain.placeholders import MissingFieldError, fill_label, fill_text
from datamax_dpl.domain.preview import (
    DEFAULT_PREVIEW_MAX_LENGTH_MM,
    SentAs,
    canvas_for,
    check_preview_length,
    preview_bitmap,
)
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.symbology import Symbology

if TYPE_CHECKING:
    from collections.abc import Callable
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
    # Bytes, because write_text turns \n into \r\n on Windows and a quoted field keeps its line ending.
    records.write_bytes(b'name,sku\n"two\nlines",A1\nplain,"say ""hi"""\n')
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


@pytest.mark.os_agnostic
@pytest.mark.parametrize("position", [0, 1], ids=["width", "height"])
@pytest.mark.parametrize("byte", range(256))
def test_explain_takes_every_byte_where_a_record_holds_a_multiplier(byte: int, position: int) -> None:
    """Latin-1 holds a digit int() refuses, \\xb2, and a letter upper() doubles, \\xdf."""
    multipliers = bytearray(b"11")
    multipliers[position] = byte
    explain(b"\x02L\r1A" + bytes(multipliers) + b"00000000000000\rE\r")


# ======================== What one small input can multiply into ========================


def _peak_while(action: Callable[[], object]) -> int:
    tracemalloc.start()
    try:
        action()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


@pytest.mark.os_agnostic
def test_a_placeholder_repeated_with_a_large_value_is_refused_without_building_it() -> None:
    """1432 repeats of a 1 MiB value would be 1.4 GB, for a field that holds 4296 characters."""
    label = Label(elements=[Qr(x=1, y=1, data="{a}" * 1432)])
    record = {"a": "x" * (1024 * 1024)}

    def fill() -> None:
        with pytest.raises(ValidationError, match="at most 4296"):
            fill_label(label, record)

    assert _peak_while(fill) < 8 * 1024 * 1024


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("element", "limit"),
    [
        (Text(x=1, y=1, text="{a}"), MAX_FIELD_DATA),
        (ScalableText(x=1, y=1, text="{a}", height_points=10), MAX_FIELD_DATA),
        (Barcode(x=1, y=1, symbology=Symbology.PDF417, data="{a}"), MAX_PDF417_DATA),
        (Qr(x=1, y=1, data="{a}"), MAX_QR_DATA),
    ],
    ids=lambda value: type(value).__name__ if not isinstance(value, int) else str(value),
)
def test_filling_stops_at_each_field_s_own_limit_and_no_earlier(element: Element, limit: int) -> None:
    """A value exactly at the limit is kept whole; one past it is refused by the model, as ever."""
    label = Label(elements=[element])
    filled = fill_label(label, {"a": "7" * limit}).elements[0]
    assert (filled.data if isinstance(filled, (Barcode, Qr)) else cast("Text", filled).text) == "7" * limit
    with pytest.raises(ValidationError, match=f"at most {limit} "):
        fill_label(label, {"a": "7" * (limit + 1)})


@pytest.mark.os_agnostic
def test_a_missing_field_past_the_limit_is_still_the_one_reported() -> None:
    """A missing field comes ahead of a too-long one, whichever stands first in the text."""
    with pytest.raises(MissingFieldError, match="later"):
        fill_label(Label(elements=[Text(x=1, y=1, text="{a}{later}")]), {"a": "x" * 1000})


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "text", ["plain", "{a}", "x{a}y{{z}}{a}", "{{", "}}", "{}", "{a", "{{{a}}}"], ids=lambda text: text
)
def test_a_limit_only_ever_cuts_text_past_it(text: str) -> None:
    """Short of the limit the text is the same with and without it."""
    record = {"a": "VALUE"}
    assert fill_text(text, record, limit=100) == fill_text(text, record)


def _records_file(tmp_path: Path, name: str, count: int) -> str:
    path = tmp_path / name
    if name.endswith(".csv"):
        path.write_text("name\n" + "x\n" * count, encoding="utf-8")
    elif name.endswith(".jsonl"):
        path.write_text('{"name": "x"}\n' * count, encoding="utf-8")
    else:
        path.write_text(json.dumps([{"name": "x"}] * count), encoding="utf-8")
    return str(path)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", ["validate", "render", "print"])
@pytest.mark.parametrize("name", ["data.csv", "data.jsonl", "data.json"])
def test_a_batch_past_max_batch_records_is_refused_before_rendering(
    cli_runner: CliRunner, tmp_path: Path, command: str, name: str
) -> None:
    """Exit 2 naming the key; nothing was rendered, so nothing reached the printer file."""
    target = tmp_path / "job.dpl"
    printer = ["--printer", f"file:{target}"] if command == "print" else []
    args = ["--json-bare", "--set", "printer.max_batch_records=3", command, _write_label(tmp_path)]
    result = _invoke(cli_runner, *args, "--data", _records_file(tmp_path, name, 4), *printer)
    assert result.exit_code == 2, result.output
    document = json.loads(result.stdout)
    assert document["error"] == "LabelInputError"
    assert "more than 3 data records" in document["message"]
    assert "[printer] max_batch_records" in document["message"]
    assert not target.exists()


@pytest.mark.os_agnostic
def test_a_batch_at_max_batch_records_goes_through(cli_runner: CliRunner, tmp_path: Path) -> None:
    args = ["--json-bare", "--set", "printer.max_batch_records=3", "validate", _write_label(tmp_path)]
    result = _invoke(cli_runner, *args, "--data", _records_file(tmp_path, "data.csv", 3))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["ok"] is True


@pytest.mark.os_agnostic
def test_the_default_batch_limit_refuses_the_measured_input(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A million one-digit records, 2 MB: it took 96 s and 4.3 GB to render with no limit."""
    records = _records_file(tmp_path, "data.csv", 1_000_000)
    result = _invoke(cli_runner, "--json-bare", "validate", _write_label(tmp_path), "--data", records)
    assert result.exit_code == 2, result.output
    assert "more than 100000" in json.loads(result.stdout)["message"]


@pytest.mark.os_agnostic
def test_reading_a_csv_stops_just_past_the_limit(tmp_path: Path) -> None:
    assert len(load_records(_records_file(tmp_path, "data.csv", 50), limit=3)) == 4
    assert len(load_records(_records_file(tmp_path, "data.jsonl", 50), limit=3)) == 4
    assert len(load_records(_records_file(tmp_path, "data.csv", 50))) == 50


@pytest.mark.os_agnostic
@pytest.mark.parametrize("stream", [b"\r", b"\x01A"], ids=["empty-lines", "immediate"])
def test_explain_stops_at_its_command_limit_and_says_so(stream: bytes) -> None:
    """One byte of input can be one command of about 1 KB: 16 MiB came to 15 GB and 70 s."""
    result = explain(stream * (MAX_EXPLAINED_COMMANDS + 50))
    assert len(result.commands) == MAX_EXPLAINED_COMMANDS
    assert [problem for problem in result.problems if "not explained" in problem] == [
        f"{MAX_EXPLAINED_COMMANDS} commands, the rest not explained"
    ]
    assert result.label is None


@pytest.mark.os_agnostic
def test_explain_stops_inside_a_format_too_and_rebuilds_no_label_from_a_part() -> None:
    result = explain(b"\x02L\r" + b"121100000500025Hi\r" * MAX_EXPLAINED_COMMANDS + b"E\r")
    assert len(result.commands) == MAX_EXPLAINED_COMMANDS
    assert result.label is None
    assert any("not explained" in problem for problem in result.problems)


@pytest.mark.os_agnostic
def test_explain_of_a_stream_at_its_limit_is_whole() -> None:
    result = explain(b"\x01A" * MAX_EXPLAINED_COMMANDS)
    assert len(result.commands) == MAX_EXPLAINED_COMMANDS
    assert result.problems == ()


def _boxes(count: int) -> Label:
    elements: list[Element] = [Box(x=1, y=1, width=1, height=1, thickness=0.2) for _ in range(count)]
    return Label(width=20, height=20, elements=elements)


@pytest.mark.os_agnostic
def test_a_preview_keeps_to_the_model_s_field_limit() -> None:
    """Render stops such a label at the limit; drawing it anyway took 14.6 s for 5000 boxes."""
    profile = get_profile("dmx-600")
    with pytest.raises(PreviewTooLongError, match=f"takes at most {profile.max_fields}"):
        preview_bitmap(_boxes(profile.max_fields + 1), profile)


@pytest.mark.os_agnostic
def test_a_preview_at_the_model_s_field_limit_is_drawn() -> None:
    profile = get_profile("allegro-2")
    assert preview_bitmap(_boxes(profile.max_fields), profile).width > 0


@pytest.mark.os_agnostic
def test_a_merged_group_counts_as_the_one_field_it_is_sent_as() -> None:
    """A transparent label sends a group as one image, so past the limit in elements it still fits."""
    profile = get_profile("allegro-2")
    count = profile.max_fields + 1
    sent = SentAs(merged=(tuple(range(count)),))
    assert preview_bitmap(_boxes(count), profile, sent_as=sent).width > 0


@pytest.mark.os_agnostic
def test_the_preview_command_refuses_too_many_fields_with_an_envelope(cli_runner: CliRunner, tmp_path: Path) -> None:
    label = {"elements": [{"type": "box", "x": 1, "y": 1, "width": 1, "height": 1, "thickness": 0.2}] * 401}
    out = tmp_path / "p.png"
    args = ["--json-bare", "preview", _write_label(tmp_path, label), "-o", str(out), "--model", "dmx-600"]
    result = _invoke(cli_runner, *args)
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["error"] == "PreviewTooLongError"
    assert not out.exists()
