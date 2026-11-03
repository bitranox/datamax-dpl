"""The edges of a batch that no other test reaches: a limit met exactly, a cap past its first error, a turned record.

Each test pins one branch a mutation left alive (sweep 5's m3, m5, m6, m7 and the held-whole report's
guard): the code was right as run, and nothing would have said so had it changed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from printer_server import ImageModules, ModulePrinter

from datamax_dpl import ReportLimits, check_batch, render_batch
from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory.printer import open_recording_printer
from datamax_dpl.application.ports import PrinterEndpoint
from datamax_dpl.application.upload_check import CheckedJob, UploadCheck, UploadRefusedError, print_checked
from datamax_dpl.application.use_cases import AnswerWait, report_of
from datamax_dpl.composition import build_production
from datamax_dpl.domain.enums import Attribute, FindingCode, Severity
from datamax_dpl.domain.label import Label, Qr, Text
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from click.testing import CliRunner, Result

pytestmark = pytest.mark.usefixtures("no_ambient_configuration")

_TEXT = Label(elements=[Text(x=1, y=1, text="{a}")])
_QR = Label(elements=[Qr(x=1, y=1, data="{a}")])
_ROWS = [{"a": "r0"}, {"a": "r1"}, {"a": "r2"}]


def _files(tmp_path: Path, label: Label) -> tuple[str, str]:
    label_path = tmp_path / "label.json"
    label_path.write_text(label.model_dump_json(), encoding="utf-8")
    data_path = tmp_path / "data.csv"
    data_path.write_text("a\n" + "".join(f"{row['a']}\n" for row in _ROWS), encoding="utf-8")
    return str(label_path), str(data_path)


def _cli(cli_runner: CliRunner, *args: str, stdin: str | None = None) -> Result:
    return cli_runner.invoke(cli_mod.cli, list(args), obj=build_production, input=stdin)


# ======================== max_batch_job_bytes, met exactly ========================


def _job_bytes() -> int:
    """What ``render --data`` with no ``-o`` counts against the key: the records' jobs, batch cuts left out."""
    return sum(len(result.data) for result in render_batch(_TEXT, _ROWS, RenderOptions(model="dmx-600")).results)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("spare", "exit_code"), [(0, 0), (-1, 2)], ids=["exactly-at-the-limit", "one-byte-over"])
def test_a_held_batch_may_reach_max_batch_job_bytes_but_not_pass_it(
    cli_runner: CliRunner, tmp_path: Path, spare: int, exit_code: int
) -> None:
    label, data = _files(tmp_path, _TEXT)
    limit = f"printer.max_batch_job_bytes={_job_bytes() + spare}"
    result = _cli(cli_runner, "--json-bare", "--set", limit, "render", label, "--data", data, "--model", "dmx-600")
    assert result.exit_code == exit_code, result.output
    document = json.loads(result.stdout)
    if exit_code:
        assert document["error"] == "BatchTooLargeError"
        assert "max_batch_job_bytes" in document["message"]
    else:
        assert document["data"]["records"] == len(_ROWS)


# ======================== the findings cap keeps the first error ========================


@pytest.mark.os_agnostic
def test_a_batch_past_its_findings_cap_still_keeps_its_first_error() -> None:
    """A refusal without its reason: under a cap of one, the findings before the error would take its place."""
    label = Label(
        elements=[
            Text(x=1, y=1, text="{a}", attribute=Attribute.OPAQUE),
            Text(x=1, y=40, text="b", attribute=Attribute.INVERSE),
        ]
    )
    report = check_batch(
        label, _ROWS[:2], RenderOptions(model="allegro-2"), limits=ReportLimits(findings=1, downloads=1)
    )
    assert not report.ok
    kept = [kept.finding for kept in report.findings]
    assert kept[0].severity is not Severity.ERROR, "the cap is spent before the error comes"
    assert [finding.severity for finding in kept].count(Severity.ERROR) == 1


# ======================== a record that reads otherwise the second time ========================


class _Turning:
    """Records a library caller can read again, the second of which reads too long for a QR Code the second time."""

    def __init__(self) -> None:
        self.reads = 0

    def __iter__(self) -> Iterator[dict[str, str]]:
        self.reads += 1
        second = "r1" if self.reads == 1 else "A" * 4000
        return iter([{"a": "r0"}, {"a": second}, {"a": "r2"}])


@pytest.mark.os_agnostic
def test_a_record_turned_bad_before_a_two_way_send_with_no_spool_stops_the_batch_there() -> None:
    records = _Turning()
    printer = ModulePrinter(ImageModules())
    options = RenderOptions(model="allegro-2")
    report = check_batch(_QR, records, options, read_names_at_send=True)
    assert report.ok
    job = CheckedJob(renders=report.renders(), check=UploadCheck(wait=AnswerWait(seconds=0.2)), reset=None)
    turned = "rendered again with what the printer then held, has errors"
    with pytest.raises(UploadRefusedError, match=turned) as raised:
        print_checked(
            report,
            job,
            endpoint=PrinterEndpoint(
                uri="tcp://192.0.2.60:9100", timeout=1.0, open_printer=open_recording_printer(printer)
            ),
        )
    assert records.reads > 1, "the send read the records again"
    assert raised.value.bytes_sent > 0, "the first label went out"
    assert b"AAAA" not in printer.data
    assert b"r2" not in printer.data


# ======================== a report held whole renders nothing again ========================


@pytest.mark.os_agnostic
def test_the_report_of_a_batch_held_whole_says_it_renders_nothing_again() -> None:
    report = report_of(render_batch(_TEXT, _ROWS, RenderOptions(model="dmx-600")))
    with pytest.raises(ValueError, match="held whole renders nothing again"):
        list(report.record_jobs())


# ======================== batch_image_names_unread on every one-way route ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "route",
    [
        pytest.param(("render", "-o", "-"), id="render-stdout"),
        pytest.param(("render",), id="render-held"),
        pytest.param(("print", "--printer", "-"), id="print-stdout"),
        pytest.param(("print", "--printer", "device://{device}"), id="print-device"),
    ],
)
def test_a_no_clear_image_batch_is_refused_on_every_one_way_route(
    cli_runner: CliRunner, tmp_path: Path, route: tuple[str, ...]
) -> None:
    """Each takes the same check as print file:, render -o FILE and validate, tested beside them."""
    label, data = _files(tmp_path, _QR)
    device = tmp_path / "device"
    command, *rest = [word.format(device=device) for word in route]
    module = ["--set", "printer.image_module=B"]
    result = _cli(cli_runner, *module, command, label, "--data", data, "--model", "allegro-2", *rest)
    assert result.exit_code == 1, result.output
    assert FindingCode.BATCH_IMAGE_NAMES_UNREAD.value in result.output
    assert b"\x02L" not in result.stdout_bytes
    assert not device.exists()
