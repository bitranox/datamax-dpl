"""Byte-level formatting of DPL records and command lines.

Pure helpers shared by the encoders: the 15-character record header, the multiplier digit, the
graphics records (line, box, polygon, circle, image placement) and the command terminator.
Field layouts are in ``docs/dpl-reference.md`` section 5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .enums import FillPattern, Rotation

if TYPE_CHECKING:
    from collections.abc import Sequence

STX = b"\x02"
SOH = b"\x01"
CR = b"\r"

#: Largest value of a three-digit graphics field before the four-digit form must be used.
MAX_SHORT_FIELD = 999
#: Largest width or height multiplier (written ``1``-``9``, then ``A``-``O``).
MAX_MULTIPLIER = 24
_DIGIT_MULTIPLIERS = 9


def multiplier_char(value: int) -> str:
    """Return the one-character DPL multiplier: ``1``-``9``, then ``A`` (10) to ``O`` (24).

    ``0`` is allowed for barcodes, where it asks for the printer's default width.

    Raises:
        ValueError: ``value`` is outside 0-24.

    Example:
        >>> multiplier_char(9), multiplier_char(10), multiplier_char(24)
        ('9', 'A', 'O')
    """
    if not 0 <= value <= MAX_MULTIPLIER:
        msg = f"multiplier must be 0-{MAX_MULTIPLIER}, got {value}"
        raise ValueError(msg)
    if value <= _DIGIT_MULTIPLIERS:
        return str(value)
    return chr(ord("A") + value - _DIGIT_MULTIPLIERS - 1)


def multiplier_value(char: str) -> int:
    """Inverse of :func:`multiplier_char`.

    Example:
        >>> multiplier_value("O")
        24
    """
    if char.isdigit():
        return int(char)
    return ord(char.upper()) - ord("A") + _DIGIT_MULTIPLIERS + 1


@dataclass(frozen=True)
class Header:
    """The record header ``a b c d eee ffff gggg``.

    Attributes:
        rotation: Field rotation.
        kind: Font digit, barcode identifier, ``X`` or ``Y`` (``W1c`` for DataMatrix).
        width: Width multiplier or wide bar width (0-24).
        height: Height multiplier or narrow bar width (0-24).
        size: Three-character size field (``000``, a barcode height, a font 9 size code).
        row: Distance from the bottom, 0-9999.
        column: Distance from the left, 0-9999.

    Example:
        >>> Header(rotation=Rotation.DEG_0, kind="2", width=1, height=1, size="000", row=50, column=25).encode()
        b'121100000500025'
    """

    rotation: Rotation
    kind: str
    width: int
    height: int
    size: str
    row: int
    column: int

    def encode(self) -> bytes:
        """Return the header bytes."""
        widths = multiplier_char(self.width) + multiplier_char(self.height)
        text = f"{self.rotation.dpl_digit}{self.kind}{widths}{self.size}{self.row:04d}{self.column:04d}"
        return text.encode("ascii")


def line(text: str | bytes) -> bytes:
    """Terminate a format line with ``<CR>``.

    Example:
        >>> line("Q0001")
        b'Q0001\\r'
    """
    raw = text.encode("ascii") if isinstance(text, str) else text
    return raw + CR


def _graphic_header(*, row: int, column: int, fill: FillPattern = FillPattern.NONE) -> str:
    return f"1X11{fill.value:03d}{row:04d}{column:04d}"


def line_record(*, row: int, column: int, horizontal: int, vertical: int) -> bytes:
    """A filled rectangle line: ``L`` with three-digit sizes, ``l`` with four.

    Example:
        >>> line_record(row=20, column=0, horizontal=400, vertical=1)
        b'1X1100000200000L400001'
    """
    head = _graphic_header(row=row, column=column)
    if max(horizontal, vertical) <= MAX_SHORT_FIELD:
        return f"{head}L{horizontal:03d}{vertical:03d}".encode("ascii")
    return f"{head}l{horizontal:04d}{vertical:04d}".encode("ascii")


@dataclass(frozen=True)
class BoxRecord:
    """A box outline: ``B`` with three-digit fields, ``b`` with four.

    Example:
        >>> BoxRecord(row=0, column=0, width=400, height=400, edge=3, side=3).encode()
        b'1X1100000000000B400400003003'
    """

    row: int
    column: int
    width: int
    height: int
    edge: int
    side: int

    def encode(self) -> bytes:
        """Return the record bytes."""
        head = _graphic_header(row=self.row, column=self.column)
        sizes = (self.width, self.height, self.edge, self.side)
        if max(sizes) <= MAX_SHORT_FIELD:
            return (head + "B" + "".join(f"{v:03d}" for v in sizes)).encode("ascii")
        return (head + "b" + "".join(f"{v:04d}" for v in sizes)).encode("ascii")


def polygon_record(points: Sequence[tuple[int, int]], *, fill: FillPattern) -> bytes:
    """A polygon through ``points`` given as ``(row, column)``; the printer closes it.

    Example:
        >>> polygon_record([(10, 10), (40, 25), (10, 40)], fill=FillPattern.NONE)
        b'1X1100000100010P00100010040002500100040'
    """
    first_row, first_column = points[0]
    head = _graphic_header(row=first_row, column=first_column, fill=fill)
    vertices = "".join(f"{row:04d}{column:04d}" for row, column in points[1:])
    return f"{head}P0010001{vertices}".encode("ascii")


def circle_record(*, row: int, column: int, radius: int, fill: FillPattern) -> bytes:
    """A circle around ``(row, column)``.

    Example:
        >>> circle_record(row=100, column=100, radius=25, fill=FillPattern.NONE)
        b'1X1100001000100C00100010025'
    """
    head = _graphic_header(row=row, column=column, fill=fill)
    return f"{head}C0010001{radius:04d}".encode("ascii")


def image_record(*, row: int, column: int, name: str) -> bytes:
    """Place a stored image (rotation 1 only).

    Example:
        >>> image_record(row=0, column=0, name="LOGO")
        b'1Y1100000000000LOGO'
    """
    return f"1Y11000{row:04d}{column:04d}{name}".encode("ascii")


__all__ = [
    "CR",
    "MAX_MULTIPLIER",
    "MAX_SHORT_FIELD",
    "SOH",
    "STX",
    "BoxRecord",
    "Header",
    "circle_record",
    "image_record",
    "line",
    "line_record",
    "multiplier_char",
    "multiplier_value",
    "polygon_record",
]
