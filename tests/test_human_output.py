"""What a person reads: the findings of a job, and hostile bytes that stay off the terminal."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory import FakeCamera, RecordingPrinter, open_recording_printer
from datamax_dpl.application.upload_check import CheckedJob, UploadCheck, print_checked
from datamax_dpl.application.use_cases import AnswerWait, print_result, record_renders, render_batch
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.enums import FallbackPolicy, FindingCode, MemoryKind
from datamax_dpl.domain.errors import LabelRejectedError
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.printable import printable
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.status import MEMORY_DIRECTORY_REQUESTS

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner, Result

#: The Allegro has no circle record: a warning with its fallback, and an error finding under strict.
CIRCLE = {"elements": [{"type": "circle", "x": 20, "y": 20, "radius": 5}]}
ALLEGRO = ["--model", "allegro"]
STRICT = [*ALLEGRO, "--policy", "strict"]
WARNED = "circle_rasterized"
#: Set the window title; a cursor sequence would overwrite the line that says a label was refused.
OSC = "\x1b]0;PWN\x07"
SHOWN = "\\x1b]0;PWN\\x07"


def _label(tmp_path: Path, document: object = CIRCLE) -> str:
    path = tmp_path / "label.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def _run(runner: CliRunner, *args: str, printer: RecordingPrinter | None = None) -> Result:
    return runner.invoke(cli_mod.cli, list(args), obj=lambda: build_testing(printer=printer, camera=FakeCamera()))


# ======================== Findings in human mode ========================


@pytest.mark.os_agnostic
def test_print_refused_says_why(cli_runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "job.dpl"
    # Every printer URI opens this recording printer, so the file: target is never written either way.
    printer = RecordingPrinter()
    result = _run(cli_runner, "print", _label(tmp_path), *STRICT, "--printer", f"file:{target}", printer=printer)
    assert result.exit_code == 1
    assert "REFUSED: nothing sent" in result.stdout
    assert FindingCode.CIRCLE_RASTERIZED.value in result.stdout
    assert printer.sent == []


@pytest.mark.os_agnostic
def test_print_refused_by_a_job_on_stdout_says_why_too(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = _run(cli_runner, "print", _label(tmp_path), *STRICT, "--printer", "-")
    assert result.exit_code == 1
    assert WARNED in result.stderr or WARNED in result.stdout


@pytest.mark.os_agnostic
def test_print_that_took_a_fallback_says_so(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = _run(cli_runner, "print", _label(tmp_path), *ALLEGRO, "--printer", f"file:{tmp_path / 'job.dpl'}")
    assert result.exit_code == 0, result.output
    assert "sent " in result.stdout
    assert WARNED in result.stdout


@pytest.mark.os_agnostic
def test_print_with_a_job_on_stdout_keeps_its_findings_off_stdout(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = _run(cli_runner, "print", _label(tmp_path), *ALLEGRO, "--printer", "-")
    assert result.exit_code == 0, result.output
    assert WARNED in result.stderr
    assert WARNED not in result.stdout


@pytest.mark.os_agnostic
def test_verify_refused_says_why(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = ["--printer", f"file:{tmp_path / 'job.dpl'}"]
    result = _run(cli_runner, "--set", "camera.kind=file", "verify", _label(tmp_path), *STRICT, *printer)
    assert result.exit_code == 1
    assert "REFUSED" in result.stdout
    assert WARNED in result.stdout


@pytest.mark.os_agnostic
def test_render_to_stdout_refused_says_why_on_stderr(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = _run(cli_runner, "render", _label(tmp_path), *STRICT, "-o", "-")
    assert result.exit_code == 1
    assert result.stdout == ""
    assert WARNED in result.stderr


@pytest.mark.os_agnostic
def test_render_to_stdout_with_a_warning_keeps_the_job_clean(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = _run(cli_runner, "render", _label(tmp_path), *ALLEGRO, "-o", "-")
    assert result.exit_code == 0, result.output
    assert WARNED not in result.stdout
    assert WARNED in result.stderr


@pytest.mark.os_agnostic
@pytest.mark.parametrize("arguments", [["print", "--printer", "file:{dir}/x"], ["render", "-o", "{dir}/x"]])
def test_machine_mode_keeps_its_findings_in_the_envelope(
    cli_runner: CliRunner, tmp_path: Path, arguments: list[str]
) -> None:
    command, *rest = [word.replace("{dir}", str(tmp_path)) for word in arguments]
    result = _run(cli_runner, "--json-bare", command, _label(tmp_path), *ALLEGRO, *rest)
    assert result.exit_code == 0, result.output
    assert WARNED in {finding["code"] for finding in json.loads(result.stdout)["data"]["findings"]}
    assert WARNED not in result.stderr


# ======================== A refusal carries its findings ========================


def _strict() -> tuple[Label, RenderOptions]:
    return Label.model_validate(CIRCLE), RenderOptions(model="allegro", policy=FallbackPolicy.STRICT)


@pytest.mark.os_agnostic
def test_print_checked_refused_carries_the_findings() -> None:
    label, options = _strict()
    printer = RecordingPrinter()
    job = CheckedJob(renders=record_renders(label, None, options), check=UploadCheck(wait=AnswerWait(seconds=1.0)))
    with pytest.raises(LabelRejectedError) as refused:
        print_checked(
            build_job(label, options), job, uri="x", timeout=1.0, open_printer=open_recording_printer(printer)
        )
    assert [finding.code for finding in refused.value.findings] == [FindingCode.CIRCLE_RASTERIZED]
    assert printer.sent == []


@pytest.mark.os_agnostic
def test_print_result_refused_carries_a_batch_s_findings() -> None:
    label, options = _strict()
    batch = render_batch(label, [{}, {}], options)
    printer = RecordingPrinter()
    with pytest.raises(LabelRejectedError) as refused:
        print_result(batch, uri="x", timeout=1.0, open_printer=open_recording_printer(printer))
    assert len(refused.value.findings) == 2


# ======================== Hostile bytes ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("text", "shown"),
    [
        (OSC, SHOWN),
        ("a\rb", "a\\x0db"),
        (chr(0x9B) + "2J", "\\x9b2J"),
        (chr(0x7F), "\\x7f"),
        (chr(0) + chr(0x1F), "\\x00\\x1f"),
        ("line\n\tnext " + chr(0xE4) + chr(0x2713), "line\n\tnext " + chr(0xE4) + chr(0x2713)),
    ],
    ids=repr,
)
def test_control_characters_become_visible_and_text_stays(text: str, shown: str) -> None:
    assert printable(text) == shown
    assert printable(shown) == shown


@pytest.mark.os_agnostic
def test_an_unknown_key_in_a_label_stays_off_the_terminal(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = _run(cli_runner, "validate", _label(tmp_path, {"elements": [], OSC: 1}), *ALLEGRO)
    assert result.exit_code == 2
    assert OSC not in result.stderr
    assert SHOWN in result.stderr


@pytest.mark.os_agnostic
def test_a_missing_file_named_so_stays_off_the_terminal(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = _run(cli_runner, "validate", str(tmp_path / f"missing{OSC}.json"), *ALLEGRO)
    assert result.exit_code == 2
    assert OSC not in result.stderr
    assert SHOWN in result.stderr


@pytest.mark.os_agnostic
def test_a_name_a_printer_lists_stays_off_the_terminal(cli_runner: CliRunner) -> None:
    listing = f"MODULE: A\rNAME{OSC}\rAVAILABLE BYTES IN MODULE : 00049083\r".encode("ascii")
    answers = {MEMORY_DIRECTORY_REQUESTS[MemoryKind.GRAPHIC]: listing}
    result = _run(cli_runner, "memory", "--printer", "tcp://192.0.2.60:9100", printer=RecordingPrinter(answers=answers))
    assert result.exit_code == 0, result.output
    assert OSC not in result.stdout
    assert f"NAME{SHOWN}" in result.stdout
