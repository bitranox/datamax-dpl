"""Printer status requests and the parsing of their answers.

The request constants are the DPL byte strings the printer understands; the ``parse_*`` functions
turn the printer's answer (as a transport returns it, terminator included) into typed values. Every
answer the DPL reference (``docs/dpl-reference.md`` section 8) does not describe raises
:class:`~datamax_dpl.domain.errors.PrinterProtocolError`, so a caller never acts on a guess.

A printer may put flow-control bytes (XON 0x11, XOFF 0x13) or one of its error characters
(``c`` framing error, ``v`` input buffer overflow, ``I`` invalid command sequence, ``R`` power-on
reset, ``T`` software reset, BEL bad image download) in front of an answer. Flow-control bytes are
dropped; an error character is reported in the ``PrinterProtocolError`` message, because it means
the printer lost or refused something and the answer behind it says nothing about that.
"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import BaseModel

from .enums import StatusProblem, Unit
from .errors import PrinterProtocolError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .memory import MemoryKind

#: ``<SOH>A``: status as eight ``Y``/``N`` characters.
STATUS_REQUEST = b"\x01A"
#: ``<SOH>F``: status as one bit field byte.
STATUS_BYTE_REQUEST = b"\x01F"
#: ``<SOH>E``: labels left in the current batch.
BATCH_REQUEST = b"\x01E"
#: ``<STX>v``: firmware version string.
VERSION_REQUEST = b"\x02v"
#: ``<STX>KQ``: memory and label size report.
MEMORY_REQUEST = b"\x02KQ"
#: ``<STX>k``: serial port test, answered with ``Y``.
SERIAL_TEST_REQUEST = b"\x02k"
#: ``<STX>Wa``: the memory module directory for one kind of data, ``a`` = ``F`` fonts, ``G``
#: graphics, ``L`` label formats. The answer has no terminator; see :mod:`.memory`.
MEMORY_DIRECTORY_REQUESTS: Mapping[MemoryKind, bytes] = MappingProxyType(
    {"font": b"\x02WF", "graphic": b"\x02WG", "format": b"\x02WL"}
)

#: ``<SOH>#``: reset the printer.
RESET = b"\x01#"
#: ``<SOH>C``: cancel the job in progress.
CANCEL = b"\x01C"
#: ``<SOH>B``: toggle pause.
TOGGLE_PAUSE = b"\x01B"
#: ``<STX>F``: feed one label.
FORM_FEED = b"\x02F"
#: ``<STX>o``: cut now.
CUT_NOW = b"\x02o"

#: Terminator the printer ends every status answer with.
ANSWER_TERMINATOR = b"\r"

XON = 0x11
XOFF = 0x13
BEL = 0x07

#: Error characters a printer may send unprompted, by byte value, with their meaning.
ERROR_CHARACTERS: Mapping[int, str] = MappingProxyType(
    {
        ord("c"): "framing error",
        ord("v"): "input buffer overflow",
        ord("I"): "invalid command sequence",
        ord("R"): "power-on reset",
        ord("T"): "software reset",
        BEL: "bad image download",
    }
)

#: Characters in an ``<SOH>A`` answer; the eighth is reserved and always ``N``.
STATUS_FLAG_COUNT = 8
#: Bit of the ``<SOH>F`` byte the manual reserves (always 0).
RESERVED_STATUS_BIT = 7
#: Digits in an ``<SOH>E`` answer.
BATCH_DIGITS = 4
#: Bytes of an unparseable answer quoted in an error message; the rest is elided.
SHOWN_ANSWER_BYTES = 48

_FLOW_CONTROL = frozenset({XON, XOFF})
_YES = ord("Y")
_NO = ord("N")
_FIRST_PRINTABLE = 0x20
_LAST_PRINTABLE = 0x7E
_STATUS_FIELDS = (
    "interpreter_busy",
    "paper_out",
    "ribbon_out",
    "printing_batch",
    "busy_printing",
    "paused",
    "label_presented",
)
_DIGITS = re.compile(rb"[0-9]{4}")
_NUMBER = re.compile(r"[0-9]{1,9}")
_LINE_BREAK = re.compile(r"[\r\n]+")
_MEMORY_HEADER = "INTERNAL MEMORY"
_MEMORY_KEYS = frozenset({"VER", "INSTALLED", "AVAILABLE", "MODULE", "SCALABLE", "LABEL MEM", "LABEL SIZE"})
_LABEL_SIZE_PARTS = 3
#: The ``oo`` units code of ``LABEL SIZE`` as the printer writes it, and the unit it names.
_LABEL_SIZE_UNITS: Mapping[str, Unit] = MappingProxyType({"IN": Unit.INCH, "MM": Unit.MM})


class PrinterStatus(BaseModel, frozen=True, extra="forbid"):
    """The printer's state as ``<SOH>A`` or ``<SOH>F`` reports it.

    Example:
        >>> status = parse_ascii_status(b"NYNNNNNN\\r")
        >>> status.paper_out, status.idle, [problem.value for problem in status.problems]
        (True, True, ['paper_out'])
    """

    interpreter_busy: bool
    paper_out: bool
    ribbon_out: bool
    printing_batch: bool
    busy_printing: bool
    paused: bool
    label_presented: bool

    def flags(self) -> tuple[tuple[str, bool], ...]:
        """Each condition's name and whether it is set, in the order the printer reports them."""
        return (
            ("interpreter_busy", self.interpreter_busy),
            ("paper_out", self.paper_out),
            ("ribbon_out", self.ribbon_out),
            ("printing_batch", self.printing_batch),
            ("busy_printing", self.busy_printing),
            ("paused", self.paused),
            ("label_presented", self.label_presented),
        )

    @property
    def idle(self) -> bool:
        """True when the printer is neither interpreting nor printing."""
        return not (self.busy_printing or self.printing_batch or self.interpreter_busy)

    @property
    def problems(self) -> tuple[StatusProblem, ...]:
        """The set conditions that need a person, in the order paper out, ribbon out, paused."""
        flagged = (
            (StatusProblem.PAPER_OUT, self.paper_out),
            (StatusProblem.RIBBON_OUT, self.ribbon_out),
            (StatusProblem.PAUSED, self.paused),
        )
        return tuple(problem for problem, is_set in flagged if is_set)


class MemoryReport(BaseModel, frozen=True, extra="forbid"):
    """What ``<STX>KQ`` reported; a line the printer left out reads as None.

    Attributes:
        version: Text after ``VER:``.
        installed_blocks: Number after ``INSTALLED:``.
        available_blocks: Number after ``AVAILABLE:``.
        label_width: ``wwww`` of ``LABEL SIZE: wwww:gggg:oo``.
        label_length: ``gggg`` of the same line.
        units: The unit ``oo`` of the same line names (``IN`` inches, ``MM`` millimetres), else None.
        raw: The whole answer as text, flow-control bytes removed.
    """

    version: str | None
    installed_blocks: int | None
    available_blocks: int | None
    label_width: int | None
    label_length: int | None
    units: Unit | None
    raw: str


def parse_ascii_status(answer: bytes) -> PrinterStatus:
    """Parse the ``<SOH>A`` answer: eight ``Y``/``N`` characters and ``<CR>``.

    Args:
        answer: The bytes the printer sent, terminator included.

    Returns:
        The decoded status.

    Raises:
        PrinterProtocolError: The answer is not eight ``Y``/``N`` characters with the reserved
            eighth one ``N``, or an error character precedes it.

    Example:
        >>> parse_ascii_status(b"\\x11NNNNYNNN\\r").busy_printing
        True
    """
    what = "<SOH>A status"
    body = _fixed_body(_drop_flow_control(answer), width=STATUS_FLAG_COUNT, what=what, answer=answer)
    if any(char not in (_YES, _NO) for char in body):
        raise PrinterProtocolError(f"{what}: expected Y/N characters, got {_show(answer)}")
    if body[-1] != _NO:
        raise PrinterProtocolError(f"{what}: reserved eighth flag is not N in {_show(answer)}")
    return PrinterStatus.model_validate(
        dict(zip(_STATUS_FIELDS, (char == _YES for char in body[: len(_STATUS_FIELDS)]), strict=True))
    )


def parse_status_byte(answer: bytes) -> PrinterStatus:
    """Parse the ``<SOH>F`` answer: one bit field byte and ``<CR>``.

    Bit 0 is interpreter busy, bit 1 paper out, and so on in the ``<SOH>A`` order up to bit 6,
    label presented. The status byte itself may be 0x11 or 0x13, so only bytes in front of it are
    treated as flow control.

    Args:
        answer: The bytes the printer sent, terminator included.

    Returns:
        The decoded status.

    Raises:
        PrinterProtocolError: The answer is not one byte and ``<CR>``, bit 7 is set, or an error
            character precedes it.

    Example:
        >>> parse_status_byte(bytes([0b0000_0010]) + b"\\r").paper_out
        True
    """
    what = "<SOH>F status"
    value = _fixed_body(answer, width=1, what=what, answer=answer)[0]
    if value >> RESERVED_STATUS_BIT:
        raise PrinterProtocolError(f"{what}: reserved bit 7 is set in {_show(answer)}")
    return PrinterStatus.model_validate({name: bool(value >> bit & 1) for bit, name in enumerate(_STATUS_FIELDS)})


def parse_batch_remaining(answer: bytes) -> int:
    """Parse the ``<SOH>E`` answer: four digits and ``<CR>``.

    Args:
        answer: The bytes the printer sent, terminator included.

    Returns:
        The number of labels left in the batch.

    Raises:
        PrinterProtocolError: The answer is not four ASCII digits and ``<CR>``.

    Example:
        >>> parse_batch_remaining(b"0012\\r")
        12
    """
    what = "<SOH>E batch count"
    body = _fixed_body(_drop_flow_control(answer), width=BATCH_DIGITS, what=what, answer=answer)
    if _DIGITS.fullmatch(body) is None:
        raise PrinterProtocolError(f"{what}: expected {BATCH_DIGITS} digits, got {_show(answer)}")
    return int(body)


def parse_version(answer: bytes) -> str:
    """Parse the ``<STX>v`` answer: the firmware version text and ``<CR>``.

    The manual fixes no format beyond its example, so a version that happens to start with an
    error letter (``I``, ``R``, ``T``...) is returned as text; only BEL is recognised there.

    Args:
        answer: The bytes the printer sent, terminator included.

    Returns:
        The version text without surrounding whitespace.

    Raises:
        PrinterProtocolError: The answer is empty, not terminated, or holds a byte that is not
            printable ASCII.

    Example:
        >>> parse_version(b"VER DA - 01.01 12/21/93\\r")
        'VER DA - 01.01 12/21/93'
    """
    what = "<STX>v version"
    cleaned = _drop_flow_control(answer)
    if not cleaned.endswith(ANSWER_TERMINATOR):
        raise PrinterProtocolError(f"{what}: answer does not end with <CR>: {_show(answer)}")
    body = cleaned[: -len(ANSWER_TERMINATOR)].strip(b" ")
    if body[:1] == bytes([BEL]):
        raise PrinterProtocolError(f"{what}: printer reported {ERROR_CHARACTERS[BEL]} before {_show(answer)}")
    if not body or any(not _FIRST_PRINTABLE <= char <= _LAST_PRINTABLE for char in body):
        raise PrinterProtocolError(f"{what}: expected printable ASCII text, got {_show(answer)}")
    return body.decode("ascii")


def parse_memory_report(answer: bytes) -> MemoryReport:
    """Parse the ``<STX>KQ`` answer, a block of ``KEY: value`` lines.

    Lines the printer leaves out read as None; values that are not the expected number read as
    None too, and ``raw`` keeps the full text for a reader to judge.

    Args:
        answer: The bytes the printer sent.

    Returns:
        The report.

    Raises:
        PrinterProtocolError: Not one line of the answer is a line the manual describes.

    Example:
        >>> report = parse_memory_report(b"INTERNAL MEMORY\\rVER: 2.03\\rLABEL SIZE: 0400:0600:IN\\r")
        >>> report.version, report.label_width, report.label_length, report.units, report.available_blocks
        ('2.03', 400, 600, <Unit.INCH: 'in'>, None)
    """
    raw = _drop_flow_control(answer).decode("ascii", errors="replace")
    fields = _memory_fields(raw)
    if not fields:
        raise PrinterProtocolError(f"<STX>KQ memory report: no line the manual describes in {_show(answer)}")
    width, length, units = _label_size(fields.get("LABEL SIZE", ""))
    return MemoryReport(
        version=fields.get("VER") or None,
        installed_blocks=_number(fields.get("INSTALLED", "")),
        available_blocks=_number(fields.get("AVAILABLE", "")),
        label_width=width,
        label_length=length,
        units=units,
        raw=raw,
    )


def _drop_flow_control(answer: bytes) -> bytes:
    """Return ``answer`` without XON/XOFF bytes, which never belong to a text answer."""
    return bytes(char for char in answer if char not in _FLOW_CONTROL)


def _fixed_body(cleaned: bytes, *, width: int, what: str, answer: bytes) -> bytes:
    """Return the ``width`` bytes before the final ``<CR>`` of ``cleaned``, refusing anything in front."""
    if not cleaned.endswith(ANSWER_TERMINATOR):
        raise PrinterProtocolError(f"{what}: answer does not end with <CR>: {_show(answer)}")
    framed = cleaned[: -len(ANSWER_TERMINATOR)]
    if len(framed) < width:
        raise PrinterProtocolError(f"{what}: expected {width} byte(s) before <CR>, got {_show(answer)}")
    _refuse_prefix(framed[: len(framed) - width], what=what, answer=answer)
    return framed[len(framed) - width :]


def _refuse_prefix(prefix: bytes, *, what: str, answer: bytes) -> None:
    """Raise for any byte in front of a fixed-size answer other than flow control."""
    meaningful = [char for char in prefix if char not in _FLOW_CONTROL]
    if not meaningful:
        return
    reported = [ERROR_CHARACTERS[char] for char in meaningful if char in ERROR_CHARACTERS]
    detail = f"printer reported {', '.join(reported)}" if reported else "unexpected bytes"
    if len(reported) != len(meaningful) and reported:
        detail += " and unexpected bytes"
    raise PrinterProtocolError(f"{what}: {detail} before the answer in {_show(answer)}")


def _memory_fields(raw: str) -> dict[str, str]:
    """Map each recognised ``<STX>KQ`` line to its value; the first occurrence of a key wins."""
    fields: dict[str, str] = {}
    for line in _LINE_BREAK.split(raw):
        key, separator, value = line.partition(":")
        key = key.strip()
        if key == _MEMORY_HEADER and not separator:
            fields.setdefault(key, "")
        elif separator and key in _MEMORY_KEYS:
            fields.setdefault(key, value.strip())
    return fields


def _label_size(value: str) -> tuple[int | None, int | None, Unit | None]:
    """Split ``wwww:gggg:oo`` into width, length and units; a malformed part reads as None."""
    parts = [part.strip() for part in value.split(":")]
    if len(parts) != _LABEL_SIZE_PARTS:
        return None, None, None
    return _number(parts[0]), _number(parts[1]), _LABEL_SIZE_UNITS.get(parts[2])


def _number(text: str) -> int | None:
    """Return ``text`` as an int when it is plain ASCII digits, else None."""
    return int(text) if _NUMBER.fullmatch(text) else None


def _show(answer: bytes) -> str:
    """Quote ``answer`` for an error message, cut short so a flood does not flood the message too."""
    if len(answer) <= SHOWN_ANSWER_BYTES:
        return repr(answer)
    return f"{answer[:SHOWN_ANSWER_BYTES]!r}... ({len(answer)} bytes)"


__all__ = [
    "ANSWER_TERMINATOR",
    "BATCH_DIGITS",
    "BATCH_REQUEST",
    "BEL",
    "CANCEL",
    "CUT_NOW",
    "ERROR_CHARACTERS",
    "FORM_FEED",
    "MEMORY_DIRECTORY_REQUESTS",
    "MEMORY_REQUEST",
    "RESERVED_STATUS_BIT",
    "RESET",
    "SERIAL_TEST_REQUEST",
    "SHOWN_ANSWER_BYTES",
    "STATUS_BYTE_REQUEST",
    "STATUS_FLAG_COUNT",
    "STATUS_REQUEST",
    "TOGGLE_PAUSE",
    "VERSION_REQUEST",
    "XOFF",
    "XON",
    "MemoryReport",
    "PrinterStatus",
    "parse_ascii_status",
    "parse_batch_remaining",
    "parse_memory_report",
    "parse_status_byte",
    "parse_version",
]
