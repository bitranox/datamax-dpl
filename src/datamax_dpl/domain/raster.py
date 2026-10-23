"""Pure-Python 1-bit raster graphics for the image fallback and the PNG preview.

A :class:`Bitmap` is a mutable grid of black and white pixels with its origin at the TOP-LEFT
corner (row 0 is the top row). It draws the shapes a DPL label can hold - thick lines at any
angle, polygons, circles and pattern-filled rectangles - so a printer model without circle or
polygon records can still print them as a downloaded image, and so the preview can render a
label without Pillow.

The DPL fill patterns (``FillPattern`` 0-11) are named in the manual but their dot layout is not
documented. The 8x8 tiles here APPROXIMATE them: the five shades are ordered dither on an 8x8
Bayer matrix, whose black share is the nearest multiple of 1/64 to the nominal percentage, and
diamonds, circles, both diagonals and the grid are small repeating line drawings. A printer's
own pattern may differ in phase and texture.

Each row is held as one Python ``int`` whose most significant bit (of ``width`` bits) is the
leftmost pixel, so whole-row operations (spans, pattern masks, blits) are single integer
operations and :meth:`Bitmap.rows` packs MSB-first without reordering bits.
"""

from __future__ import annotations

import itertools
import math
from typing import TYPE_CHECKING

from .enums import FillPattern

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

#: Edge length of every fill-pattern tile, in pixels.
TILE_SIZE = 8
#: Bits per index needed to address one axis of the Bayer tile (TILE_SIZE == 2**3).
_BAYER_BITS = TILE_SIZE.bit_length() - 1
_PERCENT = 100
_BITS_PER_BYTE = 8
_BLACK_CHARS = "1#"
_WHITE_CHARS = "0."
_TO_BITS = str.maketrans("#.", "10")

_SHADE_PERCENT: dict[FillPattern, int] = {
    FillPattern.SHADE_6: 6,
    FillPattern.SHADE_12: 12,
    FillPattern.SHADE_25: 25,
    FillPattern.SHADE_38: 38,
    FillPattern.SHADE_50: 50,
}

_DIAMOND_TILE = (
    "...#....",
    "..#.#...",
    ".#...#..",
    "#.....#.",
    ".#...#..",
    "..#.#...",
    "...#....",
    "........",
)

_CIRCLE_TILE = (
    "........",
    "..###...",
    ".#...#..",
    ".#...#..",
    ".#...#..",
    "..###...",
    "........",
    "........",
)


def _bayer_rank(x: int, y: int) -> int:
    """Return the dither rank (0-63) of cell (x, y) in the 8x8 Bayer matrix.

    The rank interleaves the bits of ``x ^ y`` and ``y``, least significant first, which is the
    closed form of the recursive Bayer construction.

    Example:
        >>> [_bayer_rank(x, 0) for x in range(4)]
        [0, 32, 8, 40]
    """
    mixed = x ^ y
    rank = 0
    for bit in range(_BAYER_BITS):
        rank = (rank << 2) | (((mixed >> bit) & 1) << 1) | ((y >> bit) & 1)
    return rank


def _shade_tile(percent: int) -> tuple[str, ...]:
    """Return the dither tile whose black share is closest to ``percent``."""
    cutoff = round(percent * TILE_SIZE * TILE_SIZE / _PERCENT)
    return tuple(
        "".join("1" if _bayer_rank(x, y) < cutoff else "0" for x in range(TILE_SIZE)) for y in range(TILE_SIZE)
    )


def _drawn_tile(rows: tuple[str, ...]) -> tuple[str, ...]:
    """Translate a ``#``/``.`` tile drawing into ``1``/``0`` rows."""
    return tuple(row.translate(_TO_BITS) for row in rows)


def _line_tile(*, column_of_row: int, slope: int) -> tuple[str, ...]:
    """Return a tile with one black pixel per row at ``column_of_row + slope * row`` (mod tile)."""
    return tuple(
        "".join("1" if x == (column_of_row + slope * y) % TILE_SIZE else "0" for x in range(TILE_SIZE))
        for y in range(TILE_SIZE)
    )


def _grid_tile() -> tuple[str, ...]:
    """Return a tile whose top row and left column are black."""
    return ("1" * TILE_SIZE,) + ("1" + "0" * (TILE_SIZE - 1),) * (TILE_SIZE - 1)


def _build_tiles() -> dict[FillPattern, tuple[str, ...]]:
    """Return the 8x8 ``1``/``0`` tile of every fill pattern."""
    tiles: dict[FillPattern, tuple[str, ...]] = {
        FillPattern.NONE: ("0" * TILE_SIZE,) * TILE_SIZE,
        FillPattern.SOLID: ("1" * TILE_SIZE,) * TILE_SIZE,
        FillPattern.DIAMONDS: _drawn_tile(_DIAMOND_TILE),
        FillPattern.CIRCLES: _drawn_tile(_CIRCLE_TILE),
        # With row 0 at the top, "/" runs from the bottom-left up to the top-right corner.
        FillPattern.RIGHT_DIAGONAL: _line_tile(column_of_row=TILE_SIZE - 1, slope=-1),
        FillPattern.LEFT_DIAGONAL: _line_tile(column_of_row=0, slope=1),
        FillPattern.GRID: _grid_tile(),
    }
    tiles.update({pattern: _shade_tile(percent) for pattern, percent in _SHADE_PERCENT.items()})
    return tiles


_TILES = _build_tiles()


def pattern_pixel(pattern: FillPattern, x: int, y: int) -> bool:
    """Return whether pixel (x, y) is black when an area is filled with ``pattern``.

    The pattern is anchored to the bitmap origin, so two adjacent fills line up seamlessly.

    Args:
        pattern: The DPL fill pattern.
        x: Column, any integer (the tile repeats in both directions).
        y: Row, any integer, row 0 at the top.

    Returns:
        True for a black pixel.

    Example:
        >>> pattern_pixel(FillPattern.SOLID, -3, 7), pattern_pixel(FillPattern.NONE, 0, 0)
        (True, False)
    """
    return _TILES[pattern][y % TILE_SIZE][x % TILE_SIZE] == "1"


def _round_div(numerator: int, denominator: int) -> int:
    """Return ``numerator / denominator`` rounded half up; ``denominator`` must be positive."""
    return (2 * numerator + denominator) // (2 * denominator)


def _ceil_div(numerator: int, denominator: int) -> int:
    """Return ``ceil(numerator / denominator)``; ``denominator`` must be positive."""
    return -(-numerator // denominator)


def _walk(start: tuple[int, int], end: tuple[int, int], *, low: int, high: int) -> Iterator[tuple[int, int]]:
    """Yield the Bresenham points of a line whose first axis is its major axis.

    Only points whose major coordinate lies in ``[low, high]`` are produced, so a line with huge
    endpoints costs as much as its visible part.
    """
    (a0, b0), (a1, b1) = sorted((start, end))
    span = a1 - a0
    for a in range(max(a0, low), min(a1, high) + 1):
        yield a, (b0 + _round_div((a - a0) * (b1 - b0), span) if span else b0)


def _crossings(points: Sequence[tuple[int, int]], y: int) -> list[int]:
    """Return, sorted, the first pixel column at or right of each edge crossing of row ``y``.

    An edge counts for rows ``y_low <= y < y_high`` (half-open), so a vertex shared by two edges
    is counted once and horizontal edges never count - the usual even-odd scanline rule.
    """
    result: list[int] = []
    for (x0, y0), (x1, y1) in zip(points, itertools.chain(points[1:], points[:1]), strict=True):
        (ya, xa), (yb, xb) = sorted(((y0, x0), (y1, x1)))
        if ya <= y < yb:
            result.append(xa + _ceil_div((y - ya) * (xb - xa), yb - ya))
    return sorted(result)


def _check_non_negative(name: str, value: int) -> None:
    """Raise ValueError when ``value`` is negative."""
    if value < 0:
        msg = f"{name} must not be negative, got {value}"
        raise ValueError(msg)


class Bitmap:
    """A mutable 1-bit image, ``width`` x ``height`` pixels, origin top-left, black = 1.

    Every drawing method clips silently: coordinates may be negative or far outside the image.

    Attributes:
        width: Pixels per row (at least 1).
        height: Number of rows (at least 1).

    Example:
        >>> bitmap = Bitmap(10, 3)
        >>> bitmap.draw_line(0, 1, 9, 1)
        >>> bitmap.black_pixels(), bitmap.get(4, 1), bitmap.get(4, 0)
        (10, True, False)
    """

    def __init__(self, width: int, height: int) -> None:
        """Create an all-white bitmap.

        Args:
            width: Pixels per row, at least 1.
            height: Number of rows, at least 1.

        Raises:
            ValueError: When either size is below 1.
        """
        if width < 1 or height < 1:
            msg = f"a bitmap needs at least 1 x 1 pixels, got {width} x {height}"
            raise ValueError(msg)
        self.width = width
        self.height = height
        self._rows: list[int] = [0] * height
        self._pattern_masks: dict[tuple[FillPattern, int], int] = {}

    def __repr__(self) -> str:
        """Return a short description with the size and the number of black pixels."""
        return f"Bitmap(width={self.width}, height={self.height}, black={self.black_pixels()})"

    def get(self, x: int, y: int) -> bool:
        """Return whether pixel (x, y) is black; a pixel outside the image reads as white."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False
        return bool(self._rows[y] >> self._bit(x) & 1)

    def set(self, x: int, y: int, *, black: bool = True) -> None:
        """Make pixel (x, y) black (or white with ``black=False``); outside the image is ignored."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        if black:
            self._rows[y] |= 1 << self._bit(x)
        else:
            self._rows[y] &= ~(1 << self._bit(x))

    def fill_rect(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        pattern: FillPattern = FillPattern.SOLID,
    ) -> None:
        """Blacken the pattern's pixels inside a rectangle whose top-left corner is (x, y).

        A zero or negative width or height draws nothing.

        Example:
            >>> bitmap = Bitmap(8, 8)
            >>> bitmap.fill_rect(2, 2, 3, 2)
            >>> bitmap.black_pixels()
            6
        """
        for row in range(max(y, 0), min(y + height, self.height)):
            self._paint_span(row, first=x, last=x + width - 1, pattern=pattern)

    def draw_line(self, x0: int, y0: int, x1: int, y1: int, *, thickness: int = 1) -> None:
        """Draw a straight line, stamping a ``thickness`` x ``thickness`` square at each point.

        Args:
            x0: Start column.
            y0: Start row.
            x1: End column.
            y1: End row.
            thickness: Edge length of the stamped square, at least 1. An even thickness extends
                one pixel further right and down than left and up.

        Raises:
            ValueError: When ``thickness`` is below 1.

        Example:
            >>> bitmap = Bitmap(20, 20)
            >>> bitmap.draw_line(0, 10, 19, 10, thickness=3)
            >>> [row for row in range(20) if bitmap.get(5, row)]
            [9, 10, 11]
        """
        if thickness < 1:
            msg = f"line thickness must be at least 1, got {thickness}"
            raise ValueError(msg)
        for x, y in self._line_points((x0, y0), (x1, y1), margin=thickness):
            self._stamp(x, y, thickness)

    def draw_polygon(
        self,
        points: Sequence[tuple[int, int]],
        *,
        outline: int = 1,
        fill: FillPattern = FillPattern.NONE,
    ) -> None:
        """Draw a closed polygon: optional even-odd pattern fill, then the outline.

        Args:
            points: Vertices in order; the last one connects back to the first.
            outline: Outline thickness in pixels; 0 draws no outline.
            fill: Pattern for the interior; :attr:`FillPattern.NONE` leaves it white.

        Raises:
            ValueError: When ``points`` is empty or ``outline`` is negative.

        Example:
            >>> bitmap = Bitmap(10, 10)
            >>> bitmap.draw_polygon([(1, 1), (8, 1), (8, 8), (1, 8)], fill=FillPattern.SOLID)
            >>> bitmap.black_pixels()
            64
        """
        if not points:
            msg = "a polygon needs at least one point"
            raise ValueError(msg)
        _check_non_negative("outline", outline)
        if fill is not FillPattern.NONE:
            self._fill_polygon(points, fill)
        if outline == 0:
            return
        for (x0, y0), (x1, y1) in zip(points, itertools.chain(points[1:], points[:1]), strict=True):
            self.draw_line(x0, y0, x1, y1, thickness=outline)

    def draw_circle(
        self,
        cx: int,
        cy: int,
        radius: int,
        *,
        outline: int = 1,
        fill: FillPattern = FillPattern.NONE,
    ) -> None:
        """Draw a circle: optional pattern fill, then an outline ``outline`` pixels thick inward.

        A pixel belongs to the disc when its squared distance from the centre is at most
        ``radius**2 + radius``, which rounds the edge to the nearest half pixel and keeps the four
        extreme points (centre plus or minus ``radius`` on each axis) inside.

        Args:
            cx: Centre column.
            cy: Centre row.
            radius: Radius in pixels, at least 0.
            outline: Ring thickness; 0 draws no outline.
            fill: Pattern for the interior; :attr:`FillPattern.NONE` leaves it white.

        Raises:
            ValueError: When ``radius`` or ``outline`` is negative.

        Example:
            >>> bitmap = Bitmap(21, 21)
            >>> bitmap.draw_circle(10, 10, 8)
            >>> bitmap.get(18, 10), bitmap.get(10, 10)
            (True, False)
        """
        _check_non_negative("radius", radius)
        _check_non_negative("outline", outline)
        outer = radius * radius + radius
        hole_radius = radius - outline
        inner = hole_radius * hole_radius + hole_radius if hole_radius >= 0 else -1
        for y in range(max(cy - radius, 0), min(cy + radius, self.height - 1) + 1):
            dy2 = (y - cy) * (y - cy)
            if fill is not FillPattern.NONE:
                half = math.isqrt(outer - dy2)
                self._paint_span(y, first=cx - half, last=cx + half, pattern=fill)
            if outline:
                self._rows[y] |= self._ring_mask(cx, outer - dy2, inner - dy2)

    def blit(self, other: Bitmap, *, x: int, y: int) -> None:
        """OR ``other`` onto this bitmap with its top-left corner at (x, y), clipped.

        Example:
            >>> canvas, stamp = Bitmap(4, 4), Bitmap.from_rows(["##", "##"])
            >>> canvas.blit(stamp, x=3, y=3)
            >>> canvas.black_pixels()
            1
        """
        # Without this early exit a far-away x would shift a row by billions of bits.
        if x >= self.width or x + other.width <= 0:
            return
        shift = self.width - x - other.width
        full = (1 << self.width) - 1
        for target in range(max(y, 0), min(y + other.height, self.height)):
            row = other._rows[target - y]
            moved = row << shift if shift >= 0 else row >> -shift
            self._rows[target] |= moved & full

    def rows(self) -> list[bytes]:
        """Return each row, top row first, packed MSB-first and padded with white to whole bytes.

        Example:
            >>> Bitmap.from_rows(["#.#"]).rows()
            [b'\\xa0']
        """
        size = self._row_bytes()
        pad = size * _BITS_PER_BYTE - self.width
        return [(row << pad).to_bytes(size, "big") for row in self._rows]

    def black_pixels(self) -> int:
        """Return the number of black pixels."""
        return sum(row.bit_count() for row in self._rows)

    @classmethod
    def from_rows(cls, rows: Sequence[str]) -> Bitmap:
        """Build a bitmap from text rows, top row first: ``1``/``#`` black, ``0``/``.`` white.

        This is the format of the label model's ``Image.bitmap``.

        Raises:
            ValueError: When there are no rows, the rows differ in length or are empty, or a row
                holds any other character.

        Example:
            >>> bitmap = Bitmap.from_rows(["#.", "01"])
            >>> bitmap.get(0, 0), bitmap.get(1, 0), bitmap.get(1, 1)
            (True, False, True)
        """
        widths = {len(row) for row in rows}
        if len(widths) != 1 or 0 in widths:
            msg = "bitmap rows must be at least one row of the same, non-zero length"
            raise ValueError(msg)
        if any(set(row) - set(_BLACK_CHARS + _WHITE_CHARS) for row in rows):
            msg = "bitmap rows may only contain 1, 0, # and ."
            raise ValueError(msg)
        bitmap = cls(widths.pop(), len(rows))
        bitmap._rows = [int(row.translate(_TO_BITS), 2) for row in rows]
        return bitmap

    @classmethod
    def from_packed_rows(cls, rows: Sequence[bytes], *, width: int) -> Bitmap:
        """Build a bitmap from MSB-first packed rows, the inverse of :meth:`rows`.

        Args:
            rows: One ``bytes`` per row, top row first, each ``ceil(width / 8)`` bytes long.
            width: Pixels per row; padding bits beyond it are ignored.

        Raises:
            ValueError: When there are no rows or a row has the wrong length.

        Example:
            >>> Bitmap.from_packed_rows([b"\\xa0"], width=3).get(2, 0)
            True
        """
        bitmap = cls(width, len(rows))
        size = bitmap._row_bytes()
        if any(len(row) != size for row in rows):
            msg = f"every packed row must be {size} bytes for width {width}"
            raise ValueError(msg)
        pad = size * _BITS_PER_BYTE - width
        bitmap._rows = [int.from_bytes(row, "big") >> pad for row in rows]
        return bitmap

    def _bit(self, x: int) -> int:
        """Return the bit index of column ``x`` inside a row integer."""
        return self.width - 1 - x

    def _row_bytes(self) -> int:
        """Return the number of bytes a packed row takes."""
        return _ceil_div(self.width, _BITS_PER_BYTE)

    def _span_mask(self, first: int, last: int) -> int:
        """Return the row mask of columns ``first..last`` (inclusive), clipped to the image."""
        first, last = max(first, 0), min(last, self.width - 1)
        if first > last:
            return 0
        return ((1 << (last - first + 1)) - 1) << self._bit(last)

    def _pattern_mask(self, pattern: FillPattern, y: int) -> int:
        """Return the full-width row mask of ``pattern`` for row ``y``, cached per tile row."""
        key = (pattern, y % TILE_SIZE)
        mask = self._pattern_masks.get(key)
        if mask is None:
            tile_row = _TILES[pattern][key[1]]
            mask = int((tile_row * _ceil_div(self.width, TILE_SIZE))[: self.width], 2)
            self._pattern_masks[key] = mask
        return mask

    def _paint_span(self, y: int, *, first: int, last: int, pattern: FillPattern) -> None:
        """Blacken the pattern's pixels of row ``y`` between ``first`` and ``last`` inclusive."""
        self._rows[y] |= self._span_mask(first, last) & self._pattern_mask(pattern, y)

    def _ring_mask(self, cx: int, outer_room: int, inner_room: int) -> int:
        """Return one row of a circle outline from the squared half-chords of both edges."""
        outer_half = math.isqrt(outer_room)
        disc = self._span_mask(cx - outer_half, cx + outer_half)
        if inner_room < 0:
            return disc
        inner_half = math.isqrt(inner_room)
        return disc & ~self._span_mask(cx - inner_half, cx + inner_half)

    def _line_points(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        *,
        margin: int,
    ) -> Iterator[tuple[int, int]]:
        """Yield the line's points whose stamps can reach the image, walking its major axis."""
        (x0, y0), (x1, y1) = start, end
        if abs(x1 - x0) >= abs(y1 - y0):
            yield from _walk(start, end, low=-margin, high=self.width - 1 + margin)
            return
        for y, x in _walk((y0, x0), (y1, x1), low=-margin, high=self.height - 1 + margin):
            yield x, y

    def _stamp(self, x: int, y: int, size: int) -> None:
        """Blacken a ``size`` x ``size`` square centred on (x, y)."""
        before, after = (size - 1) // 2, size // 2
        mask = self._span_mask(x - before, x + after)
        if mask == 0:
            return
        for row in range(max(y - before, 0), min(y + after, self.height - 1) + 1):
            self._rows[row] |= mask

    def _fill_polygon(self, points: Sequence[tuple[int, int]], pattern: FillPattern) -> None:
        """Fill the polygon's interior with ``pattern`` by the even-odd rule.

        A pixel is inside when its column lies in ``[left crossing, right crossing)``, so a
        rectangle from (0, 0) to (4, 4) fills exactly the 4 x 4 pixels ``fill_rect(0, 0, 4, 4)``
        would, and the outline drawn afterwards covers the right and bottom edges.
        """
        rows = [y for _, y in points]
        for y in range(max(min(rows), 0), min(max(rows), self.height - 1) + 1):
            crossings = _crossings(points, y)
            for left, right in zip(crossings[0::2], crossings[1::2], strict=True):
                self._paint_span(y, first=left, last=right - 1, pattern=pattern)


__all__ = ["TILE_SIZE", "Bitmap", "pattern_pixel"]
