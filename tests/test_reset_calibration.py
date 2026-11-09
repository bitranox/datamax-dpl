"""A printer reset before a job: the report is no failure, and one calibration label takes the shift.

One Allegro 2 sent ``R\\x11NNNNNNNN\\r`` as its first answer after a power cycle and printed the first
label after one about 12 mm shifted (2026-11-02). The printer here is :class:`ImageModules` with
``reset`` set: its next answer carries the report, and only that one does.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import pytest
from printer_server import ImageModules, ModulePrinter, module_printer

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.settings import PrinterOverrides, PrinterSettings, printer_settings
from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
from datamax_dpl.application.ports import PrinterEndpoint
from datamax_dpl.application.upload_check import (
    CheckedJob,
    Link,
    ResetCheck,
    UploadCheck,
    UploadRefusedError,
    print_checked,
    send_checked,
)
from datamax_dpl.application.use_cases import (
    AnswerWait,
    BatchReport,
    BatchResult,
    read_reset_report,
    record_renders,
    render_batch,
    render_label,
)
from datamax_dpl.composition import build_production, build_testing
from datamax_dpl.domain.enums import (
    Attribute,
    FallbackPolicy,
    FindingCode,
    ImageModule,
    MediaSensor,
    MemoryKind,
    Severity,
    Unit,
)
from datamax_dpl.domain.errors import ConfigurationError, PrinterProtocolError
from datamax_dpl.domain.held_images import READ_AT_SEND, held_images_from
from datamax_dpl.domain.label import Cut, DotSize, Element, Image, Label, Text
from datamax_dpl.domain.memory import parse_module_directory
from datamax_dpl.domain.profiles import list_profiles
from datamax_dpl.domain.render import RenderResult
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.reset_label import CALIBRATION_LABEL_TEXT, calibration_job, characters_replaced
from datamax_dpl.domain.status import (
    MEMORY_DIRECTORY_REQUESTS,
    STATUS_REQUEST,
    parse_ascii_status,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from click.testing import CliRunner, Result
    from printer_server import Play, PrinterServer

    from datamax_dpl.domain.findings import Finding

STX = b"\x02"
# A developer's DATAMAX_DPL___ variable would change the bytes these tests compare.
pytestmark = pytest.mark.usefixtures("no_ambient_configuration")

WG = MEMORY_DIRECTORY_REQUESTS[MemoryKind.GRAPHIC]
FORMAT = STX + b"L\r"
URI = "tcp://192.0.2.60:9100"
#: What one Allegro 2 put in front of its first answer after a power cycle.
POWER_ON = b"R\x11"
CALIBRATION_TEXT = CALIBRATION_LABEL_TEXT.encode("ascii")
ALLEGRO = RenderOptions(model="allegro-2")
#: A module no clear may reach: the job reads the names first and never clears.
PLUG_IN = RenderOptions(model="allegro-2", image_module=ImageModule.B, model_image_budget=False)
_PATTERN = ["".join("#" if (row * 7 + column * 13) % 5 == 0 else "." for column in range(64)) for row in range(64)]
LOGO = Image(x=1, y=1, name="LOGO", bitmap=_PATTERN)
TEXT_LABEL = Label(width=100, height=60, elements=[Text(x=5, y=5, text="Hello")])
IMAGE_LABEL = Label(width=100, height=60, elements=[LOGO])
BATCH_LABEL = Label(width=100, height=60, elements=[Text(x=5, y=5, text="Record {n}"), LOGO])
RECORDS = ({"n": "1"}, {"n": "2"}, {"n": "3"})
DEFAULT_RESET = ResetCheck()


def _check() -> UploadCheck:
    return UploadCheck(wait=AnswerWait(seconds=1.0))


def _findings(result: RenderResult | BatchResult | BatchReport) -> list[Finding]:
    assert not isinstance(result, BatchReport)
    results = result.results if isinstance(result, BatchResult) else (result,)
    return [finding for r in results for finding in r.findings]


def _codes(result: RenderResult | BatchResult) -> list[FindingCode]:
    return [finding.code for finding in _findings(result) if "reset" in finding.code.value]


def _print(
    printer: RecordingPrinter,
    label: Label = TEXT_LABEL,
    *,
    options: RenderOptions = ALLEGRO,
    records: Sequence[dict[str, str]] | None = None,
    reset: ResetCheck | None = DEFAULT_RESET,
) -> tuple[RenderResult | BatchResult, RenderResult | BatchResult, int]:
    """Print through the library's checked route; return the job, the job as it went out and the bytes sent."""
    result = render_label(label, options) if records is None else render_batch(label, records, options)
    job = CheckedJob(renders=record_renders(label, records, options), check=_check(), reset=reset)
    report = print_checked(
        result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
    )
    return result, report.result, report.bytes_sent


def _job_chunks(printer: RecordingPrinter) -> bytes:
    """What the job wrote, without the status and directory requests."""
    return b"".join(chunk for chunk in printer.sent if chunk not in (STATUS_REQUEST, WG))


def _label_file(tmp_path: Path, label: Label) -> str:
    path = tmp_path / "label.json"
    path.write_text(json.dumps(label.model_dump(mode="json", exclude_none=True)), encoding="utf-8")
    return str(path)


def _cli(runner: CliRunner, printer: RecordingPrinter | None, *args: str) -> Result:
    return runner.invoke(cli_mod.cli, list(args), obj=lambda: build_testing(printer=printer))


def _data(result: Result) -> dict[str, Any]:
    return json.loads(result.stdout)["data"]


# ======================== A reset report is no failure ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize("prefix", [b"R", b"T", b"R\x11", b"\x11R", b"\x11T\x13", b"RT"], ids=repr)
def test_a_reset_report_before_a_status_answer_returns_the_status_and_says_so(prefix: bytes) -> None:
    status = parse_ascii_status(prefix + b"NYNNNNNN\r")
    assert status.reset_reported is True
    assert status.paper_out is True


@pytest.mark.os_agnostic
def test_a_status_answer_with_no_report_says_none() -> None:
    assert parse_ascii_status(b"\x11NNNNNNNN\r").reset_reported is False


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("answer", "meaning"),
    [
        (b"cNNNNNNNN\r", "framing error"),
        (b"RcNNNNNNNN\r", "framing error"),
        (b"vTNNNNNNNN\r", "input buffer overflow"),
        (b"RZNNNNNNNN\r", "unexpected bytes"),
    ],
    ids=repr,
)
def test_any_other_error_character_keeps_refusing_a_status_answer(answer: bytes, meaning: str) -> None:
    with pytest.raises(PrinterProtocolError, match=meaning):
        parse_ascii_status(answer)


@pytest.mark.os_agnostic
def test_a_short_answer_behind_a_reset_letter_stays_refused() -> None:
    with pytest.raises(PrinterProtocolError, match="expected 8 byte"):
        parse_ascii_status(b"RNNN\r")


_LISTING = b"MODULE: A\rLOGO\rAVAILABLE BYTES IN MODULE : 00049083\r"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("prefix", [b"R", b"T", b"R\x11", b"\x11T\x13"], ids=repr)
def test_a_reset_report_before_a_directory_leaves_the_listing_readable(prefix: bytes) -> None:
    directory = parse_module_directory(prefix + _LISTING, kind=MemoryKind.GRAPHIC)
    assert (directory.reset_reported, directory.parsed) == (True, True)
    assert directory.modules[0].names == ("LOGO",)
    assert directory.raw.startswith(prefix.decode("ascii").replace("\x11", "\x11")[:1])
    held = held_images_from(directory, module=ImageModule.A)
    assert (held.listed, held.reset_reported) == (frozenset({"LOGO"}), True)


@pytest.mark.os_agnostic
def test_a_reset_report_before_no_modules_available_is_still_that_answer() -> None:
    directory = parse_module_directory(b"RNo Modules Available\r", kind=MemoryKind.GRAPHIC)
    assert (directory.reset_reported, directory.no_modules_available, directory.parsed) == (True, True, True)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("answer", [b"RUBBISH\r", b"rMODULE: A\r", b"TEXT\rMODULE: A\r"], ids=repr)
def test_a_directory_that_merely_starts_with_a_reset_letter_reports_nothing(answer: bytes) -> None:
    assert parse_module_directory(answer, kind=MemoryKind.GRAPHIC).reset_reported is False


@pytest.mark.os_agnostic
def test_the_status_command_exits_0_and_shows_the_field_in_both_outputs(cli_runner: CliRunner) -> None:
    answers = {STATUS_REQUEST: POWER_ON + b"NNNNNNNN\r"}

    machine = _cli(cli_runner, RecordingPrinter(answers=answers), "--json", "status", "--printer", URI)
    human = _cli(cli_runner, RecordingPrinter(answers=answers), "status", "--printer", URI)

    assert machine.exit_code == 0, machine.output
    assert _data(machine)["reset_reported"] is True
    assert human.exit_code == 0, human.output
    assert "reset_reported" in human.stdout


@pytest.mark.os_agnostic
def test_the_status_command_says_nothing_of_a_reset_nobody_reported(cli_runner: CliRunner) -> None:
    answers = {STATUS_REQUEST: b"NNNNNNNN\r"}
    machine = _cli(cli_runner, RecordingPrinter(answers=answers), "--json", "status", "--printer", URI)
    human = _cli(cli_runner, RecordingPrinter(answers=answers), "status", "--printer", URI)
    assert _data(machine)["reset_reported"] is False
    assert "reset_reported" not in human.stdout


@pytest.mark.os_agnostic
def test_the_memory_command_lists_the_names_and_names_the_report(cli_runner: CliRunner) -> None:
    answers = {WG: POWER_ON + _LISTING}

    machine = _cli(cli_runner, RecordingPrinter(answers=answers), "--json", "memory", "--printer", URI)
    human = _cli(cli_runner, RecordingPrinter(answers=answers), "memory", "--printer", URI)

    assert machine.exit_code == 0, machine.output
    assert _data(machine)["directories"][0]["reset_reported"] is True
    assert _data(machine)["directories"][0]["modules"][0]["names"] == ["LOGO"]
    assert "reset_reported" in human.stdout
    assert "LOGO" in human.stdout


# ======================== The calibration label ========================


@pytest.mark.os_agnostic
def test_the_calibration_label_starts_with_the_settings_the_job_starts_with() -> None:
    label = Label(
        width=100, height=60, media=MediaSensor.GAP, stop_position=10.0, elements=[Text(x=5, y=5, text="Hello")]
    )

    job = render_label(label, ALLEGRO)
    calibration = calibration_job(label, ALLEGRO, text="Feed")

    settings = job.data[: job.data.index(STX + b"qA")]
    assert settings.count(STX) >= 3
    assert calibration.data.startswith(settings + FORMAT)


@pytest.mark.os_agnostic
def test_the_calibration_label_holds_one_copy_of_only_its_text() -> None:
    label = BATCH_LABEL.model_copy(update={"quantity": 5})
    calibration = calibration_job(label, ALLEGRO, text="Feed")
    assert calibration.record_count == 1
    assert b"Feed" in calibration.data
    assert b"Q0001\r" in calibration.data
    assert b"Record" not in calibration.data
    assert calibration.downloads == ()


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("label", "expected_mm"),
    [
        (Label(width=100, height=60, elements=[Text(x=1, y=1, text="x")]), 30.0),
        (Label(unit=Unit.INCH, width=4, height=2, elements=[Text(x=0.1, y=0.1, text="x")]), 25.4),
        (Label(unit=Unit.DOTS, width=800, height=406, elements=[Text(x=10, y=10, text="x")]), 25.4),
    ],
    ids=["mm", "in", "dots"],
)
def test_the_text_sits_mid_label_along_the_feed_so_a_15_mm_shift_keeps_it_on_the_paper(
    label: Label, expected_mm: float
) -> None:
    calibration = calibration_job(label, RenderOptions(model="allegro-2", scale_along=1.0, offset_along=0.0), text="F")
    record = next(line for line in calibration.data.split(b"\r") if line.endswith(b"F"))
    # The record's row, in 1/100 inch on this model: 1, font, width, height, size, row, column.
    row_mm = int(record[7:11]) * 0.254
    # The canvas holds the margin a field keeps, so mid-label is a little past half the height.
    assert row_mm == pytest.approx(expected_mm, abs=1.0)
    assert row_mm > 15.0


@pytest.mark.os_agnostic
def test_the_text_sits_mid_label_across_when_it_fits() -> None:
    wide = calibration_job(TEXT_LABEL, ALLEGRO, text="F")
    record = next(line for line in wide.data.split(b"\r") if line.endswith(b"F"))
    assert 40 < int(record[11:15]) * 0.254 < 55


@pytest.mark.os_agnostic
def test_a_label_with_no_height_takes_the_height_its_fields_need() -> None:
    label = Label(elements=[Text(x=5, y=100, text="Hello")])
    calibration = calibration_job(label, ALLEGRO, text="F")
    record = next(line for line in calibration.data.split(b"\r") if line.endswith(b"F"))
    assert 40 < int(record[7:11]) * 0.254 < 70


@pytest.mark.os_agnostic
def test_the_calibration_label_clears_nothing_whatever_the_job_clears() -> None:
    assert render_label(TEXT_LABEL, ALLEGRO).closing == STX + b"qA"
    calibration = calibration_job(TEXT_LABEL, ALLEGRO, text="Feed")
    assert calibration.closing == b""
    assert STX + b"q" not in calibration.data


@pytest.mark.os_agnostic
def test_a_job_that_cuts_keeps_its_cutter_stop_on_the_calibration_label() -> None:
    options = RenderOptions(model="dmx-600", cutter_installed=True, cutter_stop_position_mm=20.0)
    label = Label(width=100, height=60, quantity=4, cut=Cut(every=2), elements=[Text(x=5, y=5, text="Hello")])
    job = render_label(label, options)
    calibration = calibration_job(label, options, text="Feed")
    stop = next(part for part in job.data.split(STX) if part.startswith(b"f"))
    assert STX + stop in calibration.data
    assert b"Q0001\r" in calibration.data


@pytest.mark.os_agnostic
def test_a_job_that_pauses_does_not_hold_the_printer_before_its_calibration_label() -> None:
    label = TEXT_LABEL.model_copy(update={"pause_each_label": True})
    assert STX + b"J" in render_label(label, ALLEGRO).data
    assert STX + b"J" not in calibration_job(label, ALLEGRO, text="Feed").data


@pytest.mark.os_agnostic
@pytest.mark.parametrize("policy", list(FallbackPolicy), ids=lambda policy: policy.value)
@pytest.mark.parametrize("model", [profile.id for profile in list_profiles()])
def test_every_model_can_print_the_default_calibration_label(model: str, policy: FallbackPolicy) -> None:
    calibration = calibration_job(TEXT_LABEL, RenderOptions(model=model, policy=policy), text=CALIBRATION_LABEL_TEXT)
    assert calibration.ok, calibration.errors
    assert CALIBRATION_TEXT in calibration.data


@pytest.mark.os_agnostic
def test_the_text_goes_through_the_mapping_of_a_text_field() -> None:
    arrow = chr(0x2192)
    assert characters_replaced(CALIBRATION_LABEL_TEXT) == ()
    assert characters_replaced(f"Feed {arrow}") == (arrow,)
    assert b"Feed ?" in calibration_job(TEXT_LABEL, ALLEGRO, text=f"Feed {arrow}").data


@pytest.mark.os_agnostic
def test_a_control_byte_in_the_text_never_reaches_the_printer_as_a_command() -> None:
    """A printer command stream is an injection sink: <SOH># in the text would reset the printer."""
    calibration = calibration_job(TEXT_LABEL, ALLEGRO, text="Feed\x01#\x02qA\rE")
    assert b"\x01#" not in calibration.data
    assert b"Feed?#?qA?E" in calibration.data


# ======================== The matrix: when it prints ========================


@pytest.mark.os_agnostic
def test_on_a_reset_the_job_reads_once_then_sends_the_calibration_label_then_the_job_unchanged() -> None:
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)

    result, sent, bytes_sent = _print(printer)

    calibration = calibration_job(TEXT_LABEL, ALLEGRO, text=CALIBRATION_LABEL_TEXT)
    assert printer.sent == [STATUS_REQUEST, calibration.data, result.data]
    assert modules.status_requests == 1
    assert bytes_sent == len(calibration.data) + len(result.data)
    assert _codes(sent) == [FindingCode.PRINTER_RESET_CALIBRATED]
    assert isinstance(sent, RenderResult)
    assert next(f for f in sent.findings if "reset" in f.code.value).severity is Severity.INFO


@pytest.mark.os_agnostic
def test_with_no_reset_the_job_goes_out_with_not_a_byte_changed_and_says_nothing() -> None:
    printer = ModulePrinter(ImageModules())
    result, sent, bytes_sent = _print(printer)
    assert printer.sent == [STATUS_REQUEST, result.data]
    assert bytes_sent == len(result.data)
    assert _codes(sent) == []
    assert isinstance(sent, RenderResult)
    assert _findings(sent) == _findings(result)


@pytest.mark.os_agnostic
def test_with_the_key_off_the_job_reads_and_reports_and_prints_no_calibration_label() -> None:
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)

    result, sent, _ = _print(printer, reset=ResetCheck(calibrate=False))

    assert printer.sent == [STATUS_REQUEST, result.data]
    assert _codes(sent) == [FindingCode.PRINTER_RESET_NOT_CALIBRATED]
    assert isinstance(sent, RenderResult)
    finding = next(f for f in sent.findings if "reset" in f.code.value)
    assert "may be shifted" in finding.message
    assert "[printer]" not in finding.message, "a library caller has no settings file to look in"
    assert ResetCheck().off_because in finding.message
    assert finding.severity is Severity.INFO


@pytest.mark.os_agnostic
def test_with_the_key_off_and_no_reset_nothing_is_said() -> None:
    printer = ModulePrinter(ImageModules())
    _, sent, _ = _print(printer, reset=ResetCheck(calibrate=False))
    assert _codes(sent) == []


@pytest.mark.os_agnostic
def test_the_configured_text_is_what_the_label_says() -> None:
    printer = ModulePrinter(ImageModules(reset=POWER_ON))
    _print(printer, reset=ResetCheck(text="Rollenanfang"))
    assert b"Rollenanfang" in printer.data
    assert CALIBRATION_TEXT not in printer.data


@pytest.mark.os_agnostic
def test_a_batch_gets_one_calibration_label_before_its_first_label_only() -> None:
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)

    result, sent, _ = _print(printer, BATCH_LABEL, records=RECORDS)

    assert printer.data.count(CALIBRATION_TEXT) == 1
    assert modules.status_requests == 1
    assert isinstance(result, BatchResult)
    assert _job_chunks(printer).index(CALIBRATION_TEXT) < _job_chunks(printer).index(b"Record 1")
    assert _job_chunks(printer).endswith(result.results[-1].data[result.results[-1].upload_end :])
    assert isinstance(sent, BatchResult)
    assert [_codes(r) for r in sent.results] == [[FindingCode.PRINTER_RESET_CALIBRATED], [], []]


@pytest.mark.os_agnostic
def test_a_batch_with_no_reset_sends_each_record_as_rendered() -> None:
    printer = ModulePrinter(ImageModules())
    result, _, _ = _print(printer, BATCH_LABEL, records=RECORDS)
    assert isinstance(result, BatchResult)
    assert CALIBRATION_TEXT not in printer.data
    assert _job_chunks(printer) == result.data


@pytest.mark.os_agnostic
def test_a_retry_round_prints_no_second_calibration_label() -> None:
    """The first download is lost, so the upload check runs its second round after the calibration label."""
    modules = ImageModules(reset=POWER_ON, lose={0})
    printer = ModulePrinter(modules)

    _, sent, _ = _print(printer, IMAGE_LABEL)

    assert printer.data.count(CALIBRATION_TEXT) == 1
    assert modules.status_requests == 1
    assert len(modules.downloads) == 2
    assert modules.placed_missing == []
    assert isinstance(sent, RenderResult)
    assert FindingCode.IMAGE_UPLOAD_RETRIED in [f.code for f in sent.findings]


@pytest.mark.os_agnostic
def test_after_the_report_the_first_image_job_needs_no_retry() -> None:
    """The measured order: status first, then the text label, then the image job with no retry."""
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)

    _, sent, _ = _print(printer, IMAGE_LABEL)

    assert isinstance(sent, RenderResult)
    assert [f.code for f in sent.findings if f.code is FindingCode.IMAGE_UPLOAD_RETRIED] == []
    assert len(modules.downloads) == 1
    assert printer.data.count(CALIBRATION_TEXT) == 1


@pytest.mark.os_agnostic
def test_the_print_command_tells_a_person_one_extra_label_came_out(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = ModulePrinter(ImageModules(reset=POWER_ON))
    args = ["print", _label_file(tmp_path, TEXT_LABEL), "--model", "allegro-2", "--printer", URI]
    result = _cli(cli_runner, printer, *args)
    assert result.exit_code == 0, result.output
    assert "printer_reset_calibrated" in result.stdout


@pytest.mark.os_agnostic
def test_the_print_command_with_no_reset_says_only_what_it_sent(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = ModulePrinter(ImageModules())
    args = ["print", _label_file(tmp_path, TEXT_LABEL), "--model", "allegro-2", "--printer", URI]
    result = _cli(cli_runner, printer, *args)
    assert result.stdout.splitlines() == [f"sent {len(_job_chunks(printer))} bytes to {URI}"]


@pytest.mark.os_agnostic
def test_a_batch_through_the_command_prints_one_calibration_label(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = ModulePrinter(ImageModules(reset=POWER_ON))
    data = tmp_path / "records.csv"
    data.write_text("n\n1\n2\n", encoding="utf-8")
    label = _label_file(tmp_path, BATCH_LABEL)

    result = _cli(
        cli_runner, printer, "--json", "print", label, "--data", str(data), "--model", "allegro-2", "--printer", URI
    )

    assert result.exit_code == 0, result.output
    assert printer.data.count(CALIBRATION_TEXT) == 1
    assert [f["record"] for f in _data(result)["findings"] if f["code"] == "printer_reset_calibrated"] == [0]


@pytest.mark.os_agnostic
def test_the_settings_reach_the_job_through_the_command(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = ModulePrinter(ImageModules(reset=POWER_ON))
    args = ["--set", "printer.calibration_label_text=Rolle", "print", _label_file(tmp_path, TEXT_LABEL)]
    result = _cli(cli_runner, printer, *args, "--model", "allegro-2", "--printer", URI)
    assert result.exit_code == 0, result.output
    assert b"Rolle" in printer.data


@pytest.mark.os_agnostic
def test_the_key_off_reaches_the_job_through_the_command(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = ModulePrinter(ImageModules(reset=POWER_ON))
    args = ["--json", "--set", "printer.calibrate_after_reset=false", "print", _label_file(tmp_path, TEXT_LABEL)]
    result = _cli(cli_runner, printer, *args, "--model", "allegro-2", "--printer", URI)
    assert result.exit_code == 0, result.output
    assert CALIBRATION_TEXT not in printer.data
    said = {f["code"]: f["message"] for f in _data(result)["findings"]}
    assert "[printer] calibrate_after_reset is off" in said["printer_reset_not_calibrated"]


@pytest.mark.os_agnostic
def test_the_key_off_under_a_named_printer_names_that_printers_section(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = ModulePrinter(ImageModules(reset=POWER_ON))
    named = ["--set", f"printers.lager.uri={URI}", "--set", "printers.lager.calibrate_after_reset=false"]
    args = ["--json", *named, "print", _label_file(tmp_path, TEXT_LABEL), "--model", "allegro-2", "--printer", "lager"]
    result = _cli(cli_runner, printer, *args)
    assert result.exit_code == 0, result.output
    said = {f["code"]: f["message"] for f in _data(result)["findings"]}
    assert "[printers.lager] calibrate_after_reset is off" in said["printer_reset_not_calibrated"]
    assert "[printer] calibrate_after_reset" not in said["printer_reset_not_calibrated"]


@pytest.mark.os_agnostic
def test_the_wait_reaches_the_read_through_the_command(cli_runner: CliRunner, tmp_path: Path) -> None:
    class _Waits(ModulePrinter):
        def __init__(self) -> None:
            super().__init__(ImageModules())
            self.status_waits: list[float] = []

        def query(self, request: bytes, *, timeout: float, **kwargs: Any) -> bytes:
            self.status_waits.append(timeout)
            return super().query(request, timeout=timeout, **kwargs)

    printer = _Waits()
    args = ["--set", "printer.reset_check_wait=1.5", "print", _label_file(tmp_path, TEXT_LABEL)]
    result = _cli(cli_runner, printer, *args, "--model", "allegro-2", "--printer", URI, "--timeout", "9")
    assert result.exit_code == 0, result.output
    assert printer.status_waits == [1.5]


# ======================== The matrix: when it must not ========================


@pytest.mark.os_agnostic
def test_a_silent_printer_gets_the_job_as_it_would_have_and_no_finding() -> None:
    modules = ImageModules(status=False, reset=POWER_ON)
    printer = ModulePrinter(modules)

    result, sent, bytes_sent = _print(printer)

    assert modules.status_requests == 1
    assert printer.sent == [STATUS_REQUEST, result.data]
    assert bytes_sent == len(result.data)
    assert isinstance(sent, RenderResult)
    assert _findings(sent) == _findings(result)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "answer",
    [b"RcNNNNNNNN\r", b"RZNNNNNNNN\r", b"cNNNNNNNN\r", b"NNNNNNNY\r"],
    ids=repr,
)
def test_an_unreadable_answer_means_nothing(answer: bytes) -> None:
    printer = ModulePrinter(ImageModules(), answers={STATUS_REQUEST: answer})
    result, sent, _ = _print(printer)
    assert printer.sent == [STATUS_REQUEST, result.data]
    assert isinstance(sent, RenderResult)
    assert _findings(sent) == _findings(result)


@pytest.mark.os_agnostic
def test_a_read_that_cannot_reach_the_printer_means_nothing() -> None:
    assert read_reset_report(RecordingPrinter(bidirectional=False), wait=1.0) is False
    closed = RecordingPrinter()
    closed.close()
    assert read_reset_report(closed, wait=1.0) is False


@pytest.mark.os_agnostic
def test_a_channel_the_read_lost_is_replaced_for_the_job(
    serve: Callable[..., PrinterServer],
) -> None:
    """A print server that hangs up on the status request: the job goes on a new connection."""

    def hanging_up(server: PrinterServer, connection: Any) -> None:
        server.read_exactly(connection, len(STATUS_REQUEST))

    modules = ImageModules()
    plays: tuple[Play, ...] = (hanging_up, module_printer(modules))
    server = serve(*plays)
    result = render_label(TEXT_LABEL, ALLEGRO)
    job = CheckedJob(renders=record_renders(TEXT_LABEL, None, ALLEGRO), check=_check())

    report = print_checked(
        result,
        job,
        endpoint=PrinterEndpoint(uri=server.address, timeout=5.0, open_printer=build_production().open_printer),
    )
    server.join()

    assert report.bytes_sent == len(result.data)
    assert bytes(server.connections[1]) == result.data


@pytest.mark.os_agnostic
def test_a_one_way_link_reads_nothing_and_prints_nothing_extra() -> None:
    printer = RecordingPrinter(bidirectional=False)
    result, sent, _ = _print(printer)
    assert printer.sent == [result.data]
    assert printer.queries == []
    assert sent is result


@pytest.mark.os_agnostic
def test_the_print_command_on_a_file_writes_exactly_the_job(cli_runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "job.dpl"
    args = ["print", _label_file(tmp_path, TEXT_LABEL), "--model", "allegro-2", "--printer", f"file:{target}"]

    result = cli_runner.invoke(cli_mod.cli, args, obj=build_production)

    assert result.exit_code == 0, result.output
    assert target.read_bytes() == render_label(TEXT_LABEL, ALLEGRO).data


@pytest.mark.os_agnostic
def test_a_one_way_link_handed_straight_to_the_check_reads_nothing() -> None:
    printer = RecordingPrinter(bidirectional=False)
    result = render_label(TEXT_LABEL, ALLEGRO)
    job = CheckedJob(renders=record_renders(TEXT_LABEL, None, ALLEGRO), check=_check())
    opener = open_recording_printer(printer)
    link = Link(
        printer=printer,
        endpoint=PrinterEndpoint(uri=URI, timeout=1.0, open_printer=opener),
        reconnect=AnswerWait(seconds=1.0),
    )

    send_checked(link, result, job)

    assert printer.queries == []
    assert printer.sent == [result.data]


@pytest.mark.os_agnostic
def test_a_real_file_channel_handed_straight_to_the_check_gets_exactly_the_job(tmp_path: Path) -> None:
    """A file cannot answer, so the read means nothing and the file holds the job."""
    target = tmp_path / "job.dpl"
    uri = f"file:{target}"
    opener = build_production().open_printer
    result = render_label(TEXT_LABEL, ALLEGRO)
    job = CheckedJob(renders=record_renders(TEXT_LABEL, None, ALLEGRO), check=_check())
    channel = opener(uri, timeout=1.0)
    link = Link(
        printer=channel,
        endpoint=PrinterEndpoint(uri=uri, timeout=1.0, open_printer=opener),
        reconnect=AnswerWait(seconds=1.0),
    )

    send_checked(link, result, job)
    channel.close()

    assert target.read_bytes() == result.data


@pytest.mark.os_agnostic
def test_the_print_command_on_stdout_writes_exactly_the_job(cli_runner: CliRunner, tmp_path: Path) -> None:
    args = ["print", _label_file(tmp_path, TEXT_LABEL), "--model", "allegro-2", "--printer", "-"]
    result = cli_runner.invoke(cli_mod.cli, args, obj=build_production)
    assert result.exit_code == 0, result.output
    assert result.stdout_bytes.startswith(render_label(TEXT_LABEL, ALLEGRO).data)
    assert CALIBRATION_TEXT not in result.stdout_bytes


@pytest.mark.os_agnostic
def test_a_refused_label_reads_nothing_and_prints_nothing_through_the_command(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """A Prodigy has no image module until one is configured, so this label is refused when it renders."""
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)
    args = ["--json", "print", _label_file(tmp_path, IMAGE_LABEL), "--model", "prodigy", "--printer", URI]

    result = _cli(cli_runner, printer, *args)

    assert result.exit_code == 1, result.output
    assert printer.sent == []
    assert modules.status_requests == 0


@pytest.mark.os_agnostic
def test_a_job_into_the_module_no_directory_lists_reads_nothing_and_prints_nothing() -> None:
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)
    options = RenderOptions(model="allegro-2", image_module=ImageModule.C, model_image_budget=False)
    result = render_label(IMAGE_LABEL, options)
    job = CheckedJob(renders=record_renders(IMAGE_LABEL, None, options), check=_check())
    link = Link(
        printer=printer,
        endpoint=PrinterEndpoint(uri=URI, timeout=1.0, open_printer=open_recording_printer(printer)),
        reconnect=AnswerWait(seconds=1.0),
    )

    send = send_checked(link, result, job)

    assert send.all_sent is False
    assert printer.sent == []
    assert modules.status_requests == 0


@pytest.mark.os_agnostic
def test_a_label_in_error_once_rendered_with_the_names_read_prints_no_calibration_label() -> None:
    """Strict refuses a held name; the first label's names are read before the reset check, so nothing goes out."""
    strict = PLUG_IN.model_copy(update={"policy": FallbackPolicy.STRICT, "held_image_severity": Severity.ERROR})
    first = render_label(IMAGE_LABEL, strict, held=READ_AT_SEND)
    [name] = first.downloads
    modules = ImageModules(reset=POWER_ON, held=[("B", name, 512)])
    printer = ModulePrinter(modules)
    opener = open_recording_printer(printer)
    job = CheckedJob(renders=record_renders(IMAGE_LABEL, None, strict), check=_check())
    link = Link(
        printer=opener(URI, timeout=1.0),
        endpoint=PrinterEndpoint(uri=URI, timeout=1.0, open_printer=opener),
        reconnect=AnswerWait(seconds=1.0),
    )

    send = send_checked(link, first, job)

    assert send.all_sent is False
    assert printer.sent == [WG]
    assert modules.status_requests == 0
    assert FindingCode.PRINTER_RESET_NOT_CALIBRATED in _codes(send.result)


@pytest.mark.os_agnostic
def test_a_calibration_label_with_an_error_finding_is_never_sent() -> None:
    """Mid-label lies past the row limit though the label's own field does not: the job goes on, and says so."""
    options = RenderOptions(model="allegro-2", max_format_rows=50)
    label = Label(width=100, height=60, elements=[Text(x=1, y=1, text="Low")])
    printer = ModulePrinter(ImageModules(reset=POWER_ON))
    assert not calibration_job(label, options, text=CALIBRATION_LABEL_TEXT).ok

    result, sent, _ = _print(printer, label, options=options)

    assert printer.sent == [STATUS_REQUEST, result.data]
    assert _codes(sent) == [FindingCode.PRINTER_RESET_NOT_CALIBRATED]
    assert isinstance(sent, RenderResult)
    assert next(f for f in sent.findings if "reset" in f.code.value).severity is Severity.WARNING


@pytest.mark.os_agnostic
def test_the_reset_read_makes_no_refused_for_silence_decision() -> None:
    """A status read with no answer changes nothing the upload check's own reads then decide."""
    arms: list[tuple[int, list[bytes]]] = []
    for status in (True, False):
        modules = ImageModules(answers=False, status=status)
        printer = ModulePrinter(modules)
        result = render_label(IMAGE_LABEL, ALLEGRO)
        job = CheckedJob(renders=record_renders(IMAGE_LABEL, None, ALLEGRO), check=_check())
        with pytest.raises(Exception, match="did not store"):
            print_checked(
                result,
                job,
                endpoint=PrinterEndpoint(uri=URI, timeout=1.0, open_printer=open_recording_printer(printer)),
            )
        arms.append((modules.requests, list(printer.sent)))
    assert arms[0] == arms[1]
    assert arms[0][0] == 3


@pytest.mark.os_agnostic
def test_a_silent_status_read_leaves_a_label_whose_directory_answers_printing() -> None:
    modules = ImageModules(status=False)
    printer = ModulePrinter(modules)
    result, _, _ = _print(printer, IMAGE_LABEL)
    assert _job_chunks(printer) == result.data
    assert modules.requests == 1


@pytest.mark.os_agnostic
def test_a_reset_is_never_read_as_an_empty_module() -> None:
    """The module still lists the name after the report: it is placed, not downloaded again, and not cleared."""
    first = render_label(IMAGE_LABEL, PLUG_IN)
    [name] = first.downloads
    modules = ImageModules(reset=POWER_ON, held=[("B", name, 512)])
    printer = ModulePrinter(modules)
    opener = open_recording_printer(printer)
    result = render_label(IMAGE_LABEL, PLUG_IN, held=READ_AT_SEND)
    job = CheckedJob(renders=record_renders(IMAGE_LABEL, None, PLUG_IN), check=_check())

    print_checked(result, job, endpoint=PrinterEndpoint(uri=URI, timeout=1.0, open_printer=opener))

    assert modules.downloaded_held == []
    assert modules.placed_missing == []
    assert modules.names("B") == [name]
    assert printer.data.count(CALIBRATION_TEXT) == 1


@pytest.mark.os_agnostic
def test_on_built_in_ram_the_calibration_label_leaves_what_the_module_holds_alone() -> None:
    """It clears nothing; the module's clear before the downloads stays the job's own."""
    keys_off = RenderOptions(
        model="allegro-2", clear_graphics_before_label=False, clear_graphics_after_label=False, model_image_budget=False
    )
    [name] = render_label(IMAGE_LABEL, keys_off).downloads
    modules = ImageModules(reset=POWER_ON, held=[("A", name, 512)])
    printer = ModulePrinter(modules)
    opener = open_recording_printer(printer)
    result = render_label(IMAGE_LABEL, keys_off, held=READ_AT_SEND)
    job = CheckedJob(renders=record_renders(IMAGE_LABEL, None, keys_off), check=_check())

    print_checked(result, job, endpoint=PrinterEndpoint(uri=URI, timeout=1.0, open_printer=opener))

    assert modules.placed_missing == []
    assert modules.names("A") == [name]


@pytest.mark.os_agnostic
def test_a_report_the_names_read_took_counts_and_nothing_more_is_asked() -> None:
    """The calibration label still goes before the first label's downloads."""
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)
    opener = open_recording_printer(printer)
    result = render_label(IMAGE_LABEL, PLUG_IN, held=READ_AT_SEND)
    job = CheckedJob(renders=record_renders(IMAGE_LABEL, None, PLUG_IN), check=_check())

    report = print_checked(result, job, endpoint=PrinterEndpoint(uri=URI, timeout=1.0, open_printer=opener))

    assert modules.status_requests == 0
    assert printer.data.count(CALIBRATION_TEXT) == 1
    assert printer.data.index(CALIBRATION_TEXT) < printer.data.index(STX + b"IB")
    assert FindingCode.PRINTER_RESET_CALIBRATED in _codes(report.result)


@pytest.mark.os_agnostic
def test_a_printer_whose_report_a_status_run_took_gets_no_calibration_label(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The documented limit: the printer gives the report once, and ``status`` took it."""
    printer = ModulePrinter(ImageModules(reset=POWER_ON))
    assert _cli(cli_runner, printer, "status", "--printer", URI).exit_code == 0
    args = ["print", _label_file(tmp_path, TEXT_LABEL), "--model", "allegro-2", "--printer", URI]
    assert _cli(cli_runner, printer, *args).exit_code == 0
    assert CALIBRATION_TEXT not in printer.data


@pytest.mark.os_agnostic
def test_a_caller_may_ask_nothing_at_all() -> None:
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)
    result, _, _ = _print(printer, reset=None)
    assert printer.sent == [result.data]
    assert modules.status_requests == 0


@pytest.mark.os_agnostic
def test_over_a_real_socket_the_report_arrives_and_the_label_goes_first(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[[Play], PrinterServer]
) -> None:
    server = serve(module_printer(ImageModules(reset=POWER_ON)))
    args = ["--json", "print", _label_file(tmp_path, TEXT_LABEL), "--model", "allegro-2", "--printer", server.address]

    result = cli_runner.invoke(cli_mod.cli, args, obj=build_production)
    server.join()

    assert result.exit_code == 0, result.output
    calibration = calibration_job(TEXT_LABEL, ALLEGRO, text=CALIBRATION_LABEL_TEXT)
    assert bytes(server.received) == STATUS_REQUEST + calibration.data + render_label(TEXT_LABEL, ALLEGRO).data


# ======================== The settings ========================


class _Config(dict[str, Any]):
    """A loaded configuration as ``printer_settings`` reads it."""


def _settings(
    printer: dict[str, Any], *, named: dict[str, Any] | None = None, name: str | None = None
) -> PrinterSettings:
    config: Any = _Config(printer=printer, printers={"lager": named} if named else {})
    return printer_settings(config, PrinterOverrides(printer=name))


@pytest.mark.os_agnostic
def test_the_keys_default_as_decided() -> None:
    settings = PrinterSettings()
    assert settings.reset_check_wait == 3.0
    assert settings.calibrate_after_reset is True
    assert settings.calibration_label_text == "Printer Reset - Calibration Feed"
    assert ResetCheck().wait == settings.reset_check_wait


@pytest.mark.os_agnostic
def test_a_named_printer_overrides_each_key() -> None:
    named = {"reset_check_wait": 1.0, "calibrate_after_reset": False, "calibration_label_text": "Lager"}
    settings = _settings({"reset_check_wait": 2.0}, named=named, name="lager")
    assert (settings.reset_check_wait, settings.calibrate_after_reset, settings.calibration_label_text) == (
        1.0,
        False,
        "Lager",
    )


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "printer",
    [
        {"reset_check_wait": 0},
        {"reset_check_wait": -1},
        {"reset_check_wait": True},
        {"calibration_label_text": ""},
        {"calibration_label_text": "x" * 256},
        {"calibration_label_text": 5},
        {"calibrate_after_reset": "perhaps"},
    ],
    ids=repr,
)
def test_a_bad_value_is_refused_naming_its_key(printer: dict[str, Any]) -> None:
    [key] = printer
    with pytest.raises(ConfigurationError, match=f"printer.{key}"):
        _settings(printer)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("wait", [0.0, -1.0, float("inf"), float("nan")], ids=repr)
def test_the_library_refuses_a_wait_before_anything_is_sent(wait: float) -> None:
    with pytest.raises(ValueError, match="reset check wait"):
        ResetCheck(wait=wait)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("text", ["", "x" * 256], ids=len)
def test_the_library_refuses_a_text_before_anything_is_sent(text: str) -> None:
    with pytest.raises(ValueError, match="characters"):
        ResetCheck(text=text)


@pytest.mark.os_agnostic
def test_a_text_the_mapping_changes_is_a_warning_when_the_settings_are_read(caplog: pytest.LogCaptureFixture) -> None:
    arrow = chr(0x2192)
    with caplog.at_level(logging.WARNING, logger="datamax_dpl.adapters.cli.settings"):
        settings = _settings({"calibration_label_text": f"Feed {arrow}"})
    assert settings.calibration_label_text == f"Feed {arrow}"
    assert "printer.calibration_label_text" in caplog.text
    assert "U+2192" in caplog.text


@pytest.mark.os_agnostic
def test_a_text_the_mapping_leaves_alone_warns_of_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="datamax_dpl.adapters.cli.settings"):
        _settings({"calibration_label_text": "Feed"})
    assert caplog.text == ""


# ======================== A report on a directory read ========================


def _image_print(
    modules: ImageModules, *, reset: ResetCheck | None = DEFAULT_RESET, records: int | None = None
) -> tuple[RenderResult | BatchResult, ModulePrinter]:
    printer = ModulePrinter(modules)
    filled = None if records is None else [{"a": str(number)} for number in range(records)]
    elements: list[Element] = [LOGO] if filled is None else [LOGO, Text(x=5, y=30, text="{a}")]
    label = Label(width=100, height=60, elements=elements)
    options = RenderOptions(model="allegro-2", model_image_budget=False)
    _, sent, _ = _print(printer, label, options=options, records=filled, reset=reset)
    return sent, printer


@pytest.mark.os_agnostic
def test_a_report_on_the_first_label_s_directory_read_gets_the_calibration_label() -> None:
    """The status read stayed silent, so the report comes with the read after the upload, before the format."""
    modules = ImageModules(reset=POWER_ON, status=False)
    sent, printer = _image_print(modules)
    assert _codes(sent) == [FindingCode.PRINTER_RESET_CALIBRATED]
    data = printer.data
    assert data.count(CALIBRATION_TEXT) == 1
    assert data.index(STX + b"I") < data.index(CALIBRATION_TEXT)
    assert data.index(CALIBRATION_TEXT) < data.rindex(FORMAT)
    assert modules.placed_missing == []


@pytest.mark.os_agnostic
def test_a_batch_gets_one_calibration_label_for_a_report_on_a_directory_read() -> None:
    sent, printer = _image_print(ImageModules(reset=POWER_ON, status=False), records=3)
    assert printer.data.count(CALIBRATION_TEXT) == 1
    assert _codes(sent) == [FindingCode.PRINTER_RESET_CALIBRATED]


@pytest.mark.os_agnostic
def test_a_report_on_a_directory_read_with_the_key_off_only_says_so() -> None:
    sent, printer = _image_print(ImageModules(reset=POWER_ON, status=False), reset=ResetCheck(calibrate=False))
    assert CALIBRATION_TEXT not in printer.data
    assert _codes(sent) == [FindingCode.PRINTER_RESET_NOT_CALIBRATED]
    assert next(f for f in _findings(sent) if "reset" in f.code.value).severity is Severity.INFO


@pytest.mark.os_agnostic
def test_a_report_after_a_status_read_that_told_it_gets_no_second_label() -> None:
    sent, printer = _image_print(ImageModules(reset=POWER_ON))
    assert printer.data.count(CALIBRATION_TEXT) == 1
    assert _codes(sent) == [FindingCode.PRINTER_RESET_CALIBRATED]


@pytest.mark.os_agnostic
def test_a_label_refused_after_a_report_on_a_directory_read_says_the_report_is_used_up() -> None:
    """Every upload is lost: no format goes, so no label takes the shift and the next job hears nothing."""
    modules = ImageModules(reset=POWER_ON, status=False, capacity=0)
    with pytest.raises(UploadRefusedError) as refused:
        _image_print(modules)
    finding = next(f for f in _findings(refused.value.result) if "reset" in f.code.value)
    assert finding.code is FindingCode.PRINTER_RESET_NOT_CALIBRATED
    assert finding.severity is Severity.WARNING
    assert "used up" in finding.message


@pytest.mark.os_agnostic
def test_a_caller_who_asks_nothing_ignores_a_report_on_a_directory_read() -> None:
    sent, printer = _image_print(ImageModules(reset=POWER_ON, status=False), reset=None)
    assert CALIBRATION_TEXT not in printer.data
    assert _codes(sent) == []


# ======================== What print_checked refuses up front ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "check",
    [
        UploadCheck(wait=AnswerWait(seconds=0.0)),
        UploadCheck(wait=AnswerWait(seconds=float("nan"))),
        UploadCheck(wait=AnswerWait(seconds=1.0, retry_interval=-1.0)),
        UploadCheck(wait=AnswerWait(seconds=1.0), rounds=-1),
        UploadCheck(wait=AnswerWait(seconds=1.0), rounds=4),
        UploadCheck(wait=AnswerWait(seconds=1.0), idle=0.0),
    ],
    ids=["no wait", "nan wait", "negative interval", "rounds -1", "rounds 4", "no idle"],
)
def test_an_unusable_upload_check_is_refused_before_a_byte_goes_out(check: UploadCheck) -> None:
    printer = ModulePrinter(ImageModules())
    label = Label(width=100, height=60, elements=[LOGO])
    job = CheckedJob(renders=record_renders(label, None, ALLEGRO), check=check)
    with pytest.raises(ValueError, match=r"wait|interval|rounds|retry|idle"):
        print_checked(
            render_label(label, ALLEGRO),
            job,
            endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer)),
        )
    assert printer.sent == []


@pytest.mark.os_agnostic
def test_a_lone_surrogate_is_refused_up_front_and_not_at_the_reset() -> None:
    """A value that is not UTF-8 reaches argv or the environment this way, and no field can hold it."""
    with pytest.raises(ValueError, match="not text"):
        ResetCheck(text="a" + chr(0xDCFF) + "b")


_PLAIN_FIELDS: dict[str, Any] = {"width": 100.0, "height": 60.0, "elements": [Text(x=5, y=5, text="Hi")]}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mirror", True),
        ("row_offset", 10.0),
        ("column_offset", 10.0),
        ("dot_size", DotSize(width=1, height=1)),
        ("attribute", Attribute.INVERSE),
        ("count_by", 5),
        ("plain_zero", True),
    ],
    ids=["mirror", "row_offset", "column_offset", "dot_size", "attribute", "count_by", "plain_zero"],
)
def test_a_calibration_label_leaves_out_the_format_settings_of_the_job(field: str, value: object) -> None:
    """It only has to feed one label to find the edge, whatever the job does with its own."""
    options = RenderOptions(model="dmx-800")
    plain = calibration_job(Label(**_PLAIN_FIELDS), options, text="Feed")
    fancy = calibration_job(Label.model_validate({**_PLAIN_FIELDS, field: value}), options, text="Feed")
    assert fancy.data == plain.data


@pytest.mark.os_agnostic
def test_a_calibration_label_does_not_depend_on_how_often_its_job_cuts() -> None:
    options = RenderOptions(model="dmx-800")
    own = calibration_job(Label(**_PLAIN_FIELDS, cut=Cut(every=1, at_end=False)), options, text="Feed")
    job = calibration_job(Label(**_PLAIN_FIELDS, cut=Cut(every=3, at_end=True)), options, text="Feed")
    assert job.data == own.data


@pytest.mark.os_agnostic
def test_the_calibration_label_is_not_cut_though_the_job_cuts() -> None:
    """It can print shifted, so a cut would land in a label: the knife stays free of its adhesive (user, on site)."""
    options = RenderOptions(model="allegro-2", cutter_installed=True)
    label = Label(**_PLAIN_FIELDS, cut=Cut(every=3))

    job = calibration_job(label, options, text="Feed")

    assert b"\x02V0" in job.data
    assert b"\x02V1" not in job.data
    assert b"\x02o" not in job.data
    assert b"\r:0" not in job.data


@pytest.mark.os_agnostic
def test_the_calibration_label_is_not_cut_though_a_default_cut_cuts_the_job() -> None:
    """A cut the job takes from ``cut_every`` leaves the calibration label uncut too, at the job's cutter stop."""
    options = RenderOptions(model="allegro-2", cutter_installed=True, cut_every=3)

    job = calibration_job(Label(**_PLAIN_FIELDS), options, text="Feed")

    assert b"\x02V0" in job.data
    assert b"\x02V1" not in job.data
    assert b"\r:0" not in job.data
    assert b"\x02f302" in job.data


@pytest.mark.os_agnostic
def test_the_calibration_label_is_not_cut_though_its_job_is_cut_after_the_print() -> None:
    """With a cutter installed every job is cut at its end; the calibration label before it is not."""
    options = RenderOptions(model="allegro-2", cutter_installed=True)

    job = calibration_job(Label(**_PLAIN_FIELDS), options, text="Feed")

    assert b"\x02V0" in job.data
    assert b"\x02V1" not in job.data
    assert b"\r:0" not in job.data
    assert b"\x02f302" in job.data


@pytest.mark.os_agnostic
def test_the_calibration_label_stops_where_the_job_it_prepares_stops() -> None:
    options = RenderOptions(model="allegro-2", cutter_installed=True)

    job = calibration_job(Label(**_PLAIN_FIELDS, cut=Cut(every=3)), options, text="Feed")

    assert b"\x02f302" in job.data
