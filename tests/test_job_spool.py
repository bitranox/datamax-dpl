"""A batch is rendered once per record: the check pass keeps each record in a spool, the send reads it back.

Only a label whose bytes depend on what the printer answers (its names read, a clear and upload
again, halved images) is rendered again. The spool lives in a private temp directory and is gone
when the command ends, however it ends.
"""

from __future__ import annotations

import dataclasses
import errno
import gc
import io
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
from datamax_dpl.adapters.cli.job_spool import FileJobSpool, SpoolWriteError
from datamax_dpl.adapters.cli.main import main
from datamax_dpl.adapters.memory import RecordingPrinter
from datamax_dpl.adapters.memory.printer import open_recording_printer
from datamax_dpl.application.upload_check import CheckedJob, UploadCheck, UploadRefusedError, print_checked
from datamax_dpl.application.use_cases import AnswerWait, BatchEntry, BatchReport, RecordRender, record_renders
from datamax_dpl.composition import AppServices, build_testing
from datamax_dpl.domain.enums import ImageModule
from datamax_dpl.domain.errors import ConfigurationError
from datamax_dpl.domain.label import Cut, Image, Label, Qr, Text
from datamax_dpl.domain.render import RenderResult, build_job
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from pathlib import Path

    from typing_extensions import Buffer

    from datamax_dpl.application.ports import JobSpool
    from datamax_dpl.domain.held_images import HeldImages

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
#: Data past what any QR Code holds: an error finding, which refuses the batch.
_TOO_LONG_FOR_A_QR_CODE = "A" * 4000
_FEW = ReportLimits(findings=1, downloads=1)
_SMALL = 50
_LARGE = 800
#: What the peak may grow by between the two sizes: a few small objects, nothing per record.
#: Strings cached per record grew it by about 50 bytes a record, 37 KB over these 750 records.
_FLAT_BYTES = 16 * 1024
#: Why the check skips 3.10: there what is held after reading back, after a full collection, grew by
#: about 70 KB between these sizes (3.10.22, two runs, 2026-11-03), past the bound with nothing of
#: the batch kept. 3.11 to 3.15 grew by under 2 KB.
_RAMP_310 = "3.10 holds about 70 KB more at 800 records than at 50 after a full collection, with nothing kept"
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

    def check(*, each: Callable[[BatchEntry], None] | None = None, spool: JobSpool | None = None) -> BatchReport:
        report = check_batch(_TEXT, records, _DMX, each=each, spool=spool)
        checked.extend(records.reads)
        return report

    assert write_batch(str(target), check, open_spool=FileJobSpool).ok
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


class _ChangingRecords:
    """Records that can be read again, and read otherwise each time: a query run again, say."""

    def __init__(self, count: int) -> None:
        self.count = count
        self.reads = 0

    def __iter__(self) -> Iterator[Mapping[str, str]]:
        self.reads += 1
        word = "CHECKED" if self.reads == 1 else "CHANGED"
        return iter([{"a": f"{word}{number}"} for number in range(self.count)])


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("label", "options"),
    [(_QR, _PLUG_IN), (_TEXT, _DMX), (_CLEARED_IMAGE, _ALLEGRO)],
    ids=["no-clear-qr", "text", "cleared-image"],
)
def test_a_two_way_send_from_a_spool_sends_what_was_checked_and_never_reads_the_records_again(
    label: Label, options: RenderOptions
) -> None:
    """A label rendered again for the names read before its downloads fills from the values that were checked."""
    records = _ChangingRecords(_RECORDS)
    modules = ImageModules(held=[("B", "OTHER", 10)])
    with FileJobSpool() as spool:
        report = check_batch(label, records, options, read_names_at_send=True, spool=spool)
        sent = _sent_two_way(report, ModulePrinter(modules))
    assert records.reads == 1
    assert modules.placed_missing == []
    assert modules.downloaded_held == []
    # The same batch from records that read the same each time: what the check saw, sent.
    checked = [{"a": f"CHECKED{number}"} for number in range(_RECORDS)]
    again = check_batch(label, checked, options, read_names_at_send=True)
    assert sent == _sent_two_way(again, ModulePrinter(ImageModules(held=[("B", "OTHER", 10)])))


#: Three fields of characters no code page of the model has: a warning on each, every record.
_UNMAPPED = Label(elements=[Text(x=10, y=10 + 10 * row, text="{a}") for row in range(3)])
_FEW_FINDINGS = ReportLimits(findings=4, downloads=1)


@pytest.mark.os_agnostic
def test_a_spool_keeps_counts_of_the_findings_the_report_does_not_keep_and_a_send_counts_them_all() -> None:
    rows = [{"a": "\u20ac\u0101\u4e2d"} for _ in range(_RECORDS * 4)]
    kept: list[BatchEntry] = []

    class _Watching(FileJobSpool):
        def add(self, entry: BatchEntry) -> None:
            kept.append(entry)
            super().add(entry)

    with _Watching() as spool:
        report = check_batch(_UNMAPPED, rows, _ALLEGRO, read_names_at_send=True, spool=spool, limits=_FEW_FINDINGS)
        job = CheckedJob(renders=report.renders(), check=UploadCheck(wait=AnswerWait(seconds=1.0)), reset=None)
        printer = ModulePrinter(ImageModules())
        sent = print_checked(report, job, uri=_TCP, timeout=1.0, open_printer=open_recording_printer(printer)).result
    assert report.findings_total > _FEW_FINDINGS.findings * 2
    assert sum(len(entry.result.findings) for entry in kept) == len(report.findings)
    assert sum(sum(entry.unkept.values()) for entry in kept) == report.findings_total - len(report.findings)
    assert isinstance(sent, BatchReport)
    assert (sent.findings_total, dict(sent.finding_counts)) == (report.findings_total, dict(report.finding_counts))
    assert sent.findings == report.findings


@pytest.mark.os_agnostic
def test_a_spool_takes_records_that_can_be_read_only_once() -> None:
    rows = ({"a": f"R{number}"} for number in range(_RECORDS))
    with FileJobSpool() as spool:
        report = check_batch(_TEXT, rows, _DMX, spool=spool)
        sent = b"".join(report.record_jobs())
    assert sent == render_batch(_TEXT, [{"a": f"R{n}"} for n in range(_RECORDS)], _DMX).data


@pytest.mark.os_agnostic
def test_with_no_spool_records_that_can_be_read_only_once_are_refused() -> None:
    with pytest.raises(ValueError, match="give it a spool"):
        check_batch(_TEXT, ({"a": "R"} for _ in range(2)), _DMX)


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


@pytest.mark.os_agnostic
def test_a_spool_that_drops_the_values_is_refused_before_its_record_renders_again() -> None:
    """A caller's own spool may keep the renders and drop the values a send fills the label again from."""

    class _Forgetting(FileJobSpool):
        def add(self, entry: BatchEntry) -> None:
            super().add(entry._replace(record=None))

    with _Forgetting() as spool:
        report = check_batch(_QR, [{"a": "one"}, {"a": "two"}], _ALLEGRO, spool=spool)
        assert report.ok
        renders = report.renders()
        with pytest.raises(ConfigurationError, match="data record 0 without its values"):
            next(iter(renders))


def _held_after_reading(records: int) -> int:
    """Traced memory still held once a batch, each record a distinct image, was checked and read back.

    What stays, not the peak: one record's render costs more than the strings a batch would keep,
    so a peak taken over the run shows the first record and hides what piles up behind it. Read
    after a full collection: on 3.11 to 3.13 what the interpreter keeps for reuse (free lists) and
    garbage not yet collected grew by about 140 KB between these sizes with nothing of the batch
    held, and a full collection drops all of it.
    """
    rows = [{"a": f"record-{number}-{'x' * 60}"} for number in range(records)]
    tracemalloc.start()
    try:
        with FileJobSpool() as spool:
            report = check_batch(_QR, rows, _PLUG_IN, read_names_at_send=True, spool=spool, limits=_FEW)
            read = sum(1 for _ in report.entries())
            gc.collect()
            held = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    assert read == records
    return held


@pytest.mark.os_agnostic
@pytest.mark.skipif(sys.version_info < (3, 11), reason=_RAMP_310)
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
def test_a_closed_spool_refuses_a_read_and_an_add_rather_than_reading_as_empty() -> None:
    """Its file is gone: an empty read would tell the send the batch had no records."""
    spool = FileJobSpool()
    spool.add(_entry("one", cut=False))
    spool.seal()
    spool.close()
    with pytest.raises(RuntimeError, match="closed"):
        spool.entries()
    with pytest.raises(RuntimeError, match="closed"):
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


class _FullDisk(io.FileIO):
    """A file that takes ``room`` bytes and then refuses each write, as a full disk does."""

    def __init__(self, descriptor: int, room: int) -> None:
        super().__init__(descriptor, "wb")
        self._room = room

    def write(self, data: Buffer, /) -> int:
        size = len(memoryview(data))
        if size > self._room:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        self._room -= size
        return super().write(data)


def _full_after(room: int) -> FileJobSpool:
    """A real spool whose file fills at ``room`` bytes, behind the buffer a spool writes through.

    The buffer keeps what would not go, as a real one does, so closing it flushes and fails again.
    Its size is set: the default differs between Python versions, and the write must fail in an
    add, before the check ends, not only when the spool is sealed.
    """
    return FileJobSpool(
        open_writer=lambda descriptor: io.BufferedWriter(_FullDisk(descriptor, room), buffer_size=_BUFFER_BYTES)
    )


#: The buffer in front of the spool's file in these tests: past one record, short of a few.
_BUFFER_BYTES = 2048
#: Enough records that the buffer flushes, and fails, before the check ends.
_PAST_THE_BUFFER = 64


@pytest.mark.os_agnostic
def test_a_spool_that_fills_the_disk_is_removed_and_names_itself() -> None:
    records = [{"a": f"R{n}"} for n in range(_PAST_THE_BUFFER)]
    spool = _full_after(1000)
    path: Path | None = None
    with pytest.raises(OSError, match="writing the batch's spool") as raised, spool:
        try:
            check_batch(_TEXT, records, _DMX, spool=spool)
        finally:
            path = spool.path
    assert raised.value.errno == errno.ENOSPC
    assert path is not None
    assert raised.value.filename == str(path)
    assert not path.parent.exists()


@pytest.mark.os_agnostic
def test_a_refused_batch_is_refused_although_its_spool_filled_the_disk() -> None:
    """The check goes on past the spool's failure: a refusal says more than the disk, and nothing is sent anyway."""
    records = [*({"a": f"R{n}"} for n in range(_PAST_THE_BUFFER)), {"a": _TOO_LONG_FOR_A_QR_CODE}]
    with _full_after(1000) as spool:
        report = check_batch(_QR, records, _ALLEGRO, spool=spool)
    assert not report.ok
    assert report.records == _PAST_THE_BUFFER + 1


#: Bytes short of the whole spool at which its file fills: inside the last buffer, so every add
#: goes through and only the seal's flush fails.
_SHORT_OF_THE_SPOOL = 10


def _sealed_size(args: list[str], tmp_path: Path) -> int:
    """The size the spool of the CLI run ``args`` reaches once sealed, measured with nothing in its way."""
    sizes: list[int] = []

    class _Measured(FileJobSpool):
        def seal(self) -> None:
            super().seal()
            assert self.path is not None
            sizes.append(self.path.stat().st_size)

    out = [word.format(out=tmp_path / "measured.dpl") for word in args]
    assert main(out, services_factory=lambda: dataclasses.replace(build_testing(), open_spool=_Measured)) == 0
    [size] = sizes
    return size


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "target",
    [
        pytest.param(("render", "-o", "-"), id="render-stdout"),
        pytest.param(("print", "--printer", "tcp://192.0.2.60:9100"), id="print-two-way"),
        pytest.param(("print", "--printer", "file:{out}"), id="print-one-way"),
    ],
)
def test_a_spool_that_fills_the_disk_as_it_is_sealed_sends_nothing(
    target: tuple[str, ...], tmp_path: Path, capfdbinary: pytest.CaptureFixture[bytes]
) -> None:
    """A check that missed the seal's failure would send the records the file holds, a cut job."""
    label = tmp_path / "label.json"
    label.write_text(Label(elements=[Text(x=10, y=10, text="{a}")]).model_dump_json(), encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_text("a\n" + "".join(f"r{n}\n" for n in range(300)), encoding="utf-8")
    verb, *rest = target
    args = [verb, str(label), "--data", str(data), "--model", "dmx-600", *rest]
    room = _sealed_size(args, tmp_path) - _SHORT_OF_THE_SPOOL
    capfdbinary.readouterr()
    printer = RecordingPrinter()
    out = tmp_path / "job.dpl"

    def services() -> AppServices:
        return dataclasses.replace(build_testing(printer=printer), open_spool=lambda: _full_after(room))

    code = main([word.format(out=out) for word in args], services_factory=services)
    captured = capfdbinary.readouterr()
    assert code == 2, captured.err
    assert b"writing the batch's spool" in captured.err
    assert captured.out == b""
    assert printer.sent == []
    assert not out.exists()


class _AddFails:
    """A spool whose every add fails, as a library caller's own spool on a full disk would."""

    def __init__(self) -> None:
        self.sealed = False

    def add(self, entry: BatchEntry) -> None:
        del entry
        raise OSError(errno.ENOSPC, "No space left on device", "spool")

    def seal(self) -> None:
        self.sealed = True

    def entries(self) -> Iterator[BatchEntry]:
        return iter(())


@pytest.mark.os_agnostic
def test_a_library_spool_that_fails_raises_for_a_batch_that_passed_and_not_for_one_refused() -> None:
    with pytest.raises(OSError, match="No space left"):
        check_batch(_TEXT, [{"a": "r0"}], _DMX, spool=_AddFails())
    refused = check_batch(_QR, [{"a": "r0"}, {"a": _TOO_LONG_FOR_A_QR_CODE}], _ALLEGRO, spool=_AddFails())
    assert not refused.ok


def _spool_with_a_stray_file() -> tuple[FileJobSpool, Path]:
    """A spool whose directory also holds a file of someone else's, so it cannot be removed."""
    spool = FileJobSpool()
    spool.add(_entry("one", cut=False))
    assert spool.path is not None
    stray = spool.path.parent / "stray"
    stray.write_bytes(b"")
    return spool, stray


@pytest.mark.os_agnostic
@pytest.mark.parametrize("in_flight", [True, False], ids=["exception-in-flight", "block-ended-plainly"])
def test_a_spool_that_cannot_be_removed_names_itself_and_never_hides_why_the_block_ended(in_flight: bool) -> None:
    spool, stray = _spool_with_a_stray_file()
    try:
        expected: type[BaseException] = KeyboardInterrupt if in_flight else SpoolWriteError
        with pytest.raises(expected) as raised, spool:
            if in_flight:
                raise KeyboardInterrupt
        assert not (stray.parent / "jobs").exists()
        if not in_flight:
            assert isinstance(raised.value, OSError)
            assert raised.value.filename == str(stray.parent)
            assert "removing the batch's spool" in str(raised.value)
    finally:
        stray.unlink()
        stray.parent.rmdir()


@pytest.mark.os_posix
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX modes; on Windows the ACL decides, owner-only from Python 3.12.4, inherited before (accepted)",
)
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


class _CountingSpool(FileJobSpool):
    """A real spool that counts the records added to it and those read back from it."""

    added = 0
    read = 0

    def add(self, entry: BatchEntry) -> None:
        type(self).added += 1
        super().add(entry)

    def entries(self) -> Iterator[BatchEntry]:
        for entry in super().entries():
            type(self).read += 1
            yield entry


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "target",
    [
        pytest.param(("print", "--printer", "file:{out}"), id="print-one-way"),
        pytest.param(("print", "--printer", _TCP), id="print-two-way"),
        pytest.param(("render", "-o", "-"), id="render-stdout"),
        pytest.param(("render", "-o", "{linked}"), id="render-file-direct"),
    ],
)
def test_each_cli_route_keeps_each_record_once_and_sends_it_from_the_spool(
    tmp_path: Path, target: tuple[str, ...], capfdbinary: pytest.CaptureFixture[bytes]
) -> None:
    """One render per record from the command line too: a route handed no spool renders every record twice."""
    label, data = _batch_files(tmp_path, [f"R{n}" for n in range(_RECORDS)])
    linked = tmp_path / "linked.dpl"
    linked.write_bytes(b"before")
    (tmp_path / "other-name.dpl").hardlink_to(linked)

    class _Counted(_CountingSpool):
        added = 0
        read = 0

    def services() -> AppServices:
        return dataclasses.replace(build_testing(printer=RecordingPrinter()), open_spool=_Counted)

    verb, *rest = [word.format(out=tmp_path / "job.dpl", linked=linked) for word in target]
    code = main([verb, str(label), "--data", str(data), "--model", "dmx-600", *rest], services_factory=services)
    assert code == 0, capfdbinary.readouterr().err
    assert (_Counted.added, _Counted.read) == (_RECORDS, _RECORDS)


#: Three fields a code page cannot hold each, so every record has more findings than the report keeps.
_UNMAPPABLE = Label(
    elements=[Image(x=1, y=1, name="LOGO", bitmap=_PATTERN), *(Text(x=10, y=40 + 10 * k, text="{a}") for k in range(3))]
)
_NO_CLEARS = RenderOptions(model="allegro-2", clear_graphics_before_label=False, clear_graphics_after_label=False)
_FEW_KEPT = ReportLimits(findings=4, downloads=1)
_UNMAPPABLE_ROWS = [{"a": "\u20ac\u0101\u4e2d"} for _ in range(8)]
#: What the send's own rounds add to a no-clear batch's codes: the held logo, left out from record 1 on.
_ROUND_CODES = {"image_name_held", "image_halved", "image_upload_retried", "image_upload_refused"}


def _sent(printer: ModulePrinter) -> tuple[BatchReport, BatchReport]:
    """The check's report of the unmappable batch and what its send reports, read through a real spool."""
    with FileJobSpool() as spool:
        checked = check_batch(
            _UNMAPPABLE, _UNMAPPABLE_ROWS, _NO_CLEARS, read_names_at_send=True, spool=spool, limits=_FEW_KEPT
        )
        job = CheckedJob(renders=checked.renders(), check=UploadCheck(wait=AnswerWait(seconds=1.0)), reset=None)
        try:
            sent = print_checked(
                checked, job, uri="tcp://192.0.2.60:9100", timeout=1.0, open_printer=open_recording_printer(printer)
            ).result
        except UploadRefusedError as refused:
            assert isinstance(refused.result, BatchReport)
            sent = refused.result
    assert isinstance(sent, BatchReport)
    return checked, sent


def _codes_of_the_check(report: BatchReport) -> dict[str, int]:
    return {code.value: count for code, count in report.finding_counts.items() if code.value not in _ROUND_CODES}


@pytest.mark.os_agnostic
def test_a_record_rendered_again_at_send_is_counted_once() -> None:
    """Records 1 on render again for the held logo; their findings past the report cap stay counted once."""
    checked, sent = _sent(ModulePrinter(ImageModules()))
    assert checked.findings_total > len(checked.findings)
    assert _codes_of_the_check(sent) == _codes_of_the_check(checked)
    assert {code.value for code in sent.finding_counts} - {
        code.value for code in checked.finding_counts
    } <= _ROUND_CODES


@pytest.mark.os_agnostic
def test_a_send_refused_midway_still_counts_the_records_it_did_not_send() -> None:
    """The records after the refusal are tallied from the spool, their unkept counts included."""
    checked, sent = _sent(ModulePrinter(ImageModules(silent=range(3, 1000))))
    assert any(code.value == "image_upload_refused" for code in sent.finding_counts)
    assert _codes_of_the_check(sent) == _codes_of_the_check(checked)


#: Spool bytes a record may take beyond its job: its header (the render without the job, its values,
#: the findings the report keeps) measured at about 0.56 KB for this label at both sizes.
_HEADER_ROOM = 1024
_FORTY_FIELDS = Label(elements=[Text(x=10, y=10 + 5 * k, text="{a}") for k in range(40)])


@pytest.mark.os_agnostic
@pytest.mark.parametrize("records", [50, 400])
def test_the_spool_holds_each_job_once_plus_a_header(records: int) -> None:
    """A header that carried the job's bytes again would double the spool for a label of many fields."""
    with FileJobSpool() as spool:
        report = check_batch(_FORTY_FIELDS, [{"a": f"r{n}"} for n in range(records)], _DMX, spool=spool)
        assert spool.path is not None
        size = spool.path.stat().st_size
    assert report.job_bytes < size <= report.job_bytes + records * _HEADER_ROOM


class _CallersOwnRender:
    """A caller's own render with the three methods :class:`RecordRender` names, and nothing of the spool's."""

    def __init__(self, label: Label, options: RenderOptions) -> None:
        (self._inner,) = record_renders(label, None, options)

    def first(self) -> RenderResult:
        return self._inner.first()

    def __call__(self, held: HeldImages, *, halve: bool) -> RenderResult:
        return self._inner(held, halve=halve)

    def calibration(self, text: str) -> RenderResult:
        return self._inner.calibration(text)


@pytest.mark.os_agnostic
def test_a_callers_own_record_render_sends_over_a_two_way_link() -> None:
    """The spool's counts stay off the protocol, so a render a caller writes needs only its three methods."""
    label = Label(elements=[Text(x=5, y=5, text="hi")])
    render: RecordRender = _CallersOwnRender(label, _DMX)
    printer = ModulePrinter(ImageModules())
    job = CheckedJob(renders=[render], check=UploadCheck(wait=AnswerWait(seconds=1.0)), reset=None)
    sent = print_checked(
        build_job(label, _DMX),
        job,
        uri="tcp://192.0.2.60:9100",
        timeout=1.0,
        open_printer=open_recording_printer(printer),
    )
    assert sent.bytes_sent > 0
    assert b"hi" in b"".join(printer.sent)
