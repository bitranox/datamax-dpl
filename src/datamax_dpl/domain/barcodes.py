"""Check barcode data against what the printer accepts for each symbology.

The rules come from ``docs/dpl-reference.md`` section 7 (character sets, lengths, check digits)
and section 5 (a record's data ends at ``<CR>`` and holds up to 255 characters, 3000 for PDF417).
:func:`check_barcode` never raises: every finding is a :class:`BarcodeProblem` with a stable
``code`` slug, so a caller can collect them all and decide which ones block a label.

The two-dimensional codes carry a parameter header in front of their data;
:func:`pdf417_data_field` and :func:`datamatrix_data_field` build it.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from pydantic import BaseModel

from .label import MAX_FIELD_DATA, MAX_PDF417_DATA, DataMatrixOptions, Pdf417Options
from .symbology import Symbology

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

DIGITS = frozenset(string.digits)
CODE39_CHARACTERS = frozenset(string.digits + string.ascii_uppercase + " -.$/+%*")
CODE93_CHARACTERS = frozenset(string.digits + string.ascii_uppercase + "-.$/+% ")
CODABAR_BODY_CHARACTERS = frozenset(string.digits + "-$:/.+")
CODABAR_START_STOP = frozenset("ABCD")
CODABAR_MIN_LENGTH = 2
FIM_PATTERNS = frozenset("ABCD")
HIBC_PREFIX = "+"
PRICE_MARKER = "V"
CODE128_ESCAPE = "&"
CODE128_ESCAPE_LETTERS = frozenset("ABCDEFG")
CODE128_FIRST = 32
ASCII_LAST = 127
BYTE_LAST = 255
MAXICODE_MAX_DATA = 84
PLESSEY_MAX_DIGITS = 14
I2OF5_BEARER_DIGITS = 13
ADDON2_DIGITS = 2
ADDON5_DIGITS = 5
POSTNET_DIGITS = frozenset({5, 9, 11})
UCC_EAN128_DIGITS = 19
KMART_DIGITS = 18
UPC_A_BODY_DIGITS = 11
UPC_E_BODY_DIGITS = 6
EAN13_BODY_DIGITS = 12
EAN8_BODY_DIGITS = 7
#: 1-based positions where a price marker V may stand.
UPC_A_PRICE_POSITIONS = (6, 7)
EAN13_PRICE_POSITIONS = (7, 8)
RANDOM_WEIGHT_MIN_DIGITS = 34
#: The printer reads a record up to this character, so data containing it is cut short.
RECORD_TERMINATOR = "\r"
GS1_MODULUS = 10
GS1_ODD_WEIGHT = 3
#: UPC-E as a 6-digit body implies number system 0 when it is expanded to UPC-A.
UPC_E_NUMBER_SYSTEM = "0"
UPC_E_SHIFT_THREE = "3"
UPC_E_SHIFT_FOUR = "4"
UPC_E_SHORT_MANUFACTURER = frozenset("012")
PRINTABLE_FIRST = 32
PRINTABLE_LAST = 126
MAX_LISTED = 10


class BarcodeProblem(BaseModel, frozen=True, extra="forbid"):
    """One reason the printer would refuse or misprint the data.

    Attributes:
        code: Stable slug (``empty``, ``bad_character``, ``bad_length``, ``bad_check_digit``,
            ``bad_price_marker``, ``bad_escape``, ``bad_start_stop``, ``missing_prefix``,
            ``too_long``, ``record_terminator``).
        message: Human-readable explanation, ASCII only.
    """

    code: str
    message: str


class BarcodeCheck(BaseModel, frozen=True, extra="forbid"):
    """The outcome of :func:`check_barcode`.

    Attributes:
        data: The data exactly as given.
        problems: Every finding, empty when the printer accepts the data.
        computed_check_digit: The UPC/EAN check digit the data implies, or None when the
            symbology has none of its own to compute here or the data is too broken to compute it.

    Example:
        >>> check_barcode(Symbology.EAN13, "012345678901").computed_check_digit
        '2'
    """

    data: str
    problems: tuple[BarcodeProblem, ...]
    computed_check_digit: str | None

    @property
    def ok(self) -> bool:
        """Return True when there are no problems."""
        return not self.problems


@dataclass(frozen=True)
class _Outcome:
    problems: tuple[BarcodeProblem, ...] = ()
    check_digit: str | None = None


@dataclass(frozen=True)
class _Lengths:
    """Accepted data lengths: an explicit set, or a range with an optional upper bound."""

    allowed: frozenset[int] = frozenset()
    minimum: int = 1
    maximum: int | None = None

    def accepts(self, length: int) -> bool:
        if self.allowed:
            return length in self.allowed
        return length >= self.minimum and (self.maximum is None or length <= self.maximum)

    def describe(self) -> str:
        if self.allowed:
            return " or ".join(str(n) for n in sorted(self.allowed))
        if self.maximum is None:
            return f"{self.minimum} or more"
        return f"{self.minimum} to {self.maximum}"


_ANY_LENGTH = _Lengths()


def _identity(digits: str) -> str:
    return digits


def _expand_upc_e(body: str) -> str:
    """Expand a 6-digit UPC-E body to the 11 UPC-A digits its check digit is computed from.

    Example:
        >>> _expand_upc_e("425261")
        '04210000526'
    """
    last = body[-1]
    if last in UPC_E_SHORT_MANUFACTURER:
        core = body[0:2] + last + "0000" + body[2:5]
    elif last == UPC_E_SHIFT_THREE:
        core = body[0:3] + "00000" + body[3:5]
    elif last == UPC_E_SHIFT_FOUR:
        core = body[0:4] + "00000" + body[4]
    else:
        core = body[0:5] + "0000" + last
    return UPC_E_NUMBER_SYSTEM + core


@dataclass(frozen=True)
class _Gs1:
    """A UPC/EAN symbology: body length, 1-based price-marker positions, check-digit source."""

    name: str
    body_length: int
    price_positions: tuple[int, ...] = ()
    check_source: Callable[[str], str] = _identity


_UPC_A = _Gs1(name="UPC-A", body_length=UPC_A_BODY_DIGITS, price_positions=UPC_A_PRICE_POSITIONS)
_UPC_E = _Gs1(name="UPC-E", body_length=UPC_E_BODY_DIGITS, check_source=_expand_upc_e)
_EAN13 = _Gs1(name="EAN-13", body_length=EAN13_BODY_DIGITS, price_positions=EAN13_PRICE_POSITIONS)
_EAN8 = _Gs1(name="EAN-8", body_length=EAN8_BODY_DIGITS)

_EMPTY = BarcodeProblem(code="empty", message="barcode data is empty")


def gs1_mod10_check_digit(digits: str) -> str:
    """Return the UPC/EAN (GS1 modulo 10) check digit for ``digits``.

    Weights 3 and 1 alternate from the rightmost digit, which gets 3.

    Args:
        digits: One or more ASCII digits, without the check digit.

    Returns:
        The check digit as a one-character string.

    Raises:
        ValueError: ``digits`` is empty or holds anything but ASCII 0-9.

    Example:
        >>> gs1_mod10_check_digit("03600029145")
        '2'
    """
    if not digits or not DIGITS.issuperset(digits):
        msg = "digits must be one or more ASCII digits 0-9"
        raise ValueError(msg)
    weighted = (int(ch) * (GS1_ODD_WEIGHT if i % 2 == 0 else 1) for i, ch in enumerate(reversed(digits)))
    return str(-sum(weighted) % GS1_MODULUS)


def pdf417_data_field(data: str, options: Pdf417Options) -> str:
    """Return the PDF417 record data: ``T``/``F``, security, aspect, rows, columns, then ``data``.

    Args:
        data: The text to encode.
        options: Truncation, security level and layout.

    Returns:
        The data field of a ``z`` record.

    Example:
        >>> pdf417_data_field("HELLO", Pdf417Options(security_level=3, rows=10))
        'F3001000HELLO'
    """
    flag = "T" if options.truncated else "F"
    return f"{flag}{options.security_level}{options.aspect_ratio:02d}{options.rows:02d}{options.columns:02d}{data}"


def datamatrix_data_field(data: str, options: DataMatrixOptions) -> str:
    """Return the DataMatrix record data: ECC (3 digits), format id, rows, columns, then ``data``.

    Args:
        data: The text to encode.
        options: ECC level, format id and symbol size (0 = automatic).

    Returns:
        The data field of a ``W1c`` record.

    Example:
        >>> datamatrix_data_field("HI", DataMatrixOptions())
        '2000000000HI'
    """
    return f"{options.ecc:03d}{options.format_id}{options.rows:03d}{options.columns:03d}{data}"


def check_barcode(
    symbology: Symbology,
    data: str,
    *,
    pdf417: Pdf417Options | None = None,
    datamatrix: DataMatrixOptions | None = None,
) -> BarcodeCheck:
    """Check ``data`` for ``symbology``; never raises.

    For UPC-A, UPC-E, EAN-13 and EAN-8 the check digit may be included or left to the printer;
    an included one must be right. ``data`` is returned unchanged either way.

    Args:
        symbology: The barcode type.
        data: The record data as the caller wants it printed.
        pdf417: PDF417 parameters, counted into the 3000-character limit (defaults when None).
        datamatrix: DataMatrix parameters, counted into the 255-character limit (defaults when None).

    Returns:
        The data, every problem found, and the computed UPC/EAN check digit when there is one.

    Example:
        >>> check_barcode(Symbology.UPC_A, "1234567890").problems[0].code
        'bad_length'
        >>> check_barcode(Symbology.CODE128, "AB&G12").ok
        True
    """
    if not data:
        return BarcodeCheck(data=data, problems=(_EMPTY,), computed_check_digit=None)
    outcome = _CHECKERS[symbology](data)
    framing = _record_problems(symbology, data, pdf417=pdf417, datamatrix=datamatrix)
    return BarcodeCheck(data=data, problems=framing + outcome.problems, computed_check_digit=outcome.check_digit)


def _record_problems(
    symbology: Symbology,
    data: str,
    *,
    pdf417: Pdf417Options | None,
    datamatrix: DataMatrixOptions | None,
) -> tuple[BarcodeProblem, ...]:
    problems: list[BarcodeProblem] = []
    if RECORD_TERMINATOR in data:
        problems.append(
            BarcodeProblem(code="record_terminator", message="data contains <CR>, which ends the DPL record early")
        )
    limit, length = _field_budget(symbology, data, pdf417=pdf417, datamatrix=datamatrix)
    if length > limit:
        message = f"record data is {length} characters, the printer takes at most {limit}"
        problems.append(BarcodeProblem(code="too_long", message=message))
    return tuple(problems)


def _field_budget(
    symbology: Symbology,
    data: str,
    *,
    pdf417: Pdf417Options | None,
    datamatrix: DataMatrixOptions | None,
) -> tuple[int, int]:
    """Return (limit, length) of the whole record data field, parameter header included."""
    if symbology is Symbology.PDF417:
        header = pdf417_data_field("", pdf417 or Pdf417Options())
        return MAX_PDF417_DATA, len(header) + len(data)
    if symbology is Symbology.DATAMATRIX:
        header = datamatrix_data_field("", datamatrix or DataMatrixOptions())
        return MAX_FIELD_DATA, len(header) + len(data)
    return MAX_FIELD_DATA, len(data)


def _render_character(ch: str) -> str:
    if PRINTABLE_FIRST <= ord(ch) <= PRINTABLE_LAST:
        return f"'{ch}'"
    return f"U+{ord(ch):04X}"


def _render_list(items: Sequence[str]) -> str:
    """Join at most MAX_LISTED items so a huge input cannot produce a huge message."""
    shown = ", ".join(items[:MAX_LISTED])
    rest = len(items) - MAX_LISTED
    return shown if rest <= 0 else f"{shown} and {rest} more"


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _character_problems(data: str, allowed: Callable[[str], bool], *, description: str) -> tuple[BarcodeProblem, ...]:
    bad = _unique(ch for ch in data if not allowed(ch))
    if not bad:
        return ()
    rendered = _render_list([_render_character(ch) for ch in bad])
    return (BarcodeProblem(code="bad_character", message=f"{rendered} not allowed; {description}"),)


def _length_problems(length: int, lengths: _Lengths, *, unit: str) -> tuple[BarcodeProblem, ...]:
    if lengths.accepts(length):
        return ()
    message = f"{length} {unit} given, {lengths.describe()} expected"
    return (BarcodeProblem(code="bad_length", message=message),)


def _check_charset(
    data: str,
    *,
    allowed: Callable[[str], bool],
    description: str,
    lengths: _Lengths = _ANY_LENGTH,
    unit: str = "characters",
) -> _Outcome:
    problems = _character_problems(data, allowed, description=description)
    return _Outcome(problems=problems + _length_problems(len(data), lengths, unit=unit))


def _digits(lengths: _Lengths) -> Callable[[str], _Outcome]:
    return partial(
        _check_charset, allowed=DIGITS.__contains__, description="digits 0-9 only", lengths=lengths, unit="digits"
    )


def _is_ascii(ch: str) -> bool:
    return ord(ch) <= ASCII_LAST


def _is_byte(ch: str) -> bool:
    return ord(ch) <= BYTE_LAST


def _is_code128_character(ch: str) -> bool:
    return CODE128_FIRST <= ord(ch) <= ASCII_LAST


def _price_marker_problems(data: str, spec: _Gs1) -> tuple[BarcodeProblem, ...]:
    positions = [i + 1 for i, ch in enumerate(data) if ch == PRICE_MARKER]
    if not positions:
        return ()
    misplaced = any(p not in spec.price_positions for p in positions)
    if not misplaced and len(positions) == 1:
        return ()
    allowed = " or ".join(str(p) for p in spec.price_positions)
    message = f"{spec.name} takes one price marker V, at position {allowed}"
    return (BarcodeProblem(code="bad_price_marker", message=message),)


def _gs1_character_problems(data: str, spec: _Gs1) -> tuple[BarcodeProblem, ...]:
    if not spec.price_positions:
        return _character_problems(data, DIGITS.__contains__, description="digits 0-9 only")
    allowed = DIGITS | {PRICE_MARKER}
    problems = _character_problems(data, allowed.__contains__, description="digits 0-9 and the price marker V only")
    return problems + _price_marker_problems(data, spec)


def _check_gs1(data: str, *, spec: _Gs1) -> _Outcome:
    lengths = _Lengths(allowed=frozenset({spec.body_length, spec.body_length + 1}))
    problems = _gs1_character_problems(data, spec) + _length_problems(len(data), lengths, unit="digits")
    # A price marker makes the printer compute a digit inside the body first, so the overall
    # check digit cannot be known here; the printer works it out.
    if problems or PRICE_MARKER in data:
        return _Outcome(problems=problems)
    expected = gs1_mod10_check_digit(spec.check_source(data[: spec.body_length]))
    if len(data) > spec.body_length and data[-1] != expected:
        message = f"{spec.name} check digit {data[-1]} is wrong, expected {expected}"
        problems = (BarcodeProblem(code="bad_check_digit", message=message),)
    return _Outcome(problems=problems, check_digit=expected)


def _lone_escape_positions(data: str) -> list[str]:
    positions: list[str] = []
    index = data.find(CODE128_ESCAPE)
    while index != -1:
        if data[index + 1 : index + 2] in CODE128_ESCAPE_LETTERS:
            index = data.find(CODE128_ESCAPE, index + 2)
        else:
            positions.append(str(index + 1))
            index = data.find(CODE128_ESCAPE, index + 1)
    return positions


def _check_code128(data: str) -> _Outcome:
    problems = _character_problems(data, _is_code128_character, description="Code 128 takes ASCII 32-127")
    lone = _lone_escape_positions(data)
    if lone:
        message = f"'&' at position {_render_list(lone)} is not followed by A-G (the escapes &A-&G)"
        problems += (BarcodeProblem(code="bad_escape", message=message),)
    return _Outcome(problems=problems)


def _check_codabar(data: str) -> _Outcome:
    problems: list[BarcodeProblem] = []
    if len(data) < CODABAR_MIN_LENGTH:
        problems.append(BarcodeProblem(code="bad_length", message="Codabar needs a start and a stop character"))
    if data[0] not in CODABAR_START_STOP or data[-1] not in CODABAR_START_STOP:
        message = "Codabar data must start and end with one of A B C D"
        problems.append(BarcodeProblem(code="bad_start_stop", message=message))
    body = data[1:-1]
    problems.extend(_character_problems(body, CODABAR_BODY_CHARACTERS.__contains__, description="0-9 - $ : / . +"))
    return _Outcome(problems=tuple(problems))


def _check_hibc(data: str) -> _Outcome:
    problems = _character_problems(data, CODE39_CHARACTERS.__contains__, description="the Code 39 set")
    if not data.startswith(HIBC_PREFIX):
        problems = (BarcodeProblem(code="missing_prefix", message="HIBC data must start with '+'"), *problems)
    return _Outcome(problems=problems)


_CHECKERS: dict[Symbology, Callable[[str], _Outcome]] = {
    Symbology.CODE39: partial(
        _check_charset, allowed=CODE39_CHARACTERS.__contains__, description="Code 39: 0-9 A-Z space - . $ / + % *"
    ),
    Symbology.UPC_A: partial(_check_gs1, spec=_UPC_A),
    Symbology.UPC_E: partial(_check_gs1, spec=_UPC_E),
    Symbology.I2OF5: _digits(_ANY_LENGTH),
    Symbology.CODE128: _check_code128,
    Symbology.EAN13: partial(_check_gs1, spec=_EAN13),
    Symbology.EAN8: partial(_check_gs1, spec=_EAN8),
    Symbology.HIBC: _check_hibc,
    Symbology.CODABAR: _check_codabar,
    Symbology.I2OF5_MOD10: _digits(_ANY_LENGTH),
    Symbology.PLESSEY: _digits(_Lengths(maximum=PLESSEY_MAX_DIGITS)),
    Symbology.I2OF5_BEARER: _digits(_Lengths(allowed=frozenset({I2OF5_BEARER_DIGITS}))),
    Symbology.ADDON2: _digits(_Lengths(allowed=frozenset({ADDON2_DIGITS}))),
    Symbology.ADDON5: _digits(_Lengths(allowed=frozenset({ADDON5_DIGITS}))),
    Symbology.CODE93: partial(
        _check_charset, allowed=CODE93_CHARACTERS.__contains__, description="Code 93: 0-9 A-Z - . $ / + % space"
    ),
    Symbology.POSTNET: _digits(_Lengths(allowed=POSTNET_DIGITS)),
    Symbology.UCC_EAN128: _digits(_Lengths(allowed=frozenset({UCC_EAN128_DIGITS}))),
    Symbology.UCC_EAN128_KMART: _digits(_Lengths(allowed=frozenset({KMART_DIGITS}))),
    Symbology.UCC_EAN128_RANDOM_WEIGHT: _digits(_Lengths(minimum=RANDOM_WEIGHT_MIN_DIGITS)),
    Symbology.TELEPEN: partial(_check_charset, allowed=_is_ascii, description="Telepen takes ASCII 0-127"),
    Symbology.MAXICODE: partial(
        _check_charset,
        allowed=_is_ascii,
        description="MaxiCode takes ASCII 0-127",
        lengths=_Lengths(maximum=MAXICODE_MAX_DATA),
    ),
    Symbology.FIM: partial(
        _check_charset,
        allowed=FIM_PATTERNS.__contains__,
        description="FIM is one of A B C D",
        lengths=_Lengths(allowed=frozenset({1})),
    ),
    Symbology.PDF417: partial(_check_charset, allowed=_is_byte, description="PDF417 takes 8-bit characters 0-255"),
    Symbology.DATAMATRIX: partial(
        _check_charset, allowed=_is_byte, description="DataMatrix takes 8-bit characters 0-255"
    ),
}

__all__ = [
    "BarcodeCheck",
    "BarcodeProblem",
    "check_barcode",
    "datamatrix_data_field",
    "gs1_mod10_check_digit",
    "pdf417_data_field",
]
