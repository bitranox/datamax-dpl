"""RecordingPrinter: the in-memory printer channel the CLI and use-case tests drive."""

from __future__ import annotations

import pytest

from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
from datamax_dpl.domain.errors import PrinterTimeoutError, TransportError, UnsupportedOperationError
from datamax_dpl.domain.status import STATUS_REQUEST, VERSION_REQUEST, parse_ascii_status


@pytest.mark.os_agnostic
def test_every_send_is_recorded_in_order() -> None:
    printer = RecordingPrinter()
    printer.send(b"\x02L\r")
    printer.send(b"E\r")
    assert printer.sent == [b"\x02L\r", b"E\r"]
    assert printer.data == b"\x02L\rE\r"


@pytest.mark.os_agnostic
def test_a_query_is_answered_from_the_table_and_recorded_on_the_wire() -> None:
    printer = RecordingPrinter(answers={STATUS_REQUEST: b"NYNNNNNN\r"})
    answer = printer.query(STATUS_REQUEST, timeout=1.0)
    assert parse_ascii_status(answer).paper_out
    assert printer.queries == [STATUS_REQUEST]
    assert printer.sent == [STATUS_REQUEST]


@pytest.mark.os_agnostic
def test_a_request_missing_from_the_table_behaves_like_a_silent_printer() -> None:
    printer = RecordingPrinter(answers={STATUS_REQUEST: b"NNNNNNNN\r"})
    with pytest.raises(PrinterTimeoutError, match="no answer"):
        printer.query(VERSION_REQUEST, timeout=0.5)
    assert printer.queries == [VERSION_REQUEST]


@pytest.mark.os_agnostic
def test_a_one_way_recording_printer_refuses_queries_and_records_nothing() -> None:
    printer = RecordingPrinter(answers={STATUS_REQUEST: b"NNNNNNNN\r"}, bidirectional=False)
    assert not printer.bidirectional
    with pytest.raises(UnsupportedOperationError):
        printer.query(STATUS_REQUEST, timeout=1.0)
    assert printer.sent == []


@pytest.mark.os_agnostic
def test_the_answer_table_is_copied_at_construction() -> None:
    answers = {STATUS_REQUEST: b"NNNNNNNN\r"}
    printer = RecordingPrinter(answers=answers)
    answers.clear()
    assert printer.query(STATUS_REQUEST, timeout=1.0) == b"NNNNNNNN\r"


@pytest.mark.os_agnostic
def test_a_closed_recording_printer_refuses_like_a_real_channel() -> None:
    printer = RecordingPrinter(answers={STATUS_REQUEST: b"NNNNNNNN\r"})
    with printer:
        printer.send(b"x")
    assert printer.closed
    printer.close()
    with pytest.raises(TransportError, match="the transport is closed"):
        printer.send(b"y")
    with pytest.raises(TransportError, match="the transport is closed"):
        printer.query(STATUS_REQUEST, timeout=1.0)
    assert printer.sent == [b"x"]


@pytest.mark.os_agnostic
def test_the_opener_records_each_open_and_reopens_the_same_printer() -> None:
    printer = RecordingPrinter()
    opener = open_recording_printer(printer)
    first = opener("tcp://printer:9100", timeout=2.0)
    first.send(b"one")
    first.close()
    second = opener("-", timeout=3.0)
    second.send(b"two")
    assert first is second is printer
    assert printer.opened == [("tcp://printer:9100", 2.0), ("-", 3.0)]
    assert printer.data == b"onetwo"


@pytest.mark.os_agnostic
def test_read_answer_returns_the_whole_answer_for_the_exact_request() -> None:
    printer = RecordingPrinter(answers={b"\x02WG": b"MODULE: A\rAVAILABLE BYTES IN MODULE:00049083\r"})
    assert printer.read_answer(b"\x02WG", timeout=1.0, idle=0.2) == b"MODULE: A\rAVAILABLE BYTES IN MODULE:00049083\r"
    assert printer.queries == [b"\x02WG"]
    assert printer.sent == [b"\x02WG"]
    with pytest.raises(PrinterTimeoutError, match="no answer to"):
        printer.read_answer(b"\x02WF", timeout=1.0, idle=0.2)


@pytest.mark.os_agnostic
def test_a_one_way_recording_printer_refuses_read_answer() -> None:
    printer = RecordingPrinter(answers={b"\x02WG": b"x"}, bidirectional=False)
    with pytest.raises(UnsupportedOperationError, match="cannot read the printer's answer"):
        printer.read_answer(b"\x02WG", timeout=1.0, idle=0.2)
    assert printer.sent == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("seconds", "phrase"),
    [
        ({"timeout": 0.0}, "timeout must be above 0"),
        ({"idle": -1.0}, "idle must be above 0"),
        ({"total": 0.0}, "total must be above 0"),
    ],
)
def test_read_answer_checks_its_seconds_like_a_real_channel(seconds: dict[str, float], phrase: str) -> None:
    printer = RecordingPrinter(answers={b"\x02WG": b"x"})
    arguments = {"timeout": 1.0, "idle": 0.2, **seconds}
    with pytest.raises(ValueError, match=phrase):
        printer.read_answer(b"\x02WG", **arguments)
    assert printer.sent == []
