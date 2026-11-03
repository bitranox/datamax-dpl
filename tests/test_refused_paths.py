"""A refused job sends and writes nothing on every route, and each CLI error route has its envelope.

The ``print`` route for one label is in ``test_cli_commands`` and ``test_upload_check``; these are
the routes with their own guard: ``verify``, ``render --output``, a batch, ``print_result``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, NoReturn

import pytest

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory import FakeCamera, RecordingPrinter, open_recording_printer
from datamax_dpl.application.ports import PrinterEndpoint
from datamax_dpl.application.upload_check import CheckedJob, UploadCheck
from datamax_dpl.application.use_cases import AnswerWait, print_result, record_renders, render_batch
from datamax_dpl.application.verify import verify_print
from datamax_dpl.composition import build_production, build_testing
from datamax_dpl.domain.camera import CameraKind, CameraSettings
from datamax_dpl.domain.enums import FallbackPolicy, FindingCode
from datamax_dpl.domain.errors import ConfigurationError, LabelRejectedError
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.profiles import UnknownModelError
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner, Result

SIMPLE = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "Hello"}]}
NAMED = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "Hi {name}"}]}
#: The Allegro has no circle record: a fallback, and so an error finding under strict.
CIRCLE = {"elements": [{"type": "circle", "x": 20, "y": 20, "radius": 5}]}
EAN13 = {"elements": [{"type": "barcode", "x": 5, "y": 5, "symbology": "ean13", "data": "{code}"}]}
STRICT = ["--model", "allegro", "--policy", "strict"]
#: A real, valid EAN-13 and a value its check refuses: the batch is mixed, not wholly bad.
GOOD_THEN_BAD = "code\n4006381333931\nnotdigits\n"
PRINTER = "tcp://192.0.2.60:9100"


def _write(path: Path, document: object) -> str:
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def _run(runner: CliRunner, args: list[str], *, printer: RecordingPrinter | None = None) -> Result:
    return runner.invoke(cli_mod.cli, args, obj=lambda: build_testing(printer=printer, camera=FakeCamera()))


def _opener_that_fails(*_args: Any, **_kwargs: Any) -> NoReturn:
    """A refused job must not get as far as asking for a connection."""
    pytest.fail("the printer was opened for a refused job")


def _strict_circle() -> tuple[Label, RenderOptions]:
    return Label.model_validate(CIRCLE), RenderOptions(model="allegro", policy=FallbackPolicy.STRICT)


# ======================== verify ========================


@pytest.mark.os_agnostic
def test_verify_prints_nothing_for_a_refused_label(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = RecordingPrinter()
    args = ["--json", "--set", "camera.kind=file", "verify", _write(tmp_path / "c.json", CIRCLE), *STRICT]
    result = _run(cli_runner, [*args, "--printer", PRINTER, "--output-dir", str(tmp_path / "out")], printer=printer)
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["ok"] is False
    assert (printer.opened, printer.sent) == ([], [])
    assert not (tmp_path / "out").exists()


@pytest.mark.os_agnostic
def test_a_library_caller_of_verify_print_gets_the_refusal(tmp_path: Path) -> None:
    label, options = _strict_circle()
    printer = RecordingPrinter()
    camera = FakeCamera()
    with pytest.raises(LabelRejectedError, match="nothing was printed"):
        verify_print(
            build_job(label, options),
            label,
            options=options,
            endpoint=PrinterEndpoint(uri=PRINTER, timeout=1.0, open_printer=open_recording_printer(printer)),
            camera=CameraSettings(kind=CameraKind.FILE),
            output_dir=tmp_path,
            capture=camera,
            sleep=lambda _seconds: None,
        )
    assert (printer.opened, printer.sent, camera.calls) == ([], [], 0)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("renders", [0, 2])
def test_verify_print_refuses_a_checked_job_that_is_not_one_label(tmp_path: Path, renders: int) -> None:
    """The check sends what ``checked`` holds, the picture shows ``label``: they must be the same one."""
    label = Label.model_validate(SIMPLE)
    options = RenderOptions(model="allegro-2")
    printer = RecordingPrinter()
    camera = FakeCamera()
    job = CheckedJob(
        renders=record_renders(label, None, options) * renders, check=UploadCheck(wait=AnswerWait(seconds=1.0))
    )
    with pytest.raises(ConfigurationError, match=f"holds {renders} renders; nothing was printed"):
        verify_print(
            build_job(label, options),
            label,
            options=options,
            endpoint=PrinterEndpoint(uri=PRINTER, timeout=1.0, open_printer=open_recording_printer(printer)),
            camera=CameraSettings(kind=CameraKind.FILE),
            output_dir=tmp_path,
            capture=camera,
            sleep=lambda _seconds: None,
            checked=job,
        )
    assert (printer.opened, printer.sent, camera.calls) == ([], [], 0)


@pytest.mark.os_agnostic
def test_verify_print_takes_the_expected_pictures_model_from_the_options_before_printing(tmp_path: Path) -> None:
    """The picture is drawn for the options' model, so an unknown one must stop the job, not follow it."""
    label = Label.model_validate(SIMPLE)
    printer = RecordingPrinter()
    camera = FakeCamera()
    with pytest.raises(UnknownModelError):
        verify_print(
            build_job(label, RenderOptions(model="allegro-2")),
            label,
            options=RenderOptions(model="no-such-model"),
            endpoint=PrinterEndpoint(uri=PRINTER, timeout=1.0, open_printer=open_recording_printer(printer)),
            camera=CameraSettings(kind=CameraKind.FILE),
            output_dir=tmp_path,
            capture=camera,
            sleep=lambda _seconds: None,
        )
    assert (printer.opened, printer.sent, camera.calls) == ([], [], 0)


# ======================== render --output ========================


@pytest.mark.os_agnostic
def test_render_writes_no_file_for_a_refused_label(cli_runner: CliRunner, tmp_path: Path) -> None:
    output = tmp_path / "job.dpl"
    args = ["--json", "render", _write(tmp_path / "c.json", CIRCLE), *STRICT, "--output", str(output)]
    result = _run(cli_runner, args)
    assert result.exit_code == 1, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["data"].get("output") is None
    assert not output.exists()


# ======================== A batch ========================


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_one_bad_record_keeps_the_whole_batch_from_the_printer(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A real file URI: nothing may be created for the good record that came first."""
    target = tmp_path / "printed.dpl"
    data = tmp_path / "codes.csv"
    data.write_text(GOOD_THEN_BAD, encoding="utf-8")
    label = _write(tmp_path / "l.json", EAN13)
    args = ["--json", "print", label, "--data", str(data), "--model", "allegro-2"]
    result = cli_runner.invoke(cli_mod.cli, [*args, "--printer", f"file:{target}"], obj=build_production)
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["ok"] is False
    assert not target.exists()


@pytest.mark.os_agnostic
def test_a_batch_is_refused_when_only_a_batch_finding_is_an_error() -> None:
    """Every record is fine on its own; the cut across them is what strict refuses."""
    label = Label.model_validate({**NAMED, "cut": {"every": 2}})
    options = RenderOptions(model="prodigy", policy=FallbackPolicy.STRICT)
    batch = render_batch(label, [{"name": "A"}, {"name": "B"}], options)
    assert all(result.ok for result in batch.results)
    assert FindingCode.BATCH_CUT_UNSUPPORTED in {finding.code for finding in batch.batch_findings}
    assert not batch.ok


@pytest.mark.os_agnostic
def test_print_refuses_a_batch_on_its_batch_finding_alone(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = RecordingPrinter()
    data = tmp_path / "names.csv"
    data.write_text("name\nAnna\nBen\n", encoding="utf-8")
    label = _write(tmp_path / "l.json", {**NAMED, "cut": {"every": 2}})
    args = ["--json", "print", label, "--data", str(data), "--model", "prodigy", "--policy", "strict"]
    result = _run(cli_runner, [*args, "--printer", PRINTER], printer=printer)
    assert result.exit_code == 1, result.output
    assert printer.sent == []


# ======================== print_result ========================


@pytest.mark.os_agnostic
def test_print_result_refuses_a_job_with_an_error_finding() -> None:
    """The CLI guards before it gets here, so a library caller is who this guard is for."""
    label, options = _strict_circle()
    with pytest.raises(LabelRejectedError, match="nothing was sent"):
        print_result(
            build_job(label, options),
            endpoint=PrinterEndpoint(uri=PRINTER, timeout=1.0, open_printer=_opener_that_fails),
        )


@pytest.mark.os_agnostic
def test_print_result_refuses_a_mixed_batch() -> None:
    label = Label.model_validate(EAN13)
    records = [{"code": "4006381333931"}, {"code": "notdigits"}]
    batch = render_batch(label, records, RenderOptions(model="allegro-2"))
    with pytest.raises(LabelRejectedError, match="nothing was sent"):
        print_result(batch, endpoint=PrinterEndpoint(uri=PRINTER, timeout=1.0, open_printer=_opener_that_fails))


# ======================== CLI error routes ========================


def _assert_a_plain_error(result: Result, *, error: str) -> None:
    """Exit 2, one envelope naming ``error``, and no traceback although --traceback asked for one.

    --traceback prints one only for a failure the CLI did not expect, so this is what tells an
    expected error type from one that fell through to the net.
    """
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["error"] == error
    assert "Traceback" not in result.stderr


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("managed_traceback_state")
def test_render_into_a_missing_directory_is_a_plain_error(cli_runner: CliRunner, tmp_path: Path) -> None:
    output = tmp_path / "no" / "such" / "dir" / "job.dpl"
    args = ["--traceback", "--json", "render", _write(tmp_path / "l.json", SIMPLE), "--output", str(output)]
    _assert_a_plain_error(_run(cli_runner, args), error="FileNotFoundError")


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("managed_traceback_state")
def test_a_data_file_lacking_a_field_is_a_plain_error(cli_runner: CliRunner, tmp_path: Path) -> None:
    data = tmp_path / "other.csv"
    data.write_text("other\nvalue\n", encoding="utf-8")
    args = ["--traceback", "--json", "render", _write(tmp_path / "l.json", NAMED), "--data", str(data)]
    result = _run(cli_runner, args)
    _assert_a_plain_error(result, error="MissingFieldError")
    assert "name" in json.loads(result.stdout)["message"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("flag", ["--json", "--json-bare"])
def test_raw_output_on_stdout_cannot_share_it_with_the_envelope(
    flag: str, cli_runner: CliRunner, tmp_path: Path
) -> None:
    result = _run(cli_runner, [flag, "render", _write(tmp_path / "l.json", SIMPLE), "--output", "-"])
    assert result.exit_code == 2, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert "stdout carries the envelope" in envelope["message"]
    assert b"\x02L" not in result.stdout_bytes
