"""The label and printer commands end to end: argv in, stdout/stderr/exit code and printer bytes out.

Every printer command runs against the recording printer the testing composition wires, so the
bytes a real printer would receive are asserted exactly.
"""

from __future__ import annotations

import base64
import dataclasses
import importlib.util
import json
from typing import TYPE_CHECKING, Any

import click
import pytest
from lib_layered_config import Config

import datamax_dpl
from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.commands.printer import KindChoice
from datamax_dpl.adapters.cli.envelope import CommandName
from datamax_dpl.adapters.memory import PrinterOpen, RecordingPrinter
from datamax_dpl.adapters.preview import render_preview_png
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.enums import MemoryKind
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.render import render
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.status import MEMORY_DIRECTORY_REQUESTS, STATUS_REQUEST

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner, Result

    from datamax_dpl.composition import AppServices

SIMPLE = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "Hello"}]}
CIRCLE = {"elements": [{"type": "circle", "x": 20, "y": 20, "radius": 5}]}
IDLE = b"NNNNNNNN\r"
PAPER_OUT = b"NYNNNNNN\r"
PRINTER = "tcp://192.0.2.60:9100"


def write_json(path: Path, document: object) -> str:
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def factory(printer: RecordingPrinter | None = None) -> Callable[[], AppServices]:
    return lambda: build_testing(printer=printer)


def run(runner: CliRunner, args: list[str], services: Callable[[], AppServices] | None = None) -> Result:
    return runner.invoke(cli_mod.cli, args, obj=services or factory())


def envelope(result: Result) -> dict[str, Any]:
    return json.loads(result.stdout)


@pytest.mark.os_agnostic
def test_render_shows_the_dpl_readably(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = run(cli_runner, ["render", write_json(tmp_path / "l.json", SIMPLE), "--model", "dmx-600"])
    assert result.exit_code == 0, result.output
    assert "<STX>L<CR>" in result.stdout
    assert "Hello<CR>" in result.stdout


@pytest.mark.os_agnostic
def test_render_json_carries_the_exact_bytes(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = run(cli_runner, ["--json", "render", write_json(tmp_path / "l.json", SIMPLE), "--model", "dmx-600"])
    document = envelope(result)
    expected = render(Label.model_validate(SIMPLE), RenderOptions(model="dmx-600")).data
    assert (document["ok"], document["command"], document["skipped"]) == (True, "render", [])
    assert base64.b64decode(document["data"]["dpl_base64"]) == expected
    assert document["data"]["model"] == "dmx-600"


@pytest.mark.os_agnostic
def test_json_bare_is_one_line(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = run(cli_runner, ["--json-bare", "models"])
    assert result.exit_code == 0
    assert result.stdout.count("\n") == 1
    assert {m["id"] for m in envelope(result)["data"]["models"]} >= {"generic", "dmx-600", "prodigy"}


@pytest.mark.os_agnostic
def test_render_to_a_file_writes_the_raw_job(cli_runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "job.dpl"
    result = run(cli_runner, ["render", write_json(tmp_path / "l.json", SIMPLE), "-o", str(out), "--model", "dmx-600"])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == render(Label.model_validate(SIMPLE), RenderOptions(model="dmx-600")).data


@pytest.mark.os_agnostic
def test_strict_policy_refuses_with_exit_1_and_ok_false(cli_runner: CliRunner, tmp_path: Path) -> None:
    args = ["--json", "render", write_json(tmp_path / "c.json", CIRCLE), "--model", "allegro", "--policy", "strict"]
    result = run(cli_runner, args)
    assert result.exit_code == 1
    document = envelope(result)
    assert document["ok"] is False
    assert {f["code"] for f in document["data"]["findings"]} >= {"circle_rasterized"}


@pytest.mark.os_agnostic
def test_invalid_json_is_an_error_envelope_with_exit_2(cli_runner: CliRunner, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    result = run(cli_runner, ["--json", "validate", str(bad)])
    assert result.exit_code == 2
    assert envelope(result)["error"] == "LabelInputError"
    assert "Error:" in result.stderr


@pytest.mark.os_agnostic
def test_an_invalid_label_names_the_field(cli_runner: CliRunner, tmp_path: Path) -> None:
    label = {"elements": [{"type": "text", "x": -1, "y": 5, "text": "X"}]}
    result = run(cli_runner, ["--json", "validate", write_json(tmp_path / "l.json", label)])
    assert result.exit_code == 2
    assert "elements.0.text.x" in envelope(result)["message"]


@pytest.mark.os_agnostic
def test_a_stop_position_word_other_than_printer_is_refused_by_validate_with_the_field_named(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    label = {**SIMPLE, "stop_position": "paper"}
    result = run(cli_runner, ["--json", "validate", write_json(tmp_path / "l.json", label)])
    assert result.exit_code == 2
    message = envelope(result)["message"]
    assert message.endswith("is not a valid label: stop_position: Input should be 'printer'")


@pytest.mark.os_agnostic
def test_an_unknown_model_is_an_error(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = run(cli_runner, ["--json", "validate", write_json(tmp_path / "l.json", SIMPLE), "--model", "nope"])
    assert result.exit_code == 2
    assert envelope(result)["error"] == "UnknownModelError"


@pytest.mark.os_agnostic
def test_print_sends_exactly_the_rendered_job(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = RecordingPrinter()
    args = ["--json", "print", write_json(tmp_path / "l.json", SIMPLE), "--model", "dmx-600", "--printer", PRINTER]
    result = run(cli_runner, args, factory(printer))
    assert result.exit_code == 0, result.output
    job = render(Label.model_validate(SIMPLE), RenderOptions(model="dmx-600")).data
    assert printer.data == STATUS_REQUEST + job
    assert envelope(result)["data"]["bytes_sent"] == len(job)


@pytest.mark.os_agnostic
def test_print_with_data_records_sends_one_job_per_record(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = RecordingPrinter()
    label = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "Hi {name}"}]}
    data = tmp_path / "people.csv"
    data.write_text("name\nAnna\nBen\n", encoding="utf-8")
    args = ["print", write_json(tmp_path / "l.json", label), "--data", str(data), "--printer", PRINTER]
    result = run(cli_runner, args, factory(printer))
    assert result.exit_code == 0, result.output
    assert b"Hi Anna\r" in printer.data and b"Hi Ben\r" in printer.data
    assert printer.data.count(b"\x02L\r") == 2


@pytest.mark.os_agnostic
def test_print_sends_nothing_for_a_refused_label(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = RecordingPrinter()
    args = ["print", write_json(tmp_path / "c.json", CIRCLE), "--model", "allegro", "--policy", "strict"]
    result = run(cli_runner, [*args, "--printer", PRINTER], factory(printer))
    assert result.exit_code == 1
    assert printer.data == b""


@pytest.mark.os_agnostic
def test_print_without_a_printer_is_a_configuration_error(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = run(cli_runner, ["--json", "print", write_json(tmp_path / "l.json", SIMPLE)])
    assert result.exit_code == 2
    assert envelope(result)["error"] == "ConfigurationError"


@pytest.mark.os_agnostic
def test_a_malformed_printer_uri_is_refused_before_anything_is_sent(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = RecordingPrinter()
    args = ["--json", "print", write_json(tmp_path / "l.json", SIMPLE), "--printer", "tcp://192.0.2.60:port"]
    result = run(cli_runner, args, factory(printer))
    assert result.exit_code == 2
    document = envelope(result)
    assert document["error"] == "ConfigurationError"
    assert "printer URI 'tcp://192.0.2.60:port'" in document["message"]
    assert printer.data == b""


@pytest.mark.os_agnostic
def test_status_reports_idle_and_problems(cli_runner: CliRunner) -> None:
    idle = run(
        cli_runner, ["--json", "status", "--printer", PRINTER], factory(RecordingPrinter(answers={b"\x01A": IDLE}))
    )
    assert idle.exit_code == 0
    assert envelope(idle)["data"]["idle"] is True
    out = run(
        cli_runner, ["--json", "status", "--printer", PRINTER], factory(RecordingPrinter(answers={b"\x01A": PAPER_OUT}))
    )
    assert out.exit_code == 1
    assert envelope(out)["data"]["problems"] == ["paper_out"]


@pytest.mark.os_agnostic
def test_status_lists_every_flag_in_the_printer_order_in_both_outputs(cli_runner: CliRunner) -> None:
    def paper_out() -> Callable[[], AppServices]:
        return factory(RecordingPrinter(answers={b"\x01A": PAPER_OUT}))

    human = run(cli_runner, ["status", "--printer", PRINTER], paper_out())
    assert human.stdout.splitlines() == [
        "interpreter_busy no",
        "paper_out        yes",
        "ribbon_out       no",
        "printing_batch   no",
        "busy_printing    no",
        "paused           no",
        "label_presented  no",
    ]
    flags = envelope(run(cli_runner, ["--json", "status", "--printer", PRINTER], paper_out()))["data"]["flags"]
    assert list(flags.items()) == [
        ("interpreter_busy", False),
        ("paper_out", True),
        ("ribbon_out", False),
        ("printing_batch", False),
        ("busy_printing", False),
        ("paused", False),
        ("label_presented", False),
        ("reset_reported", False),
    ]


@pytest.mark.os_agnostic
def test_status_on_a_one_way_channel_is_an_error(cli_runner: CliRunner) -> None:
    result = run(cli_runner, ["--json", "status", "--printer", PRINTER], factory(RecordingPrinter(bidirectional=False)))
    assert result.exit_code == 2
    assert envelope(result)["error"] == "UnsupportedOperationError"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("command", "expected"),
    [("cut", b"\x02o"), ("feed", b"\x02F"), ("reset", b"\x01#"), ("cancel", b"\x01C"), ("pause", b"\x01B")],
)
def test_single_commands_send_their_bytes(cli_runner: CliRunner, command: str, expected: bytes) -> None:
    printer = RecordingPrinter()
    result = run(cli_runner, [command, "--printer", PRINTER], factory(printer))
    assert result.exit_code == 0, result.output
    assert printer.data == expected


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("media", "sensor"), [("gap", b"\x02e"), ("reflective", b"\x02r")])
def test_feed_with_media_leaves_continuous_mode_then_feeds_to_the_stop(
    cli_runner: CliRunner, media: str, sensor: bytes
) -> None:
    printer = RecordingPrinter()
    args = ["--json", "--set", "printer.model=allegro-2", "feed", "--media", media, "--printer", PRINTER]

    result = run(cli_runner, args, factory(printer))

    assert result.exit_code == 0, result.output
    assert printer.data == b"\x02n\x02c0000" + sensor + b"\x02f263\x02F"
    data = envelope(result)["data"]
    assert data["media"] == media
    assert data["bytes_sent"] == len(printer.data)
    assert "model_defaults_applied" in [finding["code"] for finding in data["findings"]]


@pytest.mark.os_agnostic
def test_feed_with_media_follows_the_configured_stop_position(cli_runner: CliRunner) -> None:
    printer = RecordingPrinter()
    args = ["--set", "printer.model=allegro-2", "--set", "printer.stop_position=50.8"]

    result = run(cli_runner, [*args, "feed", "--media", "gap", "--printer", PRINTER], factory(printer))

    assert result.exit_code == 0, result.output
    assert printer.data == b"\x02n\x02c0000\x02e\x02f200\x02F"


@pytest.mark.os_agnostic
def test_feed_does_not_take_continuous_media(cli_runner: CliRunner) -> None:
    printer = RecordingPrinter()

    result = run(cli_runner, ["feed", "--media", "continuous", "--printer", PRINTER], factory(printer))

    assert result.exit_code == 2
    assert printer.data == b""


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", ["cut", "feed", "reset", "cancel", "pause"])
def test_a_single_commands_envelope_names_the_command_as_it_was_typed(cli_runner: CliRunner, command: str) -> None:
    result = run(cli_runner, ["--json-bare", command, "--printer", PRINTER], factory(RecordingPrinter()))
    assert result.stdout.startswith(f'{{"ok":true,"command":"{command}",'), result.output


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", list(CommandName), ids=lambda name: name.value)
def test_every_envelope_command_name_is_a_command_the_cli_has(name: CommandName) -> None:
    command: click.Command = cli_mod.cli
    for word in name.value.split(" "):
        assert isinstance(command, click.Group)
        command = command.commands[word]


@pytest.mark.os_agnostic
def test_detect_narrows_the_models_from_the_answers(cli_runner: CliRunner) -> None:
    memory = (
        b"INTERNAL MEMORY\rVER: DA-01.01 12/21/97\rINSTALLED: 0016\rAVAILABLE: 0010\rMODULE: D:0032\r"
        b"SCALABLE: 0025\rLABEL MEM: 0004\rLABEL SIZE: 0640:0640:IN\r"
    )
    answers = {b"\x02k": b"Y", b"\x02v": b"VER DA - 01.01 12/21/97\r", b"\x02KQ": memory}
    result = run(cli_runner, ["--json", "detect", "--printer", PRINTER], factory(RecordingPrinter(answers=answers)))
    assert result.exit_code == 0, result.output
    assert envelope(result)["data"]["detection"]["candidates"] == ["dmx-600"]


@pytest.mark.os_agnostic
def test_explain_reads_a_dpl_file(cli_runner: CliRunner, tmp_path: Path) -> None:
    job = tmp_path / "job.dpl"
    job.write_bytes(b"\x02L\r121100000500025Hi\rE\r")
    result = run(cli_runner, ["--json", "explain", str(job)])
    assert result.exit_code == 0
    assert envelope(result)["data"]["label"]["elements"][0]["text"] == "Hi"


@pytest.mark.os_agnostic
def test_explain_reports_an_unreadable_setting_as_a_problem_and_exits_1(cli_runner: CliRunner, tmp_path: Path) -> None:
    job = tmp_path / "job.dpl"
    job.write_bytes(b"\x02L\rQab12\r121100000500025Hi\rE\r")
    result = run(cli_runner, ["--json", "explain", str(job)])
    document = envelope(result)
    assert (result.exit_code, document["ok"]) == (1, False), result.output
    assert document["data"]["problems"] == ["Qab12: setting not understood"]
    assert document["data"]["label"]["elements"][0]["text"] == "Hi"


@pytest.mark.os_agnostic
def test_schema_describes_the_label_document(cli_runner: CliRunner) -> None:
    schema = envelope(run(cli_runner, ["--json", "schema"]))["data"]["label_schema"]
    assert "elements" in schema["properties"]
    assert "Barcode" in schema["$defs"]


@pytest.mark.os_agnostic
def test_capabilities_name_firmware_gates(cli_runner: CliRunner) -> None:
    features = envelope(run(cli_runner, ["--json", "capabilities", "dmx-400"]))["data"]["features"]
    assert features["circle_polygon"] == {
        "available": True,
        "min_firmware": {"major": 2, "minor": 1},
        "option": None,
        "source": "manual",
    }
    assert features["datamatrix"] == {
        "available": True,
        "min_firmware": None,
        "option": "datamatrix",
        "source": "manual",
    }
    assert features["mirror"] == {"available": False, "min_firmware": None, "option": None, "source": "manual"}


@pytest.mark.os_agnostic
def test_capabilities_give_each_speed_range_as_its_letters(cli_runner: CliRunner) -> None:
    # A script reads the range and the default as fields, not by parsing "C-G (default G)".
    data = envelope(run(cli_runner, ["--json", "capabilities", "dmx-400"]))["data"]
    speeds = {key: data[key] for key in ("print_speed", "slew_speed", "backup_speed")}
    assert speeds == {
        "print_speed": {"low": "C", "high": "G", "default": "G"},
        # The manual's range for the DMX 400 slew speed leaves out its own default.
        "slew_speed": {"low": "C", "high": "I", "default": "K"},
        "backup_speed": {"low": "C", "high": "I", "default": "E"},
    }
    assert envelope(run(cli_runner, ["--json", "capabilities", "prodigy"]))["data"]["backup_speed"] is None


@pytest.mark.os_agnostic
def test_capabilities_human_output_keeps_the_short_forms(cli_runner: CliRunner) -> None:
    lines = run(cli_runner, ["capabilities", "dmx-400"]).output.splitlines()
    # Feature rows are indented; a field and a feature can share a name (backup_speed).
    fields = {line.split()[0]: line.split(maxsplit=1)[1] for line in lines if not line.startswith(" ")}
    features = {line.split()[0]: line.split(maxsplit=1)[1] for line in lines if line.startswith(" ")}
    assert (fields["print_speed"], fields["backup_speed"]) == ("C-G (default G)", "C-I (default E)")
    assert (features["circle_polygon"], features["datamatrix"], features["mirror"]) == (
        "fw 2.01",
        "option datamatrix",
        "no",
    )


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("model", "module"), [("allegro-2", "A"), ("dmx-600", "E"), ("prodigy", None)])
def test_capabilities_name_the_models_image_module(cli_runner: CliRunner, model: str, module: str | None) -> None:
    data = envelope(run(cli_runner, ["--json", "capabilities", model]))["data"]
    assert data["image_module"] == module


@pytest.mark.os_agnostic
def test_capabilities_human_output_names_the_image_module(cli_runner: CliRunner) -> None:
    lines = run(cli_runner, ["capabilities", "dmx-600"]).output.splitlines()
    assert [line.split() for line in lines if line.startswith("image_module ")] == [["image_module", "E"]]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("model", "rows"), [("allegro-2", 1648), ("dmx-600", None)])
def test_capabilities_name_the_models_max_format_rows(cli_runner: CliRunner, model: str, rows: int | None) -> None:
    data = envelope(run(cli_runner, ["--json", "capabilities", model]))["data"]
    assert data["max_format_rows"] == rows


@pytest.mark.os_agnostic
def test_capabilities_human_output_names_the_max_format_rows(cli_runner: CliRunner) -> None:
    lines = run(cli_runner, ["capabilities", "allegro-2"]).output.splitlines()
    assert [line.split() for line in lines if line.startswith("max_format_rows ")] == [["max_format_rows", "1648"]]


@pytest.mark.os_agnostic
def test_capabilities_human_output_has_a_row_for_every_json_field(cli_runner: CliRunner) -> None:
    # The human rows are listed by hand; this keeps a field added to the payload from showing up
    # in --json only.
    data = envelope(run(cli_runner, ["--json", "capabilities", "dmx-400"]))["data"]
    lines = run(cli_runner, ["capabilities", "dmx-400"]).output.splitlines()
    fields = [key for key in data if key != "features"]
    assert [line.split()[0] for line in lines[: len(fields)]] == fields
    assert [line.split()[0] for line in lines[len(fields) :]] == list(data["features"])


@pytest.mark.os_agnostic
def test_preview_writes_a_png(cli_runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "p.png"
    result = run(cli_runner, ["preview", write_json(tmp_path / "l.json", SIMPLE), "-o", str(out), "--model", "dmx-600"])
    assert result.exit_code == 0, result.output
    assert out.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


#: Pillow is the optional ``preview`` extra (Python 3.11+): an interpreter without it, such as the
#: Python 3.10 cell, previews text as grey cells.
PILLOW_MISSING = importlib.util.find_spec("PIL") is None
LINE_ONLY = {"elements": [{"type": "line", "x": 5, "y": 5, "x2": 30, "y2": 5, "thickness": 0.5}]}


def preview_envelope(cli_runner: CliRunner, tmp_path: Path, label: object, *mode: str) -> Result:
    """Run ``preview`` on ``label`` for the Allegro 2 in the given output mode."""
    source = write_json(tmp_path / "l.json", label)
    return run(cli_runner, [*mode, "preview", source, "-o", str(tmp_path / "p.png"), "--model", "allegro-2"])


@pytest.mark.os_agnostic
@pytest.mark.skipif(not PILLOW_MISSING, reason="needs an interpreter without Pillow, such as the Python 3.10 cell")
def test_preview_without_pillow_says_its_text_is_approximated(cli_runner: CliRunner, tmp_path: Path) -> None:
    data = envelope(preview_envelope(cli_runner, tmp_path, SIMPLE, "--json"))["data"]
    assert data["lettered"] is False
    assert [finding["code"] for finding in data["findings"]] == ["preview_text_approximated"]
    assert "preview_text_approximated" in preview_envelope(cli_runner, tmp_path, SIMPLE).output


@pytest.mark.os_agnostic
def test_preview_with_pillow_reports_nothing_about_its_text(cli_runner: CliRunner, tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    data = envelope(preview_envelope(cli_runner, tmp_path, SIMPLE, "--json"))["data"]
    assert data["lettered"] is True
    assert data["findings"] == []


@pytest.mark.os_agnostic
def test_preview_of_a_label_without_text_reports_nothing_about_text(cli_runner: CliRunner, tmp_path: Path) -> None:
    assert envelope(preview_envelope(cli_runner, tmp_path, LINE_ONLY, "--json"))["data"]["findings"] == []


@pytest.mark.os_agnostic
def test_the_package_root_says_whether_the_preview_is_lettered() -> None:
    assert datamax_dpl.pillow_available() is not PILLOW_MISSING


@pytest.mark.os_agnostic
@pytest.mark.skipif(not PILLOW_MISSING, reason="needs an interpreter without Pillow, such as the Python 3.10 cell")
def test_the_package_root_reports_text_previewed_without_pillow_as_approximated() -> None:
    findings = datamax_dpl.preview_findings(Label.model_validate(SIMPLE))
    assert [(finding.code, finding.severity) for finding in findings] == [
        (datamax_dpl.FindingCode.PREVIEW_TEXT_APPROXIMATED, datamax_dpl.Severity.INFO)
    ]


@pytest.mark.os_agnostic
def test_the_package_root_reports_nothing_for_lettered_text() -> None:
    pytest.importorskip("PIL.Image")
    assert datamax_dpl.preview_findings(Label.model_validate(SIMPLE)) == []


#: An offset whose rounding moves SIMPLE's text two dots on the paper of a generic job: 5.2 mm is
#: written as 20/100 in, which prints at dot 41, and the 0.2 mm offset undone puts it at 39.
CALIBRATED = ["--set", "printer.offset_across=0.2"]


def simple_preview(options: RenderOptions | None = None) -> bytes:
    """The preview PNG of SIMPLE for the generic model, drawn for ``options``."""
    return render_preview_png(Label.model_validate(SIMPLE), get_profile("generic"), options=options)


@pytest.mark.os_agnostic
def test_preview_places_fields_where_the_configured_calibration_sends_them(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    out = tmp_path / "p.png"
    label = write_json(tmp_path / "l.json", SIMPLE)
    result = run(cli_runner, [*CALIBRATED, "preview", label, "-o", str(out), "--model", "generic"])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == simple_preview(RenderOptions(offset_across=0.2))
    assert out.read_bytes() != simple_preview()


@pytest.mark.os_agnostic
def test_status_skips_a_line_left_over_from_an_earlier_query(cli_runner: CliRunner) -> None:
    printer = RecordingPrinter(answers={b"\x01A": b"AVAILABLE BYTES IN MODULE : 00048631\r" + IDLE})
    status = run(cli_runner, ["--json", "status", "--printer", PRINTER], factory(printer))
    assert status.exit_code == 0, status.output
    assert envelope(status)["data"]["idle"] is True


@pytest.mark.os_agnostic
def test_a_batch_cuts_after_every_nth_record_and_at_the_end(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = RecordingPrinter()
    label = {"cut": {"every": 2}, "elements": [{"type": "text", "x": 5, "y": 5, "text": "{n}"}]}
    data = tmp_path / "n.csv"
    data.write_text("n\n1\n2\n3\n", encoding="utf-8")
    args = ["--json", "print", write_json(tmp_path / "l.json", label), "--data", str(data), "--model", "dmx-600"]
    result = run(cli_runner, [*args, "--cutter", "yes", "--printer", PRINTER], factory(printer))
    assert result.exit_code == 0, result.output
    # Every record's job starts in inch mode and ends with its clear of module E, after the
    # batch's cut where one follows it.
    jobs = printer.data.split(b"\x02n")[1:]
    assert [job.endswith(b"E\r\x02o\x02qE") for job in jobs] == [False, True, True]
    assert all(job.endswith(b"E\r\x02qE") for job in jobs[:1])
    assert "batch_cut_unverified" in {f["code"] for f in envelope(result)["data"]["findings"]}


@pytest.mark.os_agnostic
def test_a_batch_cut_on_a_model_without_cut_now_falls_back(cli_runner: CliRunner, tmp_path: Path) -> None:
    label = {"cut": {"every": 2}, "elements": [{"type": "text", "x": 5, "y": 5, "text": "{n}"}]}
    data = tmp_path / "n.csv"
    data.write_text("n\n1\n2\n3\n", encoding="utf-8")
    args = ["--json", "render", write_json(tmp_path / "l.json", label), "--data", str(data), "--model", "prodigy"]
    result = run(cli_runner, args)
    assert result.exit_code == 0, result.output
    assert "batch_cut_unsupported" in {f["code"] for f in envelope(result)["data"]["findings"]}
    assert b"\x02o" not in base64.b64decode(envelope(result)["data"]["dpl_base64"])


@pytest.mark.os_agnostic
def test_firmware_from_an_unquoted_setting_is_accepted(cli_runner: CliRunner, tmp_path: Path) -> None:
    circle = write_json(tmp_path / "c.json", CIRCLE)
    result = run(cli_runner, ["--json", "--set", "printer.firmware=2.01", "validate", circle, "--model", "dmx-400"])
    assert result.exit_code == 0, result.output
    assert "circle_rasterized" not in {f["code"] for f in envelope(result)["data"]["findings"]}


@pytest.mark.os_agnostic
def test_an_unknown_model_message_is_not_quoted_twice(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = run(cli_runner, ["--json", "validate", write_json(tmp_path / "l.json", SIMPLE), "--model", "nope"])
    assert envelope(result)["message"].startswith("unknown printer model 'nope'")


@pytest.mark.os_agnostic
def test_escaped_braces_in_a_batch_are_not_reported_as_placeholders(cli_runner: CliRunner, tmp_path: Path) -> None:
    label = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "{n} {{b}}"}]}
    data = tmp_path / "n.csv"
    data.write_text("n\n1\n", encoding="utf-8")
    result = run(cli_runner, ["--json", "render", write_json(tmp_path / "l.json", label), "--data", str(data)])
    assert result.exit_code == 0, result.output
    assert "placeholders_unfilled" not in {f["code"] for f in envelope(result)["data"]["findings"]}


#: The manual's sample answer to ``<STX>WG`` (see tests/test_memory.py).
DIRECTORY = b"MODULE: A\rAVAILABLE BYTES IN MODULE:00049083\rMODULE: B\rLOGO\rCAM\rAVAILABLE BYTES IN MODULE:00257919\r"


@pytest.mark.os_agnostic
def test_memory_prints_the_parsed_directory_envelope(cli_runner: CliRunner) -> None:
    printer = RecordingPrinter(answers={b"\x02WG": DIRECTORY})
    result = run(cli_runner, ["--json", "memory", "--kind", "graphic", "--printer", PRINTER], factory(printer))
    assert result.exit_code == 0, result.output
    document = envelope(result)
    assert (document["ok"], document["command"]) == (True, "memory")
    assert document["data"] == {
        "printer": PRINTER,
        "directories": [
            {
                "kind": "graphic",
                "parsed": True,
                "no_modules_available": False,
                "modules": [
                    {"module": "A", "names": [], "available_bytes": 49083},
                    {"module": "B", "names": ["LOGO", "CAM"], "available_bytes": 257919},
                ],
                "raw": DIRECTORY.decode("ascii"),
                "reset_reported": False,
            }
        ],
    }
    assert printer.queries == [b"\x02WG"]


@pytest.mark.os_agnostic
def test_memory_asks_for_graphics_by_default(cli_runner: CliRunner) -> None:
    printer = RecordingPrinter(answers={b"\x02WG": DIRECTORY})
    result = run(cli_runner, ["memory", "--printer", PRINTER], factory(printer))
    assert result.exit_code == 0, result.output
    assert printer.queries == [b"\x02WG"]


@pytest.mark.os_agnostic
def test_memory_kind_all_asks_fonts_graphics_and_formats(cli_runner: CliRunner) -> None:
    answers = {b"\x02WF": b"No Modules Available\r", b"\x02WG": DIRECTORY, b"\x02WL": b"MODULE: C\rSHIP\r"}
    printer = RecordingPrinter(answers=answers)
    result = run(cli_runner, ["--json", "memory", "--kind", "all", "--printer", PRINTER], factory(printer))
    assert result.exit_code == 0, result.output
    directories = envelope(result)["data"]["directories"]
    assert [d["kind"] for d in directories] == ["font", "graphic", "format"]
    assert [d["no_modules_available"] for d in directories] == [True, False, False]
    assert printer.queries == [b"\x02WF", b"\x02WG", b"\x02WL"]


@pytest.mark.os_agnostic
def test_every_kind_choice_names_the_kinds_it_asks_for_in_order() -> None:
    assert {choice: choice.kinds for choice in KindChoice} == {
        KindChoice.ALL: (MemoryKind.FONT, MemoryKind.GRAPHIC, MemoryKind.FORMAT),
        KindChoice.FONT: (MemoryKind.FONT,),
        KindChoice.GRAPHIC: (MemoryKind.GRAPHIC,),
        KindChoice.FORMAT: (MemoryKind.FORMAT,),
    }


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("word", "query"), [("font", b"\x02WF"), ("graphic", b"\x02WG"), ("format", b"\x02WL")])
def test_memory_takes_each_kind_by_its_word(cli_runner: CliRunner, word: str, query: bytes) -> None:
    """The lower-case word selects the kind; click matches an Enum on its name, so the words are given as text."""
    printer = RecordingPrinter(answers={query: DIRECTORY})
    result = run(cli_runner, ["memory", "--kind", word, "--printer", PRINTER], factory(printer))
    assert result.exit_code == 0, result.output
    assert printer.queries == [query]


@pytest.mark.os_agnostic
def test_memory_human_view_lists_modules_names_and_free_bytes(cli_runner: CliRunner) -> None:
    answers = {b"\x02WF": b"No Modules Available\r", b"\x02WG": DIRECTORY, b"\x02WL": b"?"}
    result = run(
        cli_runner, ["memory", "--kind", "all", "--printer", PRINTER], factory(RecordingPrinter(answers=answers))
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "font:",
        "  printer reports no modules available (the manual: corrupt or worn-out module)",
        "graphic:",
        "  module A: 49083 bytes free",
        "  module B: 257919 bytes free",
        "    LOGO",
        "    CAM",
        "format:",
        "  answer not understood: '?'",
    ]


@pytest.mark.os_agnostic
def test_memory_reports_an_answer_it_cannot_parse_with_exit_0(cli_runner: CliRunner) -> None:
    printer = RecordingPrinter(answers={b"\x02WG": b"VER DA - 01.01\r"})
    result = run(cli_runner, ["--json", "memory", "--printer", PRINTER], factory(printer))
    assert result.exit_code == 0, result.output
    directory = envelope(result)["data"]["directories"][0]
    assert (directory["parsed"], directory["modules"], directory["raw"]) == (False, [], "VER DA - 01.01\r")


@pytest.mark.os_agnostic
def test_memory_without_an_answer_is_a_timeout_with_exit_2(cli_runner: CliRunner) -> None:
    result = run(cli_runner, ["--json", "memory", "--printer", PRINTER], factory(RecordingPrinter()))
    assert result.exit_code == 2
    assert envelope(result)["error"] == "PrinterTimeoutError"


@pytest.mark.os_agnostic
def test_memory_on_a_one_way_channel_is_an_error(cli_runner: CliRunner) -> None:
    result = run(cli_runner, ["--json", "memory", "--printer", PRINTER], factory(RecordingPrinter(bidirectional=False)))
    assert result.exit_code == 2
    assert envelope(result)["error"] == "UnsupportedOperationError"


@pytest.mark.os_agnostic
def test_memory_refuses_an_unknown_kind_listing_the_choices(cli_runner: CliRunner) -> None:
    result = run(cli_runner, ["memory", "--kind", "logo", "--printer", PRINTER])
    assert result.exit_code == 2
    assert "'all', 'font', 'format', 'graphic'" in result.output


@pytest.mark.os_agnostic
def test_memory_reaches_a_printer_named_in_the_configuration(cli_runner: CliRunner) -> None:
    printer = RecordingPrinter(answers={b"\x02WG": DIRECTORY})
    config = Config({"printers": {"lager": {"model": "allegro-2", "uri": PRINTER, "timeout": 4.0}}}, {})

    def get_config(**_kwargs: object) -> Config:
        return config

    services = dataclasses.replace(build_testing(printer=printer), get_config=get_config)
    result = run(cli_runner, ["memory", "--printer", "lager"], lambda: services)
    assert result.exit_code == 0, result.output
    assert printer.opened == [PrinterOpen(uri=PRINTER, timeout=4.0)]


@pytest.mark.os_agnostic
def test_memory_refuses_a_bad_idle_before_opening_the_printer(cli_runner: CliRunner) -> None:
    printer = RecordingPrinter(answers={b"\x02WG": DIRECTORY})
    result = run(cli_runner, ["--json", "memory", "--idle", "0", "--printer", PRINTER], factory(printer))
    assert result.exit_code == 2
    assert "idle must be above 0" in envelope(result)["message"]
    assert printer.opened == []


@pytest.mark.os_agnostic
def test_memory_keeps_the_raw_text_of_a_listing_past_the_manuals_limits(cli_runner: CliRunner) -> None:
    answer = b"MODULE: Q\rAVAILABLE BYTES IN MODULE : 00048631\r"
    printer = RecordingPrinter(answers={b"\x02WG": answer})
    result = run(cli_runner, ["--json", "memory", "--printer", PRINTER], factory(printer))
    assert result.exit_code == 0, result.output
    directory = envelope(result)["data"]["directories"][0]
    assert (directory["parsed"], directory["raw"]) == (False, answer.decode("ascii"))


@pytest.mark.os_agnostic
def test_memory_closes_its_channel_when_a_later_directory_fails(cli_runner: CliRunner) -> None:
    """Only the first answer is waited on; the second kind fails on an open channel that must not leak."""
    printer = RecordingPrinter(answers={MEMORY_DIRECTORY_REQUESTS[MemoryKind.FONT]: DIRECTORY})
    result = run(cli_runner, ["--json", "memory", "--kind", "all", "--printer", PRINTER], factory(printer))
    assert result.exit_code == 2, result.output
    assert printer.queries[0] == MEMORY_DIRECTORY_REQUESTS[MemoryKind.FONT]
    assert len(printer.queries) > 1
    assert printer.closed


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "job", [b"\x02L\r1911A1000100010x\rZZZ\rE\r", b"\x02L\rgarbage!!\rE\r", b"\x02L\r1911\rE\r"], ids=repr
)
def test_explain_exits_1_for_a_format_line_it_cannot_read(cli_runner: CliRunner, tmp_path: Path, job: bytes) -> None:
    path = tmp_path / "job.dpl"
    path.write_bytes(job)
    result = run(cli_runner, ["--json", "explain", str(path)])
    document = envelope(result)
    assert (result.exit_code, document["ok"]) == (1, False), result.output
    assert len(document["data"]["problems"]) == 1


@pytest.mark.os_agnostic
def test_a_plain_feed_on_a_known_printer_with_a_cutter_turns_it_off_first(cli_runner: CliRunner) -> None:
    printer = RecordingPrinter()
    args = ["--json", "--set", "printer.model=allegro-2", "--set", "printer.cutter=yes", "feed", "--printer", PRINTER]

    result = run(cli_runner, args, factory(printer))

    assert result.exit_code == 0, result.output
    assert printer.data == b"\x02V0\x02F"
    assert envelope(result)["data"]["bytes_sent"] == len(printer.data)
