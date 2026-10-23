"""The label and printer commands end to end: argv in, stdout/stderr/exit code and printer bytes out.

Every printer command runs against the recording printer and the fake camera the testing
composition wires, so the bytes a real printer would receive are asserted exactly.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

import pytest

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory import FakeCamera, RecordingPrinter
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.render import render
from datamax_dpl.domain.render_context import RenderOptions

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


def factory(printer: RecordingPrinter | None = None, camera: FakeCamera | None = None) -> Callable[[], AppServices]:
    return lambda: build_testing(printer=printer, camera=camera)


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
    assert printer.data == render(Label.model_validate(SIMPLE), RenderOptions(model="dmx-600")).data
    assert envelope(result)["data"]["bytes_sent"] == len(printer.data)


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
def test_schema_describes_the_label_document(cli_runner: CliRunner) -> None:
    schema = envelope(run(cli_runner, ["--json", "schema"]))["data"]["label_schema"]
    assert "elements" in schema["properties"]
    assert "Barcode" in schema["$defs"]


@pytest.mark.os_agnostic
def test_capabilities_name_firmware_gates(cli_runner: CliRunner) -> None:
    features = envelope(run(cli_runner, ["--json", "capabilities", "dmx-400"]))["data"]["features"]
    assert features["circle_polygon"] == "fw 2.01"
    assert features["mirror"] == "no"


@pytest.mark.os_agnostic
def test_preview_writes_a_png(cli_runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "p.png"
    result = run(cli_runner, ["preview", write_json(tmp_path / "l.json", SIMPLE), "-o", str(out), "--model", "dmx-600"])
    assert result.exit_code == 0, result.output
    assert out.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.os_agnostic
def test_capture_takes_one_photo(cli_runner: CliRunner, tmp_path: Path) -> None:
    camera = FakeCamera()
    result = run(cli_runner, ["--json", "capture", "--output-dir", str(tmp_path)], factory(camera=camera))
    assert result.exit_code == 0, result.output
    assert camera.calls == 1
    assert (tmp_path / envelope(result)["data"]["path"]).exists()


@pytest.mark.os_agnostic
def test_verify_prints_waits_photographs_and_writes_the_expected_preview(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = RecordingPrinter(answers={b"\x01A": IDLE})
    camera = FakeCamera()
    args = [
        "--json",
        "verify",
        write_json(tmp_path / "l.json", SIMPLE),
        "--printer",
        PRINTER,
        "--output-dir",
        str(tmp_path),
    ]
    result = run(cli_runner, args, factory(printer, camera))
    assert result.exit_code == 0, result.output
    data = envelope(result)["data"]
    assert printer.data.startswith(render(Label.model_validate(SIMPLE), RenderOptions()).data)
    assert data["status"]["busy_printing"] is False
    assert camera.calls == 1
    assert (tmp_path / data["expected"]).read_bytes().startswith(b"\x89PNG")
