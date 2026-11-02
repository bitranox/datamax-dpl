"""How much a QR Code holds: text counts its UTF-8 bytes, and the size table in the docs is true."""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from datamax_dpl.domain.enums import QrError
from datamax_dpl.domain.qr import QrDataTooLongError, qr_bitmap

_LABEL_SPEC = Path(__file__).resolve().parent.parent / "docs" / "label-spec.md"
_TABLE_ROW = re.compile(r"^\| V(\d+) +\| (\d+) +\| (\d+) +\| ([\d.]+) mm +\| ([\d.]+) mm +\|$", re.MULTILINE)

_DOTS_PER_MM_AT_203_DPI = 8
_DOCUMENTED_ROWS = 8
_VERSION_20_MODULES = 97
_VERSION_21_MODULES = 101
_VERSION_20_BYTES_AT_M = 666
_LARGEST_VERSION = 40


def _modules(text: str) -> int:
    return qr_bitmap(text, error=QrError.M).width


def _table_width(modules: int, dots: int) -> float:
    """Return the symbol's width in mm, rounded up to the half millimetre the table states."""
    return math.ceil(modules * dots / _DOTS_PER_MM_AT_203_DPI * 2) / 2


@pytest.mark.parametrize(
    ("character", "utf8_bytes"),
    [
        pytest.param(chr(0xE4), 2, id="a-umlaut, which segno would otherwise send as kanji"),
        pytest.param(chr(0xB0), 2, id="degree sign, which segno would otherwise send as Latin-1"),
        pytest.param(chr(0x20AC), 3, id="euro sign"),
        pytest.param(chr(0x5B57), 3, id="CJK character, which segno would otherwise send as kanji"),
    ],
)
def test_text_outside_ascii_takes_as_much_room_as_its_utf8_bytes(character: str, utf8_bytes: int) -> None:
    """The symbol holds UTF-8, the encoding a reader with no ECI marker reads correctly."""
    most = _VERSION_20_BYTES_AT_M // utf8_bytes

    assert _modules(character * most) == _VERSION_20_MODULES
    assert _modules(character * (most + 1)) == _VERSION_21_MODULES


def test_digits_and_capitals_keep_their_compact_modes() -> None:
    """UTF-8 applies to byte mode only: digits and capitals still hold more than bytes do."""
    assert _modules("1" * 1600) == _VERSION_20_MODULES
    assert _modules("1" * 1601) == _VERSION_21_MODULES
    assert _modules("A" * 970) == _VERSION_20_MODULES
    assert _modules("A" * 971) == _VERSION_21_MODULES


def test_the_documented_size_table_is_what_the_code_builds() -> None:
    """Each row's modules, byte capacity and widths come out of the real encoder."""
    rows = _TABLE_ROW.findall(_LABEL_SPEC.read_text(encoding="utf-8"))

    assert len(rows) == _DOCUMENTED_ROWS
    for version, modules, capacity, at_3_dots, at_4_dots in rows:
        assert _modules("a" * int(capacity)) == int(modules), f"V{version}"
        assert int(modules) == 17 + 4 * int(version), f"V{version}"
        assert _table_width(int(modules), 3) == float(at_3_dots), f"V{version}"
        assert _table_width(int(modules), 4) == float(at_4_dots), f"V{version}"
        if int(version) < _LARGEST_VERSION:
            assert _modules("a" * (int(capacity) + 1)) > int(modules), f"V{version}"


def test_one_byte_past_the_largest_version_is_refused() -> None:
    """Version 40 at level M is the end of the table and of the standard."""
    last = 2331

    assert _modules("a" * last) == 17 + 4 * _LARGEST_VERSION
    with pytest.raises(QrDataTooLongError):
        _modules("a" * (last + 1))
