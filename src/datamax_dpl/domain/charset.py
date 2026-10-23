"""Turn text into the bytes a printer font prints, and name the characters it cannot print.

The bitmapped fonts 0-9 hold fixed subsets of code page 437 (``docs/dpl-reference.md``
section 6); scalable fonts print through the symbol set chosen with ``ySxx``. Every character a
font cannot print is replaced by ``?`` and listed once in :attr:`EncodedText.unmappable`, so a
caller can warn instead of letting the printer print a wrong glyph.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Callable

#: Bitmapped fonts index code page 437; the byte values below are positions in it.
BITMAPPED_CODEC = "cp437"
#: Symbol set used when a scalable-font record names none.
DEFAULT_SYMBOL_SET = "PC"
REPLACEMENT_BYTE = ord("?")
#: Below this code point everything is a control character, and the printer would read STX, CR
#: and friends as protocol framing rather than text.
FIRST_PRINTABLE = 32
MAX_ECHOED_ID = 8


def _span(first: int, last: int) -> frozenset[int]:
    return frozenset(range(first, last + 1))


_CAPITALS_SET = (
    frozenset({32, 128, 142, 143, 144, 146, 153, 154, 156, 157, 165, 168, 225})
    | _span(35, 38)
    | _span(40, 58)
    | _span(65, 90)
)
_FONT_1_2_SET = _span(32, 168) | {171, 172, 225}
_FONT_9_SET = (
    _span(32, 126)
    | _span(128, 169)
    | _span(171, 173)
    | _span(181, 184)
    | {189, 190, 198, 199}
    | _span(208, 216)
    | {222}
    | _span(224, 237)
    | {241, 243}
    | _span(246, 250)
)

#: Font number to the code page 437 byte values it can print.
FONT_BYTES: dict[int, frozenset[int]] = {
    0: _span(32, 127),
    1: _FONT_1_2_SET,
    2: _FONT_1_2_SET,
    3: _CAPITALS_SET,
    4: _CAPITALS_SET,
    5: _CAPITALS_SET,
    6: _CAPITALS_SET,
    7: _span(32, 126),
    8: frozenset({32, 60, 62, 67, 69, 78, 83, 84, 88, 90}) | _span(48, 57),
    9: _FONT_9_SET,
}
#: Fonts that print capitals only, so a lower-case letter is printed as its capital.
CAPITALS_ONLY_FONTS = frozenset({3, 4, 5, 6})

#: DPL symbol set id to the Python codec that encodes it byte for byte. Only sets whose layout a
#: standard-library codec reproduces exactly are listed; any other id is refused rather than
#: guessed, because a near-miss codec prints wrong glyphs without any error.
SYMBOL_SET_CODECS: dict[str, str] = {
    "PC": "cp437",
    "PM": "cp850",
    "PE": "cp852",
    "PV": "cp775",
    "E1": "latin-1",
    "E2": "iso8859-2",
    # The manual calls E5 "ISO 8859/5 Latin 5". Latin 5 (Turkish) is ISO 8859-9; ISO 8859-5 is
    # Cyrillic and is not Latin 5, so the name, not the part number, decides the codec.
    "E5": "iso8859-9",
    "W1": "cp1252",
    "WE": "cp1250",
    "WR": "cp1251",
    "WG": "cp1253",
    "WT": "cp1254",
    "US": "ascii",
}


class EncodedText(BaseModel, frozen=True, extra="forbid"):
    """Bytes ready for a DPL record, plus the characters the font could not print.

    Attributes:
        data: One byte per input character; ``?`` stands in for each unmappable one.
        unmappable: Each unmappable character once, in the order first seen.

    Example:
        >>> encode_for_bitmapped_font("Ma" + chr(0x00DF), font=1).data
        b'Ma\\xe1'
    """

    data: bytes
    unmappable: tuple[str, ...]


def encode_for_bitmapped_font(text: str, *, font: int) -> EncodedText:
    """Encode ``text`` for bitmapped font 0-9 (9 is CG Triumvirate).

    Fonts 3-6 print capitals only: a lower-case letter is printed as its capital when the font
    has that capital.

    Args:
        text: The text to print.
        font: Font number 0-9.

    Returns:
        The bytes and the characters the font cannot print.

    Raises:
        ValueError: ``font`` is not 0-9.

    Example:
        >>> encode_for_bitmapped_font("abc", font=3).data
        b'ABC'
        >>> encode_for_bitmapped_font("12x", font=8).unmappable
        ('x',)
    """
    allowed = FONT_BYTES.get(font)
    if allowed is None:
        msg = f"bitmapped font must be 0-9, got {font}"
        raise ValueError(msg)
    byte_for = partial(_bitmapped_byte, allowed=allowed, capitals_only=font in CAPITALS_ONLY_FONTS)
    return _encode(text, byte_for)


def encode_for_symbol_set(text: str, *, symbol_set: str | None) -> EncodedText:
    """Encode ``text`` for a scalable font printing through ``symbol_set``.

    Args:
        text: The text to print.
        symbol_set: The DPL symbol set id from ``ySxx``; None means ``PC`` (code page 437).

    Returns:
        The bytes and the characters the symbol set cannot print.

    Raises:
        ValueError: No exact codec is known for ``symbol_set`` (see :data:`SYMBOL_SET_CODECS`).

    Example:
        >>> encode_for_symbol_set(chr(0x00E4), symbol_set="W1").data
        b'\\xe4'
        >>> encode_for_symbol_set(chr(0x00E4), symbol_set=None).data
        b'\\x84'
    """
    set_id = DEFAULT_SYMBOL_SET if symbol_set is None else symbol_set
    codec = SYMBOL_SET_CODECS.get(set_id)
    if codec is None:
        known = ", ".join(sorted(SYMBOL_SET_CODECS))
        msg = f"no exact codec is known for symbol set {set_id[:MAX_ECHOED_ID]!r}; known: {known}"
        raise ValueError(msg)
    return _encode(text, partial(_codec_byte, codec=codec))


def _encode(text: str, byte_for: Callable[[str], int | None]) -> EncodedText:
    data = bytearray()
    unmappable: dict[str, None] = {}
    for ch in text:
        value = byte_for(ch)
        if value is None:
            unmappable.setdefault(ch, None)
            value = REPLACEMENT_BYTE
        data.append(value)
    return EncodedText(data=bytes(data), unmappable=tuple(unmappable))


def _codec_byte(ch: str, *, codec: str) -> int | None:
    if ord(ch) < FIRST_PRINTABLE:
        return None
    try:
        encoded = ch.encode(codec)
    except UnicodeEncodeError:
        return None
    return encoded[0] if len(encoded) == 1 else None


def _bitmapped_byte(ch: str, *, allowed: frozenset[int], capitals_only: bool) -> int | None:
    value = _codec_byte(ch, codec=BITMAPPED_CODEC)
    if value is not None and value in allowed:
        return value
    upper = ch.upper()
    if not capitals_only or len(upper) != 1 or upper == ch:
        return None
    capital = _codec_byte(upper, codec=BITMAPPED_CODEC)
    return capital if capital is not None and capital in allowed else None


__all__ = [
    "SYMBOL_SET_CODECS",
    "EncodedText",
    "encode_for_bitmapped_font",
    "encode_for_symbol_set",
]
