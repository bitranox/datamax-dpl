"""What a batch's report route does that only a run through it can show: held names, module C, sizes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from printer_server import ImageModules, ModulePrinter, module_printer

from datamax_dpl import (
    CheckedJob,
    LabelRejectedError,
    UploadCheck,
    check_batch,
    iter_batch,
    print_checked,
)
from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory.printer import open_recording_printer
from datamax_dpl.application.use_cases import AnswerWait
from datamax_dpl.composition import build_production
from datamax_dpl.domain.enums import ImageModule
from datamax_dpl.domain.label import Cut, Label, Qr, Text
from datamax_dpl.domain.render import build_record
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner, Result
    from printer_server import PrinterServer

_QR = Label(elements=[Qr(x=1, y=1, data="{a}")])
_TEXT = Label(elements=[Text(x=10, y=10, text="{a}")])
_ALLEGRO = RenderOptions(model="allegro-2")
_ROWS = [{"a": "r0"}, {"a": "r1"}]
#: Too long for a QR Code on this model, so the record renders with an error finding.
_TOO_LONG = "A" * 4000
_FORMAT = b"\x02L"


def _files(tmp_path: Path, label: Label, values: str) -> tuple[str, str]:
    label_path = tmp_path / "label.json"
    label_path.write_text(label.model_dump_json(), encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_bytes(f"a\n{values}".encode())
    return str(label_path), str(data)


def _name_of(value: str, options: RenderOptions) -> str:
    """The image name a QR Code holding ``value`` downloads under."""
    [name] = build_record(Label(elements=[Qr(x=1, y=1, data=value)]), options).downloads
    return name


def _codes(payload: dict[str, list[dict[str, str]]]) -> set[str]:
    return {finding["code"] for finding in payload["findings"]}


def _print(cli_runner: CliRunner, *args: str, printer: str) -> Result:
    words = ["--json-bare", *args, "--printer", printer]
    return cli_runner.invoke(cli_mod.cli, words, obj=build_production)


@pytest.mark.os_agnostic
def test_print_data_downloads_no_name_the_printer_already_holds(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[..., PrinterServer]
) -> None:
    """A download over a held name prints nothing and says nothing, with no clear running before it.

    Each label's names are read on the job's own connection, right before its downloads.
    """
    name = _name_of("r0", RenderOptions(model="allegro-2", image_module=ImageModule.B))
    modules = ImageModules(held=[("B", name, 300)])
    label, data = _files(tmp_path, _QR, "r0\nr1\n")
    server = serve(module_printer(modules))
    sets = ("--set", "printer.image_module=B", "--set", "printer.stored_names_wait=0.5")
    result = _print(cli_runner, *sets, "print", label, "--data", data, "--model", "allegro-2", printer=server.address)
    server.join()
    assert result.exit_code == 0, result.output
    assert modules.downloaded_held == []
    assert "image_name_held" in _codes(json.loads(result.stdout)["data"])


@pytest.mark.os_agnostic
def test_print_data_into_module_c_sends_nothing_and_names_the_first_record(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[..., PrinterServer]
) -> None:
    label, data = _files(tmp_path, _QR, "r0\nr1\nr2\n")
    server = serve(module_printer(ImageModules()), next_connection_within=0.1)
    sets = ("--set", "printer.image_module=C", "--set", "printer.stored_names_wait=0.3")
    result = _print(cli_runner, *sets, "print", label, "--data", data, "--model", "allegro-2", printer=server.address)
    server.join()
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)["data"]
    assert payload["bytes_sent"] == 0
    unlisted = [finding for finding in payload["findings"] if finding["code"] == "image_module_unlisted"]
    assert [finding["record"] for finding in unlisted] == [0]
    assert _FORMAT not in server.received


@pytest.mark.os_agnostic
def test_a_report_with_an_error_is_refused_with_its_findings_and_nothing_sent() -> None:
    report = check_batch(_QR, [{"a": "r0"}, {"a": _TOO_LONG}], _ALLEGRO)
    printer = ModulePrinter(ImageModules())
    job = CheckedJob(renders=report.renders(), check=UploadCheck(wait=AnswerWait(seconds=1.0)), reset=None)
    with pytest.raises(LabelRejectedError) as refused:
        print_checked(report, job, uri="tcp://printer", timeout=5.0, open_printer=open_recording_printer(printer))
    assert [finding.code.value for finding in refused.value.findings] == ["qr_data_too_long"]
    assert printer.sent == []


@pytest.mark.os_agnostic
def test_bytes_and_fields_count_the_batch_cuts_and_agree_across_commands(cli_runner: CliRunner, tmp_path: Path) -> None:
    cut = Label(elements=[Text(x=10, y=10, text="{a}")], cut=Cut(every=2))
    label, data = _files(tmp_path, cut, "r0\nr1\nr2\nr3\nr4\n")
    target = tmp_path / "job.dpl"
    common = [label, "--data", data, "--model", "dmx-600"]
    written = cli_runner.invoke(
        cli_mod.cli, ["--json-bare", "render", *common, "-o", str(target)], obj=build_production
    )
    checked = cli_runner.invoke(cli_mod.cli, ["--json-bare", "validate", *common], obj=build_production)
    rendered, validated = json.loads(written.stdout)["data"], json.loads(checked.stdout)["data"]
    assert rendered["bytes"] == validated["bytes"] == target.stat().st_size
    assert rendered["fields"] == validated["fields"] == 5


@pytest.mark.os_agnostic
def test_an_empty_batch_yields_nothing_and_has_no_report() -> None:
    assert list(iter_batch(_TEXT, [], _ALLEGRO)) == []
    with pytest.raises(ValueError, match="a batch needs at least one record"):
        check_batch(_TEXT, [], _ALLEGRO)
