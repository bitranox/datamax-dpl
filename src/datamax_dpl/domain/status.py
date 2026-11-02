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

``R`` and ``T`` in front of a status answer are the exception: they say the printer restarted, not
that this answer is wrong, and one Allegro 2 sent ``R`` in front of its first answer after every
power cycle (``R\\x11NNNNNNNN\\r``, later answers without it). The status is returned with
:attr:`PrinterStatus.reset_reported` set. The other answers keep refusing them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

from pydantic import BaseModel

from .enums import MemoryKind, StatusFlag, StatusProblem, Unit
from .errors import PrinterProtocolError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

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
    {MemoryKind.FONT: b"\x02WF", MemoryKind.GRAPHIC: b"\x02WG", MemoryKind.FORMAT: b"\x02WL"}
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

#: The error characters that report a restart: a status answer behind them is still sound.
RESET_CHARACTERS = frozenset({ord("R"), ord("T")})

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
_DIGITS = re.compile(rb"[0-9]{4}")
_NUMBER = re.compile(r"[0-9]{1,9}")
_LINE_BREAK = re.compile(r"[\r\n]+")
_MEMORY_HEADER = "INTERNAL MEMORY"
_LABEL_SIZE_PARTS = 3
#: The ``oo`` units code of ``LABEL SIZE`` as the printer writes it, and the unit it names.
_LABEL_SIZE_UNITS: Mapping[str, Unit] = MappingProxyType({"IN": Unit.INCH, "MM": Unit.MM})


class FlagState(NamedTuple):
    """One status condition and whether the printer reported it set.

    Example:
        >>> FlagState(StatusFlag.PAUSED, True).flag.value
        'paused'
    """

    flag: StatusFlag
    is_set: bool


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
    #: True when ``R`` (power-on) or ``T`` (software reset) stood in front of the answer. The printer
    #: says so once, so whoever read this answer took the report.
    reset_reported: bool = False

    def flags(self) -> tuple[FlagState, ...]:
        """Each condition and whether it is set, in the order the printer reports them.

        Example:
            >>> [state.flag.value for state in parse_ascii_status(b"NYNNNNNN\\r").flags() if state.is_set]
            ['paper_out']
        """
        return (
            FlagState(StatusFlag.INTERPRETER_BUSY, self.interpreter_busy),
            FlagState(StatusFlag.PAPER_OUT, self.paper_out),
            FlagState(StatusFlag.RIBBON_OUT, self.ribbon_out),
            FlagState(StatusFlag.PRINTING_BATCH, self.printing_batch),
            FlagState(StatusFlag.BUSY_PRINTING, self.busy_printing),
            FlagState(StatusFlag.PAUSED, self.paused),
            FlagState(StatusFlag.LABEL_PRESENTED, self.label_presented),
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
            eighth one ``N``, or an error character other than a reset report precedes it.

    Example:
        >>> parse_ascii_status(b"\\x11NNNNYNNN\\r").busy_printing
        True
        >>> parse_ascii_status(b"R\\x11NNNNNNNN\\r").reset_reported
        True
    """
    what = "<SOH>A status"
    body, reset = _status_body(_drop_flow_control(answer), width=STATUS_FLAG_COUNT, what=what, answer=answer)
    if any(char not in (_YES, _NO) for char in body):
        raise PrinterProtocolError(f"{what}: expected Y/N characters, got {show_answer(answer)}")
    if body[-1] != _NO:
        raise PrinterProtocolError(f"{what}: reserved eighth flag is not N in {show_answer(answer)}")
    return _status_from_flags([char == _YES for char in body[: len(StatusFlag)]], reset_reported=reset)


def is_status_line(line: bytes) -> bool:
    """Whether ``line`` is the line an ``<SOH>A`` answer arrives on: it ends in eight ``Y``/``N`` and ``<CR>``.

    A transport hands each line it reads to this test and skips the ones it refuses, so a line left
    over from an earlier query (an ``<STX>W`` or ``<STX>KQ`` line such as ``MODULE: A``) is never
    read as the status. Flow-control bytes are dropped first. Whatever stands in front of the eight
    flags on the same line stays for :func:`parse_ascii_status` to judge, so an error character or
    junk on the answer's own line is still refused there rather than skipped here.

    Args:
        line: One line as a transport read it, terminator included.

    Returns:
        True when the line ends in eight ``Y``/``N`` characters and ``<CR>``.

    Example:
        >>> is_status_line(b"\\x11NNNNYNNN\\r"), is_status_line(b"cZNNNNNNNN\\r")
        (True, True)
        >>> is_status_line(b"AVAILABLE BYTES IN MODULE : 00048631\\r"), is_status_line(b"NNNNNNN\\r")
        (False, False)
    """
    cleaned = _drop_flow_control(line)
    if not cleaned.endswith(ANSWER_TERMINATOR):
        return False
    flags = cleaned[: -len(ANSWER_TERMINATOR)][-STATUS_FLAG_COUNT:]
    return len(flags) == STATUS_FLAG_COUNT and all(char in (_YES, _NO) for char in flags)


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
            character other than a reset report precedes it.

    Example:
        >>> parse_status_byte(bytes([0b0000_0010]) + b"\\r").paper_out
        True
    """
    what = "<SOH>F status"
    body, reset = _status_body(answer, width=1, what=what, answer=answer)
    value = body[0]
    if value >> RESERVED_STATUS_BIT:
        raise PrinterProtocolError(f"{what}: reserved bit 7 is set in {show_answer(answer)}")
    return _status_from_flags([bool(value >> bit & 1) for bit in range(len(StatusFlag))], reset_reported=reset)


def _status_from_flags(flags: Sequence[bool], *, reset_reported: bool) -> PrinterStatus:
    """Build the status from one bool per :class:`StatusFlag`, in that order."""
    interpreter_busy, paper_out, ribbon_out, printing_batch, busy_printing, paused, label_presented = flags
    return PrinterStatus(
        interpreter_busy=interpreter_busy,
        paper_out=paper_out,
        ribbon_out=ribbon_out,
        printing_batch=printing_batch,
        busy_printing=busy_printing,
        paused=paused,
        label_presented=label_presented,
        reset_reported=reset_reported,
    )


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
        raise PrinterProtocolError(f"{what}: expected {BATCH_DIGITS} digits, got {show_answer(answer)}")
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
        raise PrinterProtocolError(f"{what}: answer does not end with <CR>: {show_answer(answer)}")
    body = cleaned[: -len(ANSWER_TERMINATOR)].strip(b" ")
    if body[:1] == bytes([BEL]):
        raise PrinterProtocolError(f"{what}: printer reported {ERROR_CHARACTERS[BEL]} before {show_answer(answer)}")
    if not body or any(not _FIRST_PRINTABLE <= char <= _LAST_PRINTABLE for char in body):
        raise PrinterProtocolError(f"{what}: expected printable ASCII text, got {show_answer(answer)}")
    return body.decode("ascii")


def parse_serial_test(answer: bytes) -> bool:
    """Read the ``<STX>k`` answer: whether the printer passed the serial port test.

    The manual's answer is a bare ``Y`` with no terminator, so the test passes when the answer,
    without surrounding whitespace, starts with ``Y``. Anything else is False rather than an
    error: detection only collects evidence, and a garbled answer still says the port is not one
    that answers ``Y``.

    Args:
        answer: The bytes the printer sent.

    Returns:
        True when the answer starts with ``Y``.

    Example:
        >>> parse_serial_test(b"Y"), parse_serial_test(b" Y\\r\\n"), parse_serial_test(b"N\\r")
        (True, True, False)
    """
    return answer.strip().startswith(bytes([_YES]))


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
    lines = _memory_lines(raw)
    if not lines.recognised:
        raise PrinterProtocolError(f"<STX>KQ memory report: no line the manual describes in {show_answer(answer)}")
    size = _label_size(lines.label_size)
    return MemoryReport(
        version=lines.version or None,
        installed_blocks=_number(lines.installed),
        available_blocks=_number(lines.available),
        label_width=size.width,
        label_length=size.length,
        units=size.units,
        raw=raw,
    )


def _drop_flow_control(answer: bytes) -> bytes:
    """Return ``answer`` without XON/XOFF bytes, which never belong to a text answer."""
    return bytes(char for char in answer if char not in _FLOW_CONTROL)


def _fixed_body(cleaned: bytes, *, width: int, what: str, answer: bytes) -> bytes:
    """Return the ``width`` bytes before the final ``<CR>`` of ``cleaned``, refusing anything in front."""
    if not cleaned.endswith(ANSWER_TERMINATOR):
        raise PrinterProtocolError(f"{what}: answer does not end with <CR>: {show_answer(answer)}")
    framed = cleaned[: -len(ANSWER_TERMINATOR)]
    if len(framed) < width:
        raise PrinterProtocolError(f"{what}: expected {width} byte(s) before <CR>, got {show_answer(answer)}")
    _refuse_prefix(framed[: len(framed) - width], what=what, answer=answer)
    return framed[len(framed) - width :]


def _status_body(cleaned: bytes, *, width: int, what: str, answer: bytes) -> tuple[bytes, bool]:
    """Return a status answer's body as :func:`_fixed_body` does, and whether a reset report stood in front.

    Only a prefix of nothing but reset characters and flow control counts: with any other byte
    among them the printer also reported something else, which :func:`_refuse_prefix` names whole.
    """
    prefix = cleaned[: max(0, len(cleaned) - len(ANSWER_TERMINATOR) - width)]
    meaningful = [char for char in prefix if char not in _FLOW_CONTROL]
    reset = bool(meaningful) and all(char in RESET_CHARACTERS for char in meaningful)
    rest = cleaned[len(prefix) :] if reset else cleaned
    return _fixed_body(rest, width=width, what=what, answer=answer), reset


def _refuse_prefix(prefix: bytes, *, what: str, answer: bytes) -> None:
    """Raise for any byte in front of a fixed-size answer other than flow control."""
    meaningful = [char for char in prefix if char not in _FLOW_CONTROL]
    if not meaningful:
        return
    reported = [ERROR_CHARACTERS[char] for char in meaningful if char in ERROR_CHARACTERS]
    detail = f"printer reported {', '.join(reported)}" if reported else "unexpected bytes"
    if len(reported) != len(meaningful) and reported:
        detail += " and unexpected bytes"
    raise PrinterProtocolError(f"{what}: {detail} before the answer in {show_answer(answer)}")


class _MemoryKey(str, Enum):
    """The keys of the ``KEY: value`` lines an ``<STX>KQ`` answer may hold (manual section 8)."""

    VERSION = "VER"
    INSTALLED = "INSTALLED"
    AVAILABLE = "AVAILABLE"
    MODULE = "MODULE"
    SCALABLE = "SCALABLE"
    LABEL_MEMORY = "LABEL MEM"
    LABEL_SIZE = "LABEL SIZE"


_MEMORY_KEYS: Mapping[str, _MemoryKey] = MappingProxyType({key.value: key for key in _MemoryKey})


@dataclass(frozen=True, slots=True)
class _MemoryLines:
    """The values of the ``<STX>KQ`` lines a memory report is built from; None where a line is missing.

    Attributes:
        recognised: Whether any line, the ``INTERNAL MEMORY`` header included, is one the manual
            describes. ``MODULE``, ``SCALABLE`` and ``LABEL MEM`` count here although no value
            is read from them.
    """

    recognised: bool
    version: str | None
    installed: str | None
    available: str | None
    label_size: str | None


def _memory_lines(raw: str) -> _MemoryLines:
    """Read each recognised ``<STX>KQ`` line's value; the first occurrence of a key wins."""
    values: dict[_MemoryKey, str] = {}
    header = False
    for line in _LINE_BREAK.split(raw):
        text, separator, value = line.partition(":")
        text = text.strip()
        key = _MEMORY_KEYS.get(text)
        if text == _MEMORY_HEADER and not separator:
            header = True
        elif separator and key is not None:
            values.setdefault(key, value.strip())
    return _MemoryLines(
        recognised=header or bool(values),
        version=values.get(_MemoryKey.VERSION),
        installed=values.get(_MemoryKey.INSTALLED),
        available=values.get(_MemoryKey.AVAILABLE),
        label_size=values.get(_MemoryKey.LABEL_SIZE),
    )


@dataclass(frozen=True)
class _LabelSize:
    """The ``LABEL SIZE`` line of a memory report; each part None where it is malformed or missing."""

    width: int | None = None
    length: int | None = None
    units: Unit | None = None


def _label_size(value: str | None) -> _LabelSize:
    """Split ``wwww:gggg:oo`` into width, length and units; a malformed or missing part reads as None."""
    if value is None:
        return _LabelSize()
    parts = [part.strip() for part in value.split(":")]
    if len(parts) != _LABEL_SIZE_PARTS:
        return _LabelSize()
    return _LabelSize(width=_number(parts[0]), length=_number(parts[1]), units=_LABEL_SIZE_UNITS.get(parts[2]))


def _number(text: str | None) -> int | None:
    """Return ``text`` as an int when it is plain ASCII digits, else None."""
    return int(text) if text is not None and _NUMBER.fullmatch(text) else None


def show_answer(answer: bytes) -> str:
    """Quote ``answer`` for an error message, cut short so a flood does not flood the message too.

    Example:
        >>> show_answer(b"NNNNNNN\\r")
        "b'NNNNNNN\\\\r'"
        >>> show_answer(b"x" * 100)
        "b'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'... (100 bytes)"
    """
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
    "RESET_CHARACTERS",
    "SERIAL_TEST_REQUEST",
    "SHOWN_ANSWER_BYTES",
    "STATUS_BYTE_REQUEST",
    "STATUS_FLAG_COUNT",
    "STATUS_REQUEST",
    "TOGGLE_PAUSE",
    "VERSION_REQUEST",
    "XOFF",
    "XON",
    "FlagState",
    "MemoryReport",
    "PrinterStatus",
    "is_status_line",
    "parse_ascii_status",
    "parse_batch_remaining",
    "parse_memory_report",
    "parse_serial_test",
    "parse_status_byte",
    "parse_version",
    "show_answer",
]
