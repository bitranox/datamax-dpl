"""Barcode symbologies and the DPL identifiers that select them.

A DPL barcode record names its symbology with one character: the upper-case letter prints the
human-readable text under the bars, the lower-case one prints the bars only. A few symbologies
exist in one case only (Postnet, MaxiCode, FIM, PDF417), and DataMatrix uses the three-character
identifier ``W1c``. See ``docs/dpl-reference.md`` section 7.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class Symbology(str, Enum):
    """Barcode symbology; the value is the name used in JSON and on the command line.

    Example:
        >>> Symbology("code128") is Symbology.CODE128
        True
    """

    CODE39 = "code39"
    UPC_A = "upc_a"
    UPC_E = "upc_e"
    I2OF5 = "i2of5"
    CODE128 = "code128"
    EAN13 = "ean13"
    EAN8 = "ean8"
    HIBC = "hibc"
    CODABAR = "codabar"
    I2OF5_MOD10 = "i2of5_mod10"
    PLESSEY = "plessey"
    I2OF5_BEARER = "i2of5_bearer"
    ADDON2 = "addon2"
    ADDON5 = "addon5"
    CODE93 = "code93"
    POSTNET = "postnet"
    UCC_EAN128 = "ucc_ean128"
    UCC_EAN128_KMART = "ucc_ean128_kmart"
    UCC_EAN128_RANDOM_WEIGHT = "ucc_ean128_random_weight"
    TELEPEN = "telepen"
    MAXICODE = "maxicode"
    FIM = "fim"
    PDF417 = "pdf417"
    DATAMATRIX = "datamatrix"


@dataclass(frozen=True)
class SymbologyCode:
    """How one symbology is written in a DPL record.

    Attributes:
        with_text: Identifier that prints human-readable text, or None when the symbology has none.
        bars_only: Identifier that prints the bars alone; every symbology has one.
        two_dimensional: True for the stacked and matrix codes, whose ``c``/``d`` header digits are
            a module size rather than a wide:narrow ratio.
    """

    with_text: str | None
    bars_only: str
    two_dimensional: bool = False

    def identifier(self, *, human_readable: bool) -> str:
        """Return the identifier for the requested text setting, falling back to the one that exists.

        Example:
            >>> SYMBOLOGY_CODES[Symbology.CODE128].identifier(human_readable=False)
            'e'
            >>> SYMBOLOGY_CODES[Symbology.POSTNET].identifier(human_readable=True)
            'p'
        """
        return self.with_text if human_readable and self.with_text is not None else self.bars_only

    def offers_text_choice(self) -> bool:
        """Return True when the symbology can be printed both with and without text."""
        return self.with_text is not None


def _pair(letter: str) -> SymbologyCode:
    return SymbologyCode(with_text=letter.upper(), bars_only=letter.lower())


SYMBOLOGY_CODES: Mapping[Symbology, SymbologyCode] = MappingProxyType(
    {
        Symbology.CODE39: _pair("a"),
        Symbology.UPC_A: _pair("b"),
        Symbology.UPC_E: _pair("c"),
        Symbology.I2OF5: _pair("d"),
        Symbology.CODE128: _pair("e"),
        Symbology.EAN13: _pair("f"),
        Symbology.EAN8: _pair("g"),
        Symbology.HIBC: _pair("h"),
        Symbology.CODABAR: _pair("i"),
        Symbology.I2OF5_MOD10: _pair("j"),
        Symbology.PLESSEY: _pair("k"),
        Symbology.I2OF5_BEARER: _pair("l"),
        Symbology.ADDON2: _pair("m"),
        Symbology.ADDON5: _pair("n"),
        Symbology.CODE93: _pair("o"),
        Symbology.POSTNET: SymbologyCode(with_text=None, bars_only="p"),
        Symbology.UCC_EAN128: _pair("q"),
        Symbology.UCC_EAN128_KMART: _pair("r"),
        Symbology.UCC_EAN128_RANDOM_WEIGHT: _pair("s"),
        Symbology.TELEPEN: _pair("t"),
        Symbology.MAXICODE: SymbologyCode(with_text=None, bars_only="u", two_dimensional=True),
        Symbology.FIM: SymbologyCode(with_text=None, bars_only="v"),
        Symbology.PDF417: SymbologyCode(with_text=None, bars_only="z", two_dimensional=True),
        Symbology.DATAMATRIX: SymbologyCode(with_text=None, bars_only="W1c", two_dimensional=True),
    }
)

#: Identifier character (or ``W1c``) back to its symbology, for decoding DPL.
SYMBOLOGY_BY_IDENTIFIER: Mapping[str, Symbology] = MappingProxyType(
    {
        identifier: symbology
        for symbology, code in SYMBOLOGY_CODES.items()
        for identifier in (code.with_text, code.bars_only)
        if identifier is not None
    }
)

__all__ = ["SYMBOLOGY_BY_IDENTIFIER", "SYMBOLOGY_CODES", "Symbology", "SymbologyCode"]
