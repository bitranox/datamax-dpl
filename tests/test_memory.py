"""Memory module directories (``<STX>W``): parsing the answer and reading it through a printer."""

from __future__ import annotations

from pathlib import Path

import pytest

from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
from datamax_dpl.application.use_cases import read_memory_directory
from datamax_dpl.domain.errors import PrinterTimeoutError, UnsupportedOperationError
from datamax_dpl.domain.memory import MemoryKind, MemoryModule, parse_module_directory
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
FIXTURES = Path(__file__).parent / "fixtures"
#: What one Allegro 2 (firmware CA02 03.04) holds in graphic memory, in the order it listed them.
ALLEGRO2_GRAPHICS = ("M7F", "M7R", "M7B", "M9F", "M9R", "M9B", "M10F", "M10R", "M10B", "CK2IMG", "CK3IMG")


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
def test_no_modules_available_is_reported_as_such(answer: bytes) -> None:
    directory = parse_module_directory(answer, kind="font")
    assert directory.parsed
    assert directory.no_modules_available
    assert directory.modules == ()


@pytest.mark.os_agnostic
def test_a_module_listing_is_not_no_modules_available() -> None:
    assert not parse_module_directory(MANUAL_SAMPLE, kind="graphic").no_modules_available


@pytest.mark.os_agnostic
def test_garbage_after_the_last_module_and_flow_control_bytes_are_ignored() -> None:
    answer = b"\x13" + MANUAL_SAMPLE + b"\x11\r\x02junk\r"
    directory = parse_module_directory(answer, kind="graphic")
    assert directory.parsed
    assert directory.modules == MANUAL_MODULES


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("kind", "names"), [("graphic", ALLEGRO2_GRAPHICS), ("font", ()), ("format", ())], ids=["graphic", "font", "format"]
)
def test_a_real_allegro2_answer_parses_with_its_free_bytes(kind: MemoryKind, names: tuple[str, ...]) -> None:
    """The answers one Allegro 2 (firmware CA02 03.04) sent to <STX>WG, <STX>WF and <STX>WL on 2026-10-30.

    Kept byte for byte: a space before the colon of the free-bytes header, CR line ends, and an
    XON (0x11) in front of the font answer. Every kind reports module A's same free bytes.
    """
    raw = (FIXTURES / f"memory_allegro2_{kind}.txt").read_bytes()
    directory = parse_module_directory(raw, kind=kind)
    assert directory.parsed
    assert directory.modules == (MemoryModule(module="A", names=names, available_bytes=48631),)


@pytest.mark.os_agnostic
def test_the_fixtures_keep_the_printers_bytes() -> None:
    assert (FIXTURES / "memory_allegro2_font.txt").read_bytes() == (
        b"\x11MODULE: A\rAVAILABLE BYTES IN MODULE : 00048631\r"
    )


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "header",
    [
        b"AVAILABLE BYTES IN MODULE : ",
        b"AVAILABLE BYTES IN MODULE  :  ",
        b"available bytes in module:",
        b"AVAILABLE  BYTES IN MODULE\t:",
    ],
)
def test_the_free_bytes_header_allows_blanks_around_the_colon_and_any_case(header: bytes) -> None:
    directory = parse_module_directory(b"MODULE: A\rLOGO\r" + header + b"00048631\r", kind="graphic")
    assert directory.parsed
    assert directory.modules == (MemoryModule(module="A", names=("LOGO",), available_bytes=48631),)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "line",
    [
        b"AVAILABLE BYTES IN MODULE = 00048631",
        b"AVAILABLE BYTES IN MODULE: 00048631 KB",
        b"AVAILABLE BYTES: 00048631",
        b"AVAILABLE BYTES IN MODULE: ????",
    ],
)
def test_a_free_bytes_line_that_cannot_be_read_unparses_the_directory_and_is_no_name(line: bytes) -> None:
    directory = parse_module_directory(b"MODULE: A\rLOGO\r" + line + b"\r", kind="graphic")
    assert not directory.parsed
    assert directory.modules == (MemoryModule(module="A", names=("LOGO",), available_bytes=None),)


@pytest.mark.os_agnostic
def test_a_module_without_its_free_bytes_unparses_the_directory() -> None:
    directory = parse_module_directory(MANUAL_SAMPLE + b"MODULE: C\rSHIP\r", kind="format")
    assert not directory.parsed
    assert directory.modules[-1] == MemoryModule(module="C", names=("SHIP",), available_bytes=None)


@pytest.mark.os_agnostic
def test_a_free_bytes_header_with_no_number_after_it_unparses_the_directory() -> None:
    directory = parse_module_directory(b"MODULE: A\rAVAILABLE BYTES IN MODULE:\r", kind="graphic")
    assert not directory.parsed
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


def _unparsed(answer: bytes) -> None:
    directory = parse_module_directory(answer, kind="graphic")
    assert not directory.parsed
    assert directory.raw == answer.decode("ascii", errors="backslashreplace")


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "figure",
    [b"0004\r", b"00048631", b"000486310\r", b"0004863\r", b"0004863 \r"],
    ids=["short", "no-cr", "nine-digits", "seven-digits", "seven-digits-blank"],
)
def test_a_free_bytes_figure_must_be_eight_digits_ending_its_line(figure: bytes) -> None:
    _unparsed(b"MODULE: A\rAVAILABLE BYTES IN MODULE : " + figure)


@pytest.mark.os_agnostic
def test_a_truncated_figure_on_its_own_line_is_unparsed() -> None:
    _unparsed(b"MODULE: A\rAVAILABLE BYTES IN MODULE:\r0004")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("letter", [b"Q", b"F", b"AB", b"1"])
def test_a_module_letter_outside_a_to_e_is_unparsed(letter: bytes) -> None:
    _unparsed(b"MODULE: " + letter + b"\rAVAILABLE BYTES IN MODULE : 00048631\r")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("header", [b"MODULE : A", b"MODULE  :  A", b"module: A"])
def test_the_module_line_allows_blanks_around_the_colon(header: bytes) -> None:
    directory = parse_module_directory(header + b"\rAVAILABLE BYTES IN MODULE : 00048631\r", kind="font")
    assert directory.parsed
    assert directory.modules == (MemoryModule(module="A", names=(), available_bytes=48631),)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "name",
    [b"X" * 17, b"LO\x07GO", b"LOGO\xff", b"LO\tGO", b"LOGO\x7f"],
    ids=["17-chars", "bel", "non-ascii", "tab", "del"],
)
def test_a_name_past_the_manuals_limits_is_unparsed(name: bytes) -> None:
    _unparsed(b"MODULE: B\r" + name + b"\rAVAILABLE BYTES IN MODULE : 00048631\r")


@pytest.mark.os_agnostic
def test_a_name_of_sixteen_printable_characters_is_kept() -> None:
    name = "A-Z 0123456789_~"
    directory = parse_module_directory(
        b"MODULE: B\r" + name.encode("ascii") + b"\rAVAILABLE BYTES IN MODULE : 00048631\r", kind="graphic"
    )
    assert directory.parsed
    assert directory.modules[0].names == (name,)


@pytest.mark.os_agnostic
def test_a_module_listed_twice_is_unparsed() -> None:
    _unparsed(b"MODULE: A\rAVAILABLE BYTES IN MODULE : 00048631\r" * 2)


@pytest.mark.os_agnostic
def test_a_bad_idle_is_refused_before_the_printer_is_opened() -> None:
    printer = RecordingPrinter(answers={b"\x02WG": MANUAL_SAMPLE})
    with pytest.raises(ValueError, match="idle must be above 0"):
        read_memory_directory(
            kinds=("graphic",), uri=PRINTER, timeout=2.0, idle=0.0, open_printer=open_recording_printer(printer)
        )
    assert printer.opened == []
