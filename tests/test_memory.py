"""Memory module directories (``<STX>W``): parsing the answer and reading it through a printer."""

from __future__ import annotations

import pytest

from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
from datamax_dpl.application.use_cases import read_memory_directory
from datamax_dpl.domain.errors import PrinterTimeoutError, UnsupportedOperationError
from datamax_dpl.domain.memory import MemoryModule, parse_module_directory
from datamax_dpl.domain.status import MEMORY_DIRECTORY_REQUESTS

#: The manual's sample answer to ``<STX>WG`` (DPL Programmer's Manual, page 43-44) as printed:
#: no ``<CR>`` after ``AVAILABLE BYTES IN MODULE:``, so the number follows on the same line.
MANUAL_SAMPLE = (
    b"MODULE: A\rAVAILABLE BYTES IN MODULE:00049083\rMODULE: B\rLOGO\rCAM\rAVAILABLE BYTES IN MODULE:00257919\r"
)
MANUAL_MODULES = (
    MemoryModule(module="A", names=(), available_bytes=49083),
    MemoryModule(module="B", names=("LOGO", "CAM"), available_bytes=257919),
)
PRINTER = "tcp://192.0.2.60:9100"


@pytest.mark.os_agnostic
def test_the_request_letters_are_the_manuals() -> None:
    assert dict(MEMORY_DIRECTORY_REQUESTS) == {"font": b"\x02WF", "graphic": b"\x02WG", "format": b"\x02WL"}


@pytest.mark.os_agnostic
def test_the_manual_sample_parses_into_its_two_modules() -> None:
    directory = parse_module_directory(MANUAL_SAMPLE, kind="graphic")
    assert directory.parsed
    assert directory.kind == "graphic"
    assert directory.modules == MANUAL_MODULES
    assert directory.raw == MANUAL_SAMPLE.decode("ascii")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("line_break", [b"\r", b"\n", b"\r\n"], ids=["cr", "lf", "crlf"])
@pytest.mark.parametrize("after_header", [b"", b"\r", b" "], ids=["same-line", "own-line", "space"])
def test_every_line_break_and_the_number_on_its_own_line_parse_alike(line_break: bytes, after_header: bytes) -> None:
    answer = MANUAL_SAMPLE.replace(b"MODULE:0", b"MODULE:" + after_header + b"0").replace(b"\r", line_break)
    assert parse_module_directory(answer, kind="graphic").modules == MANUAL_MODULES


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "answer", [b"No Modules Available\r", b"NO MODULES AVAILABLE\r\n", b"\x11No Modules Available"]
)
def test_no_modules_available_is_an_empty_directory(answer: bytes) -> None:
    directory = parse_module_directory(answer, kind="font")
    assert directory.parsed
    assert directory.modules == ()


@pytest.mark.os_agnostic
def test_garbage_after_the_last_module_and_flow_control_bytes_are_ignored() -> None:
    answer = b"\x13" + MANUAL_SAMPLE + b"\x11\r\x02junk\r"
    directory = parse_module_directory(answer, kind="graphic")
    assert directory.parsed
    assert directory.modules == MANUAL_MODULES


@pytest.mark.os_agnostic
def test_a_module_without_its_available_bytes_line_reports_none() -> None:
    directory = parse_module_directory(b"MODULE: C\rSHIP\r", kind="format")
    assert directory.modules == (MemoryModule(module="C", names=("SHIP",), available_bytes=None),)


@pytest.mark.os_agnostic
def test_an_available_bytes_value_that_is_not_a_number_reports_none() -> None:
    directory = parse_module_directory(b"MODULE: A\rAVAILABLE BYTES IN MODULE:\r????\r", kind="graphic")
    assert directory.modules == (MemoryModule(module="A", names=(), available_bytes=None),)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("answer", [b"?", b"VER DA - 01.01\r", b"\xff\xfe"])
def test_an_answer_that_is_no_directory_is_kept_unparsed_and_not_raised(answer: bytes) -> None:
    directory = parse_module_directory(answer, kind="graphic")
    assert not directory.parsed
    assert directory.modules == ()
    assert directory.raw == answer.decode("ascii", errors="backslashreplace")


@pytest.mark.os_agnostic
def test_reading_one_kind_sends_its_request_and_parses_the_answer() -> None:
    printer = RecordingPrinter(answers={b"\x02WG": MANUAL_SAMPLE})
    directories = read_memory_directory(
        kinds=("graphic",), uri=PRINTER, timeout=2.0, idle=0.5, open_printer=open_recording_printer(printer)
    )
    assert [d.modules for d in directories] == [MANUAL_MODULES]
    assert printer.queries == [b"\x02WG"]
    assert printer.opened == [(PRINTER, 2.0)]
    assert printer.closed


@pytest.mark.os_agnostic
def test_reading_every_kind_asks_in_the_order_given_over_one_connection() -> None:
    answers = {b"\x02WF": b"No Modules Available\r", b"\x02WG": MANUAL_SAMPLE, b"\x02WL": b"MODULE: C\rSHIP\r"}
    printer = RecordingPrinter(answers=answers)
    directories = read_memory_directory(
        kinds=("font", "graphic", "format"),
        uri=PRINTER,
        timeout=2.0,
        idle=0.5,
        open_printer=open_recording_printer(printer),
    )
    assert [d.kind for d in directories] == ["font", "graphic", "format"]
    assert printer.queries == [b"\x02WF", b"\x02WG", b"\x02WL"]
    assert len(printer.opened) == 1


@pytest.mark.os_agnostic
def test_a_printer_that_does_not_answer_is_a_timeout() -> None:
    printer = RecordingPrinter()
    with pytest.raises(PrinterTimeoutError, match="no answer to"):
        read_memory_directory(
            kinds=("graphic",), uri=PRINTER, timeout=2.0, idle=0.5, open_printer=open_recording_printer(printer)
        )
    assert printer.closed


@pytest.mark.os_agnostic
def test_a_one_way_channel_cannot_read_a_directory() -> None:
    printer = RecordingPrinter(bidirectional=False)
    with pytest.raises(UnsupportedOperationError, match="cannot read the printer's answer"):
        read_memory_directory(
            kinds=("graphic",), uri=PRINTER, timeout=2.0, idle=0.5, open_printer=open_recording_printer(printer)
        )
