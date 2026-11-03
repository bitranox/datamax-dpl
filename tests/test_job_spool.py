"""A batch is rendered once per record: the check pass keeps each record in a spool, the send reads it back.

Only a label whose bytes depend on what the printer answers (its names read, a clear and upload
again, halved images) is rendered again. The spool lives in a private temp directory and is gone
when the command ends, however it ends.
"""

from __future__ import annotations

import os
import stat
import sys
import tracemalloc
from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING

import pytest
from cli_process import run_cli
from printer_server import ImageModules, ModulePrinter

from datamax_dpl import ReportLimits, check_batch, render_batch
from datamax_dpl.adapters.cli.job_file import write_batch
from datamax_dpl.adapters.cli.job_spool import FileJobSpool
from datamax_dpl.adapters.memory.printer import open_recording_printer
from datamax_dpl.application.upload_check import CheckedJob, UploadCheck, print_checked
from datamax_dpl.application.use_cases import AnswerWait, BatchEntry, BatchReport
from datamax_dpl.domain.enums import ImageModule
from datamax_dpl.domain.label import Cut, Image, Label, Qr, Text
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from pathlib import Path

    from datamax_dpl.application.ports import JobSpool

pytestmark = pytest.mark.usefixtures("no_ambient_configuration")

_DMX = RenderOptions(model="dmx-600")
_ALLEGRO = RenderOptions(model="allegro-2")
#: A plug-in module no clear may reach: each label's names are read before its downloads.
_PLUG_IN = RenderOptions(
    model="allegro-2", clear_graphics_before_label=False, clear_graphics_after_label=False, image_module=ImageModule.B
)
_TEXT = Label(elements=[Text(x=10, y=10, text="{a}")], cut=Cut(every=2))
_PATTERN = ["".join("#" if (row * 7 + column * 13) % 5 == 0 else "." for column in range(32)) for row in range(32)]
#: An image under a fixed name, and a clear before each label: its bytes need no read of the printer.
_CLEARED_IMAGE = Label(elements=[Image(x=1, y=1, name="LOGO", bitmap=_PATTERN), Text(x=10, y=40, text="{a}")])
#: A QR Code of its record's value, with no clear before it: its bytes depend on the names read.
_QR = Label(elements=[Qr(x=1, y=1, data="{a}"), Text(x=10, y=50, text="n")])
_RECORDS = 5
_FEW = ReportLimits(findings=1, downloads=1)
_SMALL = 50
_LARGE = 800
#: What the peak may grow by between the two sizes: a few small objects, nothing per record.
#: Strings cached per record grew it by about 50 bytes a record, 37 KB over these 750 records.
_FLAT_BYTES = 16 * 1024
_TCP = "tcp://192.0.2.60:9100"


class _Counted(Mapping[str, str]):
    """One record whose value is counted each time it is read: each fill of the label reads it alike."""

    def __init__(self, reads: list[int], number: int) -> None:
        self._reads = reads
        self._number = number

    def __getitem__(self, key: str) -> str:
        if key != "a":
            raise KeyError(key)
        self._reads[self._number] += 1
        return f"R{self._number}"

    def __iter__(self) -> Iterator[str]:
        return iter(("a",))

    def __len__(self) -> int:
        return 1


class _CountedRecords:
    """Records that can be read again, each counting its own fills."""

    def __init__(self, count: int) -> None:
        self.reads = [0] * count

    def __iter__(self) -> Iterator[Mapping[str, str]]:
        return (_Counted(self.reads, number) for number in range(len(self.reads)))


def _sent_two_way(report: BatchReport, printer: ModulePrinter) -> bytes:
    job = CheckedJob(renders=report.renders(), check=UploadCheck(wait=AnswerWait(seconds=1.0)), reset=None)
    print_checked(report, job, uri=_TCP, timeout=1.0, open_printer=open_recording_printer(printer))
    return printer.data


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("label", "options"), [(_TEXT, _DMX), (_CLEARED_IMAGE, _ALLEGRO)], ids=["text", "image"])
def test_a_one_way_send_renders_each_record_once_and_sends_what_it_checked(
    label: Label, options: RenderOptions
) -> None:
    records = _CountedRecords(_RECORDS)
    with FileJobSpool() as spool:
        report = check_batch(label, records, options, spool=spool)
        checked = list(records.reads)
        sent = b"".join(report.record_jobs())
    assert min(checked) > 0
    assert records.reads == checked
    assert sent == render_batch(label, [{"a": f"R{n}"} for n in range(_RECORDS)], options).data


@pytest.mark.os_agnostic
def test_a_job_written_in_place_renders_each_record_once(tmp_path: Path) -> None:
    """A target with another hard link is written in place, after a pass that checks every record."""
    target = tmp_path / "job.dpl"
    target.write_bytes(b"old")
    try:
        os.link(target, tmp_path / "other.dpl")
    except OSError as exc:
        pytest.skip(f"this file system makes no hard link: {exc}")
    records = _CountedRecords(_RECORDS)
    checked: list[int] = []

    def check(each: Callable[[BatchEntry], None]) -> BatchReport:
        report = check_batch(_TEXT, records, _DMX, each=each)
        checked.extend(records.reads)
        return report

    assert write_batch(str(target), check).ok
    assert min(checked) > 0
    assert records.reads == checked
    assert target.read_bytes() == render_batch(_TEXT, [{"a": f"R{n}"} for n in range(_RECORDS)], _DMX).data
    assert (tmp_path / "other.dpl").read_bytes() == target.read_bytes()


@pytest.mark.os_agnostic
def test_with_no_spool_the_send_renders_each_record_again() -> None:
    """The control: the counting records can tell a second render from none."""
    records = _CountedRecords(_RECORDS)
    report = check_batch(_TEXT, records, _DMX)
    checked = list(records.reads)
    list(report.record_jobs())
    assert records.reads == [2 * n for n in checked]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("label", "options"), [(_TEXT, _DMX), (_CLEARED_IMAGE, _ALLEGRO)], ids=["text", "image"])
def test_a_two_way_send_renders_a_label_no_answer_changes_once(label: Label, options: RenderOptions) -> None:
    """A label with a clear before its downloads, or with none, sends what the check rendered."""
    records = _CountedRecords(_RECORDS)
    with FileJobSpool() as spool:
        report = check_batch(label, records, options, read_names_at_send=True, spool=spool)
        checked = list(records.reads)
        sent = _sent_two_way(report, ModulePrinter(ImageModules()))
    assert records.reads == checked
    rows = [{"a": f"R{n}"} for n in range(_RECORDS)]
    again = check_batch(label, rows, options, read_names_at_send=True)
    assert sent == _sent_two_way(again, ModulePrinter(ImageModules()))


@pytest.mark.os_agnostic
def test_a_label_rendered_from_the_names_read_before_its_downloads_is_rendered_again_for_them() -> None:
    """Its bytes depend on the answer, so the one render more is the read's own."""
    records = _CountedRecords(_RECORDS)
    modules = ImageModules(held=[("B", "OTHER", 10)])
    with FileJobSpool() as spool:
        report = check_batch(_QR, records, _PLUG_IN, read_names_at_send=True, spool=spool)
        checked = list(records.reads)
        _sent_two_way(report, ModulePrinter(modules))
    assert records.reads == [2 * n for n in checked]
    assert modules.placed_missing == []
    assert modules.downloaded_held == []


@pytest.mark.os_agnostic
def test_a_refused_batch_sends_from_no_spool_and_keeps_nothing_past_its_first_error() -> None:
    too_long = "A" * 4000
    rows = [{"a": "ok"}, {"a": too_long}, {"a": "ok"}]
    label = Label(elements=[Qr(x=1, y=1, data="{a}")])
    kept: list[BatchEntry] = []

    class _Keeping(FileJobSpool):
        def add(self, entry: BatchEntry) -> None:
            kept.append(entry)
            super().add(entry)

    with _Keeping() as spool:
        report = check_batch(label, rows, _ALLEGRO, spool=spool)
    assert not report.ok
    assert len(kept) == 1
    assert report.job is not None
    assert report.job.spool is None


def _held_after_reading(records: int) -> int:
    """Traced memory still held once a batch, each record a distinct image, was checked and read back.

    What stays, not the peak: one record's render costs more than the strings a batch would keep,
    so a peak taken over the run shows the first record and hides what piles up behind it.
    """
    rows = [{"a": f"record-{number}-{'x' * 60}"} for number in range(records)]
    tracemalloc.start()
    try:
        with FileJobSpool() as spool:
            report = check_batch(_QR, rows, _PLUG_IN, read_names_at_send=True, spool=spool, limits=_FEW)
            read = sum(1 for _ in report.entries())
            held = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    assert read == records
    return held


@pytest.mark.os_agnostic
def test_reading_a_spool_back_keeps_nothing_per_record() -> None:
    """Each record's image name is a new string: none of them may stay once its record has gone."""
    _held_after_reading(_SMALL)  # warms what is imported and cached once, which is no cost of the batch
    small, large = _held_after_reading(_SMALL), _held_after_reading(_LARGE)
    assert large - small < _FLAT_BYTES, f"{small} at {_SMALL} records, {large} at {_LARGE}"


def _entry(text: str, *, cut: bool) -> BatchEntry:
    report = check_batch(Label(elements=[Text(x=1, y=1, text="{a}")]), [{"a": text}], _DMX)
    return next(iter(report.entries()))._replace(cut_after=cut)


@pytest.mark.os_agnostic
def test_the_spool_gives_back_every_record_as_added_and_each_read_starts_over() -> None:
    added = [_entry("one", cut=False), _entry("two \x00 \r\n bytes", cut=True)]
    with FileJobSpool() as spool:
        for entry in added:
            spool.add(entry)
        spool.seal()
        assert list(spool.entries()) == added
        assert list(spool.entries()) == added


@pytest.mark.os_agnostic
def test_a_spool_nothing_is_added_to_makes_no_file_and_reads_as_empty() -> None:
    with FileJobSpool() as spool:
        spool.seal()
        assert spool.path is None
        assert list(spool.entries()) == []


@pytest.mark.os_agnostic
def test_the_spool_refuses_a_read_before_it_is_sealed_and_an_add_after() -> None:
    with FileJobSpool() as spool:
        spool.add(_entry("one", cut=False))
        with pytest.raises(RuntimeError, match="sealed"):
            spool.entries()
        spool.seal()
        with pytest.raises(RuntimeError, match="sealed"):
            spool.add(_entry("two", cut=False))


@pytest.mark.os_agnostic
def test_a_spool_cut_short_is_refused_naming_the_file() -> None:
    with FileJobSpool() as spool:
        spool.add(_entry("one", cut=False))
        spool.seal()
        assert spool.path is not None
        whole = spool.path.read_bytes()
        spool.path.write_bytes(whole[:-3])
        with pytest.raises(OSError, match="ends inside a record") as raised:
            list(spool.entries())
        assert raised.value.filename == str(spool.path)


@pytest.mark.os_agnostic
def test_the_spool_is_removed_when_its_block_ends_with_a_reader_still_open() -> None:
    """A reader left mid-way is closed first: Windows removes no file a handle holds."""
    with FileJobSpool() as spool:
        spool.add(_entry("one", cut=False))
        spool.add(_entry("two", cut=False))
        spool.seal()
        path = spool.path
        assert path is not None
        reader = spool.entries()
        next(reader)
    assert not path.exists()
    assert not path.parent.exists()


@pytest.mark.os_agnostic
def test_the_spool_is_removed_when_ctrl_c_ends_its_block() -> None:
    path: Path | None = None
    with pytest.raises(KeyboardInterrupt), FileJobSpool() as spool:
        spool.add(_entry("one", cut=False))
        path = spool.path
        raise KeyboardInterrupt
    assert path is not None
    assert not path.parent.exists()


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes; Windows gives the temp dir its user's ACL")
def test_the_spool_is_readable_by_its_user_alone() -> None:
    with FileJobSpool() as spool:
        spool.add(_entry("one", cut=False))
        assert spool.path is not None
        assert stat.S_IMODE(spool.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(spool.path.parent.stat().st_mode) == 0o700


def _env_with_temp(temp: Path) -> dict[str, str]:
    """This process's environment, its temp directory moved to ``temp``: the spool goes there."""
    return {**os.environ, "TMPDIR": str(temp), "TEMP": str(temp), "TMP": str(temp)}


def _batch_files(tmp_path: Path, rows: list[str]) -> tuple[Path, Path]:
    label = tmp_path / "label.json"
    label.write_text('{"elements": [{"type": "text", "x": 10, "y": 10, "text": "{a}"}]}', encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_bytes(("a\n" + "".join(f"{row}\n" for row in rows)).encode("utf-8"))
    return label, data


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", ["print", "render"])
def test_a_command_leaves_no_spool_behind(tmp_path: Path, command: str) -> None:
    temp = tmp_path / "temp"
    temp.mkdir()
    label, data = _batch_files(tmp_path, [f"R{n}" for n in range(20)])
    out = tmp_path / "job.dpl"
    target = ["--printer", f"file:{out}"] if command == "print" else ["-o", "-"]
    finished = run_cli([command, str(label), "--data", str(data), *target], env=_env_with_temp(temp))
    assert finished.returncode == 0, finished.stderr
    assert list(temp.iterdir()) == []
    job = out.read_bytes() if command == "print" else finished.stdout
    assert all(f"R{n}".encode() in job for n in range(20))


@pytest.mark.os_agnostic
def test_a_refused_print_leaves_no_spool_behind(tmp_path: Path) -> None:
    temp = tmp_path / "temp"
    temp.mkdir()
    label = tmp_path / "label.json"
    label.write_text('{"elements": [{"type": "qr", "x": 1, "y": 1, "data": "{a}"}]}', encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_bytes(("a\nok\n" + "A" * 4000 + "\nok\n").encode("utf-8"))
    out = tmp_path / "job.dpl"
    finished = run_cli(
        ["print", str(label), "--data", str(data), "--printer", f"file:{out}", "--model", "allegro-2"],
        env=_env_with_temp(temp),
    )
    assert finished.returncode == 1, finished.stderr
    assert not out.exists()
    assert list(temp.iterdir()) == []


def _spools_of(spool: JobSpool) -> JobSpool:
    """The port as the use case sees it: the adapter is one."""
    return spool


@pytest.mark.os_agnostic
def test_the_file_spool_is_a_job_spool() -> None:
    with FileJobSpool() as spool:
        assert _spools_of(spool) is spool
