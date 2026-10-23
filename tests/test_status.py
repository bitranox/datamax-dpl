"""Status answers: real byte strings, including garbage, truncated, over-long and non-ASCII ones."""

from __future__ import annotations

import contextlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from datamax_dpl.domain.errors import PrinterProtocolError
from datamax_dpl.domain.status import (
    BATCH_REQUEST,
    CANCEL,
    CUT_NOW,
    FORM_FEED,
    MEMORY_REQUEST,
    RESET,
    SERIAL_TEST_REQUEST,
    STATUS_BYTE_REQUEST,
    STATUS_REQUEST,
    TOGGLE_PAUSE,
    VERSION_REQUEST,
    MemoryReport,
    PrinterStatus,
    parse_ascii_status,
    parse_batch_remaining,
    parse_memory_report,
    parse_status_byte,
    parse_version,
)

FLAG_ORDER = (
    "interpreter_busy",
    "paper_out",
    "ribbon_out",
    "printing_batch",
    "busy_printing",
    "paused",
    "label_presented",
)


def _status(**set_flags: bool) -> PrinterStatus:
    values: dict[str, bool] = dict.fromkeys(FLAG_ORDER, False)
    values.update(set_flags)
    return PrinterStatus.model_validate(values)


# --- request constants -------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_request_constants_are_the_dpl_bytes() -> None:
    assert STATUS_REQUEST == b"\x01A"
    assert STATUS_BYTE_REQUEST == b"\x01F"
    assert BATCH_REQUEST == b"\x01E"
    assert VERSION_REQUEST == b"\x02v"
    assert MEMORY_REQUEST == b"\x02KQ"
    assert SERIAL_TEST_REQUEST == b"\x02k"
    assert (RESET, CANCEL, TOGGLE_PAUSE, FORM_FEED, CUT_NOW) == (b"\x01#", b"\x01C", b"\x01B", b"\x02F", b"\x02o")


# --- <SOH>A ------------------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize("position", range(7))
def test_each_ascii_status_position_sets_its_own_flag(position: int) -> None:
    answer = bytearray(b"NNNNNNNN\r")
    answer[position] = ord("Y")
    assert parse_ascii_status(bytes(answer)) == _status(**{FLAG_ORDER[position]: True})


@pytest.mark.os_agnostic
def test_an_all_clear_ascii_status_is_idle_with_no_problems() -> None:
    status = parse_ascii_status(b"NNNNNNNN\r")
    assert status.idle
    assert status.problems == ()


@pytest.mark.os_agnostic
def test_flow_control_bytes_anywhere_in_an_ascii_status_are_dropped() -> None:
    assert parse_ascii_status(b"\x11\x13NYN\x11NNNNN\r") == _status(paper_out=True)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("prefix", "meaning"),
    [
        (b"c", "framing error"),
        (b"v", "input buffer overflow"),
        (b"I", "invalid command sequence"),
        (b"R", "power-on reset"),
        (b"T", "software reset"),
        (b"\x07", "bad image download"),
    ],
)
def test_an_error_character_before_an_ascii_status_is_named(prefix: bytes, meaning: str) -> None:
    with pytest.raises(PrinterProtocolError, match=meaning):
        parse_ascii_status(prefix + b"NNNNNNNN\r")


@pytest.mark.os_agnostic
def test_an_error_character_mixed_with_garbage_is_still_named() -> None:
    with pytest.raises(PrinterProtocolError, match="framing error and unexpected bytes"):
        parse_ascii_status(b"cZNNNNNNNN\r")


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "answer",
    [
        b"",
        b"\r",
        b"NNNNNNNN",  # truncated: no terminator
        b"NNNNNNN\r",  # truncated: seven flags
        b"YNNNNNNNN\r",  # over-long: nine flags
        b"NNNNNNNY\r",  # reserved eighth flag set
        b"nnnnnnnn\r",  # lower case is not in the manual
        b"NNNN NNN\r",
        b"NNNNNNNN\r\n",
        b"ZZZZZZZZZZZZ\r",
        bytes([0xC3, 0xA4]) + b"NNNNNNNN\r",  # non-ASCII in front
        b"NNNNNNN" + bytes([0xE4]) + b"\r",  # non-ASCII inside
        b"x" * 5000 + b"NNNNNNNN\r",
    ],
)
def test_an_ascii_status_outside_the_manual_is_refused(answer: bytes) -> None:
    with pytest.raises(PrinterProtocolError):
        parse_ascii_status(answer)


@pytest.mark.os_agnostic
def test_a_long_garbage_answer_is_quoted_short_in_the_message() -> None:
    with pytest.raises(PrinterProtocolError) as caught:
        parse_ascii_status(b"x" * 5000 + b"NNNNNNNN\r")
    assert len(str(caught.value)) < 300
    assert "5009 bytes" in str(caught.value)


# --- <SOH>F ------------------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize("bit", range(7))
def test_each_status_bit_sets_the_flag_in_the_ascii_order(bit: int) -> None:
    assert parse_status_byte(bytes([1 << bit]) + b"\r") == _status(**{FLAG_ORDER[bit]: True})


@pytest.mark.os_agnostic
@given(value=st.integers(min_value=0, max_value=0x7F))
def test_the_status_byte_and_the_ascii_status_agree(value: int) -> None:
    ascii_answer = bytes(ord("Y") if value >> bit & 1 else ord("N") for bit in range(7)) + b"N\r"
    assert parse_status_byte(bytes([value]) + b"\r") == parse_ascii_status(ascii_answer)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("value", [0x11, 0x13])
def test_a_status_byte_equal_to_xon_or_xoff_is_read_as_status(value: int) -> None:
    status = parse_status_byte(bytes([value]) + b"\r")
    assert status.interpreter_busy
    assert status.busy_printing


@pytest.mark.os_agnostic
def test_flow_control_in_front_of_the_status_byte_is_dropped() -> None:
    assert parse_status_byte(b"\x11\x13\x04\r") == _status(ribbon_out=True)


@pytest.mark.os_agnostic
def test_an_error_character_in_front_of_the_status_byte_is_named() -> None:
    with pytest.raises(PrinterProtocolError, match="input buffer overflow"):
        parse_status_byte(b"v\x00\r")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("answer", [b"", b"\r", b"\x00", b"\x80\r", b"\xff\r", b"Z\x00\r", b"\x00\r\r"])
def test_a_status_byte_outside_the_manual_is_refused(answer: bytes) -> None:
    with pytest.raises(PrinterProtocolError):
        parse_status_byte(answer)


# --- PrinterStatus -----------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize("flag", ["interpreter_busy", "printing_batch", "busy_printing"])
def test_any_busy_flag_makes_the_printer_not_idle(flag: str) -> None:
    assert not _status(**{flag: True}).idle


@pytest.mark.os_agnostic
@pytest.mark.parametrize("flag", ["paper_out", "ribbon_out", "paused", "label_presented"])
def test_a_non_busy_flag_keeps_the_printer_idle(flag: str) -> None:
    assert _status(**{flag: True}).idle


@pytest.mark.os_agnostic
def test_problems_lists_every_condition_that_needs_a_person_in_order() -> None:
    status = _status(paused=True, paper_out=True, ribbon_out=True, label_presented=True)
    assert status.problems == ("paper_out", "ribbon_out", "paused")


@pytest.mark.os_agnostic
def test_printer_status_is_frozen_and_so_hashable() -> None:
    assert hash(_status(paused=True)) == hash(_status(paused=True))


# --- <SOH>E ------------------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("answer", "count"), [(b"0000\r", 0), (b"0012\r", 12), (b"9999\r", 9999), (b"\x110042\x13\r", 42)]
)
def test_the_batch_count_is_read(answer: bytes, count: int) -> None:
    assert parse_batch_remaining(answer) == count


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "answer",
    [
        b"",
        b"12\r",
        b"0012",
        b"00012\r",
        b"00 12\r",
        b"-012\r",
        b"+012\r",
        b"001a\r",
        ("00" + chr(0x0662) * 2).encode() + b"\r",
    ],
)
def test_a_batch_count_outside_the_manual_is_refused(answer: bytes) -> None:
    with pytest.raises(PrinterProtocolError):
        parse_batch_remaining(answer)


@pytest.mark.os_agnostic
def test_a_reset_report_before_the_batch_count_is_named() -> None:
    with pytest.raises(PrinterProtocolError, match="power-on reset"):
        parse_batch_remaining(b"R0012\r")


# --- <STX>v ------------------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_the_version_text_is_returned_stripped() -> None:
    assert parse_version(b"\x11 VER DA - 01.01 12/21/93 \r") == "VER DA - 01.01 12/21/93"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "answer",
    [b"", b"\r", b"   \r", b"VER DA", b"VER\x00DA\r", b"VER " + bytes([0xE4]) + b"\r", b"VER\rDA\r", b"VER\tDA\r"],
)
def test_a_version_outside_printable_ascii_is_refused(answer: bytes) -> None:
    with pytest.raises(PrinterProtocolError):
        parse_version(answer)


@pytest.mark.os_agnostic
def test_a_bel_before_the_version_is_named() -> None:
    with pytest.raises(PrinterProtocolError, match="bad image download"):
        parse_version(b"\x07VER DA - 01.01\r")


# --- <STX>KQ -----------------------------------------------------------------------------------

FULL_REPORT = (
    b"INTERNAL MEMORY\r"
    b"VER: 2.03\r"
    b"INSTALLED: 0512\r"
    b"AVAILABLE: 0256\r"
    b"MODULE: D\r"
    b"SCALABLE: NONE\r"
    b"LABEL MEM: 0128\r"
    b"LABEL SIZE: 0410:0600:IN\r"
)


@pytest.mark.os_agnostic
def test_a_full_memory_report_is_read() -> None:
    report = parse_memory_report(FULL_REPORT)
    assert report == MemoryReport(
        version="2.03",
        installed_blocks=512,
        available_blocks=256,
        label_width=410,
        label_length=600,
        units="IN",
        raw=FULL_REPORT.decode("ascii"),
    )


@pytest.mark.os_agnostic
def test_missing_memory_lines_read_as_none() -> None:
    report = parse_memory_report(b"INTERNAL MEMORY\r\n\x11LABEL SIZE: 1000:2000:MM\r\n")
    assert (report.version, report.installed_blocks, report.available_blocks) == (None, None, None)
    assert (report.label_width, report.label_length, report.units) == (1000, 2000, "MM")
    assert "\x11" not in report.raw


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (b"LABEL SIZE: 0410:0600:XX\r", (410, 600, None)),
        (b"LABEL SIZE: 0410:0600\r", (None, None, None)),
        (b"LABEL SIZE: wide:0600:IN\r", (None, 600, "IN")),
        (b"LABEL SIZE: 0410:0600:IN:EXTRA\r", (None, None, None)),
    ],
)
def test_a_malformed_label_size_reads_its_bad_parts_as_none(line: bytes, expected: tuple[object, ...]) -> None:
    report = parse_memory_report(b"INTERNAL MEMORY\r" + line)
    assert (report.label_width, report.label_length, report.units) == expected


@pytest.mark.os_agnostic
def test_a_non_numeric_block_count_reads_as_none_and_stays_in_raw() -> None:
    report = parse_memory_report(b"INSTALLED: 512K\rAVAILABLE: " + bytes([0xE4]) + b"\r")
    assert report.installed_blocks is None
    assert report.available_blocks is None
    assert "512K" in report.raw


@pytest.mark.os_agnostic
def test_the_first_of_two_repeated_memory_lines_wins() -> None:
    assert parse_memory_report(b"VER: 1\rVER: 2\r").version == "1"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("answer", [b"", b"\r", b"garbage\r", b"Y", b"VERSION 2\r", bytes([0xFF, 0xFE]) + b"\r"])
def test_a_memory_report_without_any_known_line_is_refused(answer: bytes) -> None:
    with pytest.raises(PrinterProtocolError):
        parse_memory_report(answer)


@pytest.mark.os_agnostic
@given(answer=st.binary(max_size=200))
def test_every_parser_either_parses_or_refuses_arbitrary_bytes(answer: bytes) -> None:
    for parse in (parse_ascii_status, parse_status_byte, parse_batch_remaining, parse_version, parse_memory_report):
        with contextlib.suppress(PrinterProtocolError):
            parse(answer)
