"""A batch goes one record at a time: nothing kept grows with the records, and it says the same as one held whole."""

from __future__ import annotations

import base64
import gc
import json
import os
import stat
import subprocess
import sys
import threading
import tracemalloc
import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from printer_server import ImageModules, ModulePrinter, module_printer

from datamax_dpl import ReportLimits, check_batch, iter_batch
from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.label_io import load_records, read_input
from datamax_dpl.adapters.memory.printer import open_recording_printer
from datamax_dpl.application.upload_check import CheckedJob, UploadCheck, UploadRefusedError, print_checked
from datamax_dpl.application.use_cases import (
    AnswerWait,
    BatchReport,
    BatchResult,
    record_renders,
    render_batch,
    report_of,
)
from datamax_dpl.composition import build_production
from datamax_dpl.domain.label import Cut, Image, Label, Qr, Text
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from click.testing import CliRunner, Result
    from printer_server import PrinterServer

pytestmark = pytest.mark.usefixtures("no_ambient_configuration")

_DMX = RenderOptions(model="dmx-600")
_ALLEGRO = RenderOptions(model="allegro-2")
_TEXT = Label(elements=[Text(x=10, y=10, text="{a}")])
_CUTS = Label(elements=[Text(x=10, y=10, text="{a}")], cut=Cut(every=2))
#: Off the print width: a warning from every record.
_WARNS = Label(elements=[Text(x=100000, y=10, text="{a}")])
_PATTERN = ["".join("#" if (row * 7 + column * 13) % 5 == 0 else "." for column in range(64)) for row in range(64)]
_IMAGE = Label(elements=[Image(x=1, y=1, name="LOGO", bitmap=_PATTERN), Text(x=10, y=10, text="{a}")])
#: A QR Code of its record's value: its image name differs per record, so held images matter.
_QR = Label(elements=[Qr(x=1, y=1, data="{a}"), Text(x=10, y=10, text="n")])
#: More than a QR Code holds: the error finding of the one record that has it.
_TOO_LONG = "A" * 4000
_BASE_PYTHON: str = getattr(sys, "_base_executable", sys.executable)


#: The record being rendered, the one read ahead to know whether it is the last, and the next one
#: as it is built, while the earlier one is still on its way out.
_MOST_ALIVE = 3


def _values(count: int) -> list[dict[str, str]]:
    return [{"a": f"r{number}"} for number in range(count)]


class _Record(dict[str, str]):
    """A record a weak reference can follow, which a plain dict cannot."""


class _CountedRecords:
    """Records built when they are read, counting how many are read and how many are alive at most."""

    def __init__(self, count: int) -> None:
        self.count = count
        self.read = 0
        self.most_alive = 0
        self._alive: list[weakref.ref[_Record]] = []

    def __iter__(self) -> Iterator[Mapping[str, str]]:
        for number in range(self.count):
            gc.collect()
            record = _Record(a=f"r{number}")
            self._alive = [*(ref for ref in self._alive if ref() is not None), weakref.ref(record)]
            self.most_alive = max(self.most_alive, len(self._alive))
            self.read += 1
            yield record
            del record


# ======================== one record at a time ========================


@pytest.mark.os_agnostic
def test_iter_batch_reads_one_record_ahead_and_keeps_no_earlier_one() -> None:
    records = _CountedRecords(30)
    for yielded, _result in enumerate(iter_batch(_TEXT, records, _DMX), start=1):
        assert records.read <= yielded + 1
    assert records.read == 30
    assert records.most_alive <= _MOST_ALIVE


@pytest.mark.os_agnostic
def test_iter_batch_takes_a_generator() -> None:
    results = list(iter_batch(_TEXT, iter(_values(3)), _DMX))
    assert [result.data for result in results] == [r.data for r in render_batch(_TEXT, _values(3), _DMX).results]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("label", "options"),
    [(_TEXT, _DMX), (_CUTS, _DMX), (_CUTS, _ALLEGRO), (_QR, _ALLEGRO), (_IMAGE, _ALLEGRO)],
    ids=["plain", "cuts", "cuts-allegro", "qr", "image"],
)
def test_iter_batch_comes_to_the_job_render_batch_holds(label: Label, options: RenderOptions) -> None:
    whole = render_batch(label, _values(5), options)
    results = list(iter_batch(label, _values(5), options))
    assert b"".join(result.data for result in results) == whole.data
    assert [r.downloads for r in results] == [r.downloads for r in whole.results]
    # The first record carries the batch's own findings, as nothing else is yielded to carry them.
    said = sorted(repr(f) for r in results for f in r.findings)
    assert said == sorted(repr(f) for f in (*whole.batch_findings, *(f for r in whole.results for f in r.findings)))


@pytest.mark.os_agnostic
def test_check_batch_keeps_no_record_and_refuses_what_it_cannot_read_twice() -> None:
    records = _CountedRecords(30)
    report = check_batch(_TEXT, records, _DMX)
    assert (report.records, report.ok, records.read) == (30, True, 30)
    assert records.most_alive <= _MOST_ALIVE
    with pytest.raises(ValueError, match="read only once"):
        check_batch(_TEXT, iter(_values(3)), _DMX)


@pytest.mark.os_agnostic
def test_a_checked_send_of_a_report_holds_only_the_current_record() -> None:
    records = _CountedRecords(20)
    report = check_batch(_QR, records, _ALLEGRO)
    printer = ModulePrinter(ImageModules())
    job = CheckedJob(renders=report.renders(), check=_check(), reset=None)
    sent = print_checked(report, job, uri="tcp://printer", timeout=5.0, open_printer=open_recording_printer(printer))
    assert sent.result.records == 20
    assert records.read == 40
    assert records.most_alive <= _MOST_ALIVE


def _check() -> UploadCheck:
    return UploadCheck(wait=AnswerWait(seconds=1.0), rounds=3)


# ======================== the same answer as a batch held whole ========================


def _sent_whole(modules: ImageModules, label: Label, options: RenderOptions) -> tuple[bytes, BatchReport, bool]:
    """Print the batch held whole; return what went out, its report and whether it all went."""
    printer = ModulePrinter(modules)
    records = _values(5)
    held = render_batch(label, records, options)
    job = CheckedJob(renders=record_renders(label, records, options), check=_check(), reset=None)
    opened = open_recording_printer(printer)
    try:
        result: BatchResult = print_checked(held, job, uri="tcp://printer", timeout=5.0, open_printer=opened).result
    except UploadRefusedError as refused:
        assert isinstance(refused.result, BatchResult)
        return b"".join(printer.sent), report_of(refused.result, limits=ReportLimits()), False
    return b"".join(printer.sent), report_of(result, limits=ReportLimits()), True


def _sent_report(modules: ImageModules, label: Label, options: RenderOptions) -> tuple[bytes, BatchReport, bool]:
    """Print the batch one record at a time; return what went out, its report and whether it all went."""
    printer = ModulePrinter(modules)
    report = check_batch(label, _values(5), options)
    job = CheckedJob(renders=report.renders(), check=_check(), reset=None)
    opened = open_recording_printer(printer)
    try:
        result = print_checked(report, job, uri="tcp://printer", timeout=5.0, open_printer=opened).result
    except UploadRefusedError as refused:
        assert isinstance(refused.result, BatchReport)
        return b"".join(printer.sent), refused.result, False
    return b"".join(printer.sent), result, True


def _kept(report: BatchReport) -> list[Any]:
    return [
        report.records,
        report.job_bytes,
        report.ok,
        report.downloads,
        report.findings,
        report.batch_findings,
        dict(report.finding_counts),
    ]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "modules",
    [
        ImageModules,
        lambda: ImageModules(capacity=400),
        lambda: ImageModules(capacity=0),
        lambda: ImageModules(lose=[0]),
        lambda: ImageModules(lose=[2]),
        lambda: ImageModules(silent=[0]),
    ],
    ids=["plain", "halved", "refused", "first-upload-lost", "later-upload-lost", "first-read-silent"],
)
@pytest.mark.parametrize("label", [_IMAGE, _QR], ids=["image", "qr"])
def test_a_report_sends_and_says_what_a_batch_held_whole_does(
    label: Label, modules: Callable[[], ImageModules]
) -> None:
    whole = _sent_whole(modules(), label, _ALLEGRO)
    streamed = _sent_report(modules(), label, _ALLEGRO)
    assert streamed[0] == whole[0]
    assert streamed[2] == whole[2]
    assert _kept(streamed[1]) == _kept(whole[1])


# ======================== what the report keeps ========================


@pytest.mark.os_agnostic
def test_a_report_keeps_the_first_findings_and_counts_them_all() -> None:
    report = check_batch(_WARNS, _values(10), _DMX, limits=ReportLimits(findings=3))
    whole = report_of(render_batch(_WARNS, _values(10), _DMX), limits=ReportLimits(findings=10**6))
    assert len(report.findings) == 3
    assert report.findings == whole.findings[:3]
    assert report.findings_total == whole.findings_total > 3
    assert dict(report.finding_counts) == dict(whole.finding_counts)
    assert sum(report.finding_counts.values()) == report.findings_total


@pytest.mark.os_agnostic
def test_an_error_past_the_limit_still_refuses_and_is_kept() -> None:
    records = [*_values(4), {"a": _TOO_LONG}, *_values(2)]
    report = check_batch(_QR, records, _ALLEGRO, limits=ReportLimits(findings=1))
    assert report.ok is False
    assert len(report.findings) == 2
    assert report.findings[-1].record == 4
    assert report.findings[-1].finding.severity.value == "error"


@pytest.mark.os_agnostic
def test_a_report_keeps_the_first_image_names_and_counts_them_all() -> None:
    report = check_batch(_QR, _values(5), _ALLEGRO, limits=ReportLimits(downloads=2))
    assert len(report.downloads) == 2
    assert report.downloads_total == 5


def _files(tmp_path: Path, label: Label, rows: str) -> tuple[str, str]:
    label_path = tmp_path / "label.json"
    label_path.write_text(label.model_dump_json(), encoding="utf-8")
    data_path = tmp_path / "data.csv"
    data_path.write_text("a\n" + rows, encoding="utf-8")
    return str(label_path), str(data_path)


def _run(cli_runner: CliRunner, *args: str) -> Result:
    return cli_runner.invoke(cli_mod.cli, list(args), obj=build_production)


@pytest.mark.os_agnostic
def test_the_envelope_says_what_it_left_out_and_names_the_keys(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _QR, "".join(f"r{n}\n" for n in range(6)))
    limits = ("--set", "printer.max_reported_findings=2", "--set", "printer.max_reported_downloads=3")
    result = _run(cli_runner, "--json-bare", *limits, "validate", label, "--data", data, "--model", "allegro-2")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)["data"]
    assert payload["records"] == 6
    assert len(payload["findings"]) == 2 < payload["findings_total"]
    assert sum(payload["findings_by_code"].values()) == payload["findings_total"]
    assert len(payload["downloads"]) == 3
    assert payload["downloads_total"] == 6
    assert "[printer] max_reported_findings" in payload["report_note"]
    assert "[printer] max_reported_downloads" in payload["report_note"]


@pytest.mark.os_agnostic
def test_the_human_output_says_what_it_left_out(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _WARNS, "".join(f"r{n}\n" for n in range(6)))
    limit = ("--set", "printer.max_reported_findings=2")
    result = _run(cli_runner, *limit, "validate", label, "--data", data, "--model", "dmx-600")
    assert "findings shown" in result.stdout
    assert "[printer] max_reported_findings" in result.stdout


@pytest.mark.os_agnostic
def test_an_envelope_that_left_out_nothing_has_no_note(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _TEXT, "r0\nr1\n")
    result = _run(cli_runner, "--json-bare", "validate", label, "--data", data, "--model", "dmx-600")
    payload = json.loads(result.stdout)["data"]
    assert payload["report_note"] is None
    assert payload["findings_total"] == len(payload["findings"])
    assert payload["downloads_total"] == 0


# ======================== nothing goes out before every record passed ========================


@pytest.mark.os_agnostic
def test_print_refuses_a_bad_last_record_before_any_byte(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[..., PrinterServer]
) -> None:
    label, data = _files(tmp_path, _QR, f"r0\nr1\n{_TOO_LONG}\n")
    server = serve(module_printer(ImageModules()), next_connection_within=0.1)
    result = _run(
        cli_runner, "--json-bare", "print", label, "--data", data, "--model", "allegro-2", "--printer", server.address
    )
    server.join()
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["data"]["bytes_sent"] is None
    # What a pre-job read asks for is a request; no job byte, no format and no image went out.
    assert b"\x02L" not in server.received
    assert b"\x02I" not in server.received


# ======================== render -o ========================


def _job_of(cli_runner: CliRunner, label: str, data: str, model: str) -> bytes:
    held = _run(cli_runner, "--json-bare", "render", label, "--data", data, "--model", model)
    assert held.exit_code == 0, held.output
    return base64.b64decode(json.loads(held.stdout)["data"]["dpl_base64"])


@pytest.mark.os_agnostic
def test_render_to_a_file_writes_the_job_it_would_hold(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _QR, "r0\nr1\nr2\n")
    target = tmp_path / "job.dpl"
    result = _run(cli_runner, "--json-bare", "render", label, "--data", data, "--model", "allegro-2", "-o", str(target))
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)["data"]
    assert document["output"] == str(target)
    assert document["dpl"] is None
    assert target.read_bytes() == _job_of(cli_runner, label, data, "allegro-2")
    assert [path.name for path in tmp_path.iterdir() if path.name.endswith(".tmp")] == []


@pytest.mark.os_agnostic
def test_a_bad_last_record_leaves_the_file_as_it_was(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _QR, f"r0\nr1\n{_TOO_LONG}\n")
    target = tmp_path / "job.dpl"
    target.write_bytes(b"before")
    result = _run(cli_runner, "--json-bare", "render", label, "--data", data, "--model", "allegro-2", "-o", str(target))
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["data"]["output"] is None
    assert target.read_bytes() == b"before"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["data.csv", "job.dpl", "label.json"]


@pytest.mark.os_agnostic
def test_a_bad_last_record_creates_no_file(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _QR, f"r0\n{_TOO_LONG}\n")
    target = tmp_path / "job.dpl"
    _run(cli_runner, "--json-bare", "render", label, "--data", data, "--model", "allegro-2", "-o", str(target))
    assert sorted(path.name for path in tmp_path.iterdir()) == ["data.csv", "label.json"]


@pytest.mark.os_agnostic
def test_a_target_that_cannot_be_written_is_named_in_the_error(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _TEXT, "r0\n")
    target = tmp_path / "no" / "such" / "dir" / "job.dpl"
    result = _run(cli_runner, "--json-bare", "render", label, "--data", data, "--model", "dmx-600", "-o", str(target))
    assert result.exit_code != 0
    assert "job.dpl" in result.stdout


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no mode bits for chmod to keep")
def test_a_replaced_file_keeps_its_mode(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _TEXT, "r0\n")
    target = tmp_path / "job.dpl"
    target.write_bytes(b"before")
    target.chmod(0o640)
    result = _run(cli_runner, "--json-bare", "render", label, "--data", data, "--model", "dmx-600", "-o", str(target))
    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert target.read_bytes() != b"before"


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="a symbolic link needs a privilege Windows runners may not hold")
def test_a_link_is_written_through_not_replaced(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _TEXT, "r0\n")
    real = tmp_path / "real.dpl"
    real.write_bytes(b"before")
    link = tmp_path / "link.dpl"
    link.symlink_to(real)
    result = _run(cli_runner, "--json-bare", "render", label, "--data", data, "--model", "dmx-600", "-o", str(link))
    assert result.exit_code == 0, result.output
    assert link.is_symlink()
    assert real.read_bytes() != b"before"


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="/dev/null exists only on POSIX; NUL is not a path a stat can read")
def test_a_device_is_written_not_replaced(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Replacing /dev/null needs root, and as root it would destroy it: the job is checked, then written."""
    label, data = _files(tmp_path, _TEXT, "r0\n")
    before = Path("/dev/null").stat()
    result = _run(cli_runner, "--json-bare", "render", label, "--data", data, "--model", "dmx-600", "-o", "/dev/null")
    assert result.exit_code == 0, result.output
    assert Path("/dev/null").stat().st_ino == before.st_ino
    assert stat.S_ISCHR(Path("/dev/null").stat().st_mode)


@pytest.mark.os_posix
def test_a_pipe_is_written_not_replaced(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A reader on a named pipe must get the job through the pipe, with no file taking its name."""
    if sys.platform == "win32":
        pytest.skip("Windows has no named pipes in the file system")
    label, data = _files(tmp_path, _TEXT, "r0\nr1\n")
    pipe = tmp_path / "job.pipe"
    os.mkfifo(pipe)
    got: list[bytes] = []
    reader = threading.Thread(target=lambda: got.append(pipe.read_bytes()), daemon=True)
    reader.start()
    result = _run(cli_runner, "--json-bare", "render", label, "--data", data, "--model", "dmx-600", "-o", str(pipe))
    reader.join(timeout=30)
    assert result.exit_code == 0, result.output
    assert stat.S_ISFIFO(pipe.stat().st_mode)
    assert got == [_job_of(cli_runner, label, data, "dmx-600")]


@pytest.mark.os_agnostic
def test_render_to_stdout_checks_every_record_first(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _QR, f"r0\nr1\n{_TOO_LONG}\n")
    result = cli_runner.invoke(
        cli_mod.cli, ["render", label, "--data", data, "--model", "allegro-2", "-o", "-"], obj=build_production
    )
    assert result.exit_code == 1, result.output
    assert result.stdout_bytes == b""


@pytest.mark.os_agnostic
def test_render_to_stdout_streams_the_job_it_would_hold(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _QR, "r0\nr1\n")
    result = cli_runner.invoke(
        cli_mod.cli, ["render", label, "--data", data, "--model", "allegro-2", "-o", "-"], obj=build_production
    )
    assert result.exit_code == 0, result.output
    assert result.stdout_bytes == _job_of(cli_runner, label, data, "allegro-2")


@pytest.mark.os_agnostic
def test_render_to_stdout_with_an_envelope_is_refused_before_writing_anything(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    label, data = _files(tmp_path, _TEXT, "r0\n")
    result = _run(cli_runner, "--json-bare", "render", label, "--data", data, "--model", "dmx-600", "-o", "-")
    assert result.exit_code == 2, result.output
    assert "stdout carries the envelope" in json.loads(result.stdout)["message"]


# ======================== reading the records ========================


@pytest.mark.os_agnostic
def test_records_can_be_read_again(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("a\nr0\nr1\n", encoding="utf-8")
    records = load_records(str(path))
    assert list(records) == list(records) == [{"a": "r0"}, {"a": "r1"}]
    assert len(records) == 2


@pytest.mark.os_agnostic
def test_a_csv_row_is_parsed_only_when_it_is_reached(tmp_path: Path) -> None:
    path = tmp_path / "data.jsonl"
    path.write_text('{"a": "r0"}\n{"a": "r1"}\n{"a": "r2"}\n', encoding="utf-8")
    reader = iter(load_records(str(path)))
    assert next(reader) == {"a": "r0"}
    assert next(reader) == {"a": "r1"}


@pytest.mark.os_agnostic
def test_stdin_is_read_once_and_gone_over_twice(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, _ = _files(tmp_path, _TEXT, "")
    target = tmp_path / "job.dpl"
    result = cli_runner.invoke(
        cli_mod.cli,
        ["--json-bare", "print", label, "--data", "-", "--model", "dmx-600", "--printer", f"file:{target}"],
        input='{"a": "r0"}\n{"a": "r1"}\n',
        obj=build_production,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)["data"]
    assert payload["records"] == 2
    assert b"r0" in target.read_bytes()
    assert b"r1" in target.read_bytes()


@pytest.mark.os_agnostic
def test_a_small_file_is_read_without_room_for_the_largest(tmp_path: Path) -> None:
    """``read(limit)`` allocates the limit first: a measure of one small file used to show 16 MiB."""
    path = tmp_path / "data.csv"
    path.write_bytes(b"a\nr0\n")
    tracemalloc.start()
    try:
        assert read_input(str(path)) == b"a\nr0\n"
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024


# ======================== peak memory ========================


def _peak(command: str, records: int, directory: Path) -> int:
    directory.mkdir()
    answer = subprocess.run(
        [sys.executable, "-m", "batch_peak_probe"],
        executable=_BASE_PYTHON,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(sys.path),
            "PEAK_COMMAND": command,
            "PEAK_RECORDS": str(records),
            "PEAK_DIRECTORY": str(directory),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=300,
    )
    assert answer.returncode == 0, answer.stderr
    return int(answer.stdout.split()[-1])


#: Records of the two runs compared: few enough for the suite, where a held job already shows.
_FEW, _MANY = 50, 250
#: The larger run may peak this many times the smaller: a job kept in memory measured 1.8-2.2 here.
_MOST_GROWTH = 1.5


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", ["validate", "render", "render-stdout", "print", "print-one-way", "library-one-way"])
def test_peak_memory_does_not_grow_with_the_records(command: str, tmp_path: Path) -> None:
    few = _peak(command, _FEW, tmp_path / "few")
    many = _peak(command, _MANY, tmp_path / "many")
    assert many < few * _MOST_GROWTH, (few, many)


#: What ``render`` with no ``-o`` may hold above a command that holds no job, in jobs: the job, its
#: readable text and what writing that text takes. Its record results and base64 made it 8.
_MOST_JOBS_HELD_IN_HUMAN_MODE = 5


@pytest.mark.os_agnostic
def test_render_in_human_mode_holds_the_job_and_its_text_and_little_else(tmp_path: Path) -> None:
    """No base64 it never shows, and no record results: 11 times the job came down to under 5."""
    plain = _peak("render", _MANY, tmp_path / "plain")
    job = (tmp_path / "plain" / "job.dpl").stat().st_size
    held = _peak("render-held-human", _MANY, tmp_path / "held")
    assert held - plain < _MOST_JOBS_HELD_IN_HUMAN_MODE * job, (plain, held, job)
