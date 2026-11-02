"""Records that differ between the check and the send are refused, not sent unchecked."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from printer_server import ImageModules, ModulePrinter
from pydantic import ValidationError

from datamax_dpl import (
    CheckedJob,
    ConfigurationError,
    LabelRejectedError,
    MissingFieldError,
    UploadCheck,
    check_batch,
    open_printer,
    print_checked,
)
from datamax_dpl.adapters.memory.printer import open_recording_printer
from datamax_dpl.application.use_cases import AnswerWait
from datamax_dpl.domain.label import Label, Qr, Text
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from pathlib import Path

    from datamax_dpl import BatchReport

_OPTIONS = RenderOptions(model="allegro-2")
_TEXT = Label(elements=[Text(x=10, y=10, text="{a}")])
_QR = Label(elements=[Qr(x=10, y=10, data="{a}")])
#: Longer than the largest QR Code holds, so the record renders with an error finding.
_TOO_LONG = "A" * 4000
#: What each label's format starts with.
_FORMAT = b"\x02L"


class _Passes:
    """Records that give ``passes[n]`` on the n-th read, and count the reads."""

    def __init__(self, *passes: Sequence[Mapping[str, str]]) -> None:
        self.passes = passes
        self.reads = 0

    def __iter__(self) -> Iterator[Mapping[str, str]]:
        current = self.passes[min(self.reads, len(self.passes) - 1)]
        self.reads += 1
        return iter(current)


def _rows(*values: str) -> list[dict[str, str]]:
    return [{"a": value} for value in values]


def _one_way_job(report: BatchReport) -> CheckedJob:
    return CheckedJob(renders=report.renders(), check=UploadCheck(wait=AnswerWait(seconds=1.0)))


@pytest.mark.os_agnostic
def test_record_jobs_refuses_a_record_that_has_turned_bad() -> None:
    report = check_batch(_QR, _Passes(_rows("r0", "r1"), _rows("r0", _TOO_LONG)), _OPTIONS)
    assert report.ok
    jobs = report.record_jobs()
    assert next(jobs).count(_FORMAT) == 1
    with pytest.raises(LabelRejectedError) as caught:
        next(jobs)
    assert caught.value.findings, "the findings travel with the refusal"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "second",
    [pytest.param(("r0",), id="fewer"), pytest.param(("r0", "r1", "r2"), id="more")],
)
def test_record_jobs_refuses_another_number_of_records(second: tuple[str, ...]) -> None:
    report = check_batch(_TEXT, _Passes(_rows("r0", "r1"), _rows(*second)), _OPTIONS)
    jobs = report.record_jobs()
    with pytest.raises(ConfigurationError, match="2 records"):
        list(jobs)


@pytest.mark.os_agnostic
def test_a_more_records_refusal_sends_no_record_past_the_count() -> None:
    report = check_batch(_TEXT, _Passes(_rows("r0"), _rows("r0", "EXTRA")), _OPTIONS)
    got: list[bytes] = []
    with pytest.raises(ConfigurationError):
        got.extend(report.record_jobs())
    assert not any(b"EXTRA" in job for job in got)


@pytest.mark.os_agnostic
def test_the_one_way_send_stops_at_a_record_that_has_turned_bad(tmp_path: Path) -> None:
    target = tmp_path / "job.dpl"
    report = check_batch(_QR, _Passes(_rows("r0", "r1"), _rows("r0", _TOO_LONG)), _OPTIONS)
    with pytest.raises(LabelRejectedError):
        print_checked(report, _one_way_job(report), uri=f"file:{target}", timeout=1.0, open_printer=open_printer)
    assert target.read_bytes().count(_FORMAT) == 1, "the record before it went out, and no more"


@pytest.mark.os_agnostic
def test_the_one_way_send_refuses_another_number_of_records(tmp_path: Path) -> None:
    report = check_batch(_TEXT, _Passes(_rows("r0", "r1"), _rows("r0")), _OPTIONS)
    uri = f"file:{tmp_path / 'job.dpl'}"
    with pytest.raises(ConfigurationError):
        print_checked(report, _one_way_job(report), uri=uri, timeout=1.0, open_printer=open_printer)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "second",
    [pytest.param(("r0",), id="fewer"), pytest.param(("r0", "r1", "r2"), id="more")],
)
def test_the_two_way_send_refuses_another_number_of_records(second: tuple[str, ...]) -> None:
    report = check_batch(_QR, _Passes(_rows("r0", "r1"), _rows(*second)), _OPTIONS)
    printer = ModulePrinter(ImageModules())
    job = _one_way_job(report)._replace(reset=None)
    opened = open_recording_printer(printer)
    with pytest.raises(ConfigurationError, match="holds 2 labels"):
        print_checked(report, job, uri="tcp://printer", timeout=5.0, open_printer=opened)


@pytest.mark.os_agnostic
def test_a_record_with_a_missing_field_on_the_send_raises_what_the_docs_name(tmp_path: Path) -> None:
    report = check_batch(_TEXT, _Passes(_rows("r0"), [{"b": "x"}]), _OPTIONS)
    uri = f"file:{tmp_path / 'job.dpl'}"
    with pytest.raises(MissingFieldError, match="data record 0"):
        print_checked(report, _one_way_job(report), uri=uri, timeout=1.0, open_printer=open_printer)


@pytest.mark.os_agnostic
def test_a_value_past_a_field_limit_on_the_send_raises_what_the_docs_name(tmp_path: Path) -> None:
    report = check_batch(_TEXT, _Passes(_rows("r0"), _rows("A" * 100000)), _OPTIONS)
    uri = f"file:{tmp_path / 'job.dpl'}"
    with pytest.raises(ValidationError):
        print_checked(report, _one_way_job(report), uri=uri, timeout=1.0, open_printer=open_printer)


@pytest.mark.os_agnostic
def test_the_check_reads_the_records_once() -> None:
    records = _Passes(_rows("r0", "r1"))
    check_batch(_TEXT, records, _OPTIONS)
    assert records.reads == 1


@pytest.mark.os_agnostic
def test_a_generator_is_refused_without_being_read() -> None:
    read: list[int] = []

    def rows() -> Iterator[dict[str, str]]:
        read.append(1)
        yield {"a": "r0"}

    with pytest.raises(ValueError, match="read only once"):
        check_batch(_TEXT, rows(), _OPTIONS)
    assert not read
