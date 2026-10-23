"""A minimal PNG writer built on the standard library (``zlib`` and ``struct``) only.

It writes exactly two kinds of image for the label preview: 1-bit grayscale from a
:class:`~datamax_dpl.domain.raster.Bitmap`, and 8-bit RGB for coloured overlays. Every scanline
uses filter type 0 (none) and there is no interlacing, which keeps the encoder small; the label
images are mostly white, so zlib alone compresses them well.
"""

from __future__ import annotations

import struct
import zlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .raster import Bitmap

#: The eight bytes every PNG file starts with.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_COLOUR_GRAY = 0
_COLOUR_RGB = 2
_DEPTH_1 = 1
_DEPTH_8 = 8
_BYTES_PER_RGB_PIXEL = 3
_FILTER_NONE = b"\x00"
_COMPRESSION_LEVEL = 9
_BITS_PER_BYTE = 8
#: Bitmap black (1) becomes PNG black (0) and white the other way round, each widened by scale.
_INVERT = str.maketrans("01", "10")


def _chunk(kind: bytes, data: bytes) -> bytes:
    """Return one PNG chunk: length, type, data and the CRC over type plus data."""
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _png(*, width: int, height: int, depth: int, colour: int, scanlines: Sequence[bytes]) -> bytes:
    """Assemble a non-interlaced PNG from unfiltered scanlines."""
    header = struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, 0)
    raw = b"".join(_FILTER_NONE + line for line in scanlines)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, _COMPRESSION_LEVEL))
        + _chunk(b"IEND", b"")
    )


def _scaled_scanline(packed: bytes, *, width: int, scale: int) -> bytes:
    """Return one bitmap row as a 1-bit PNG scanline, inverted and widened ``scale`` times."""
    bits = format(int.from_bytes(packed, "big"), f"0{len(packed) * _BITS_PER_BYTE}b")[:width]
    widened = "".join(bit * scale for bit in bits.translate(_INVERT))
    size = -(-len(widened) // _BITS_PER_BYTE)
    return int(widened.ljust(size * _BITS_PER_BYTE, "0"), 2).to_bytes(size, "big")


def encode_png(bitmap: Bitmap, *, scale: int = 1) -> bytes:
    """Encode a bitmap as a 1-bit grayscale PNG (black pixels stored as 0, white as 1).

    Args:
        bitmap: The image.
        scale: Each pixel becomes a ``scale`` x ``scale`` block, at least 1.

    Returns:
        The PNG file contents.

    Raises:
        ValueError: When ``scale`` is below 1.

    Example:
        >>> from datamax_dpl.domain.raster import Bitmap
        >>> encode_png(Bitmap(2, 2), scale=3)[:8] == PNG_SIGNATURE
        True
    """
    if scale < 1:
        msg = f"scale must be at least 1, got {scale}"
        raise ValueError(msg)
    scanlines: list[bytes] = []
    for packed in bitmap.rows():
        scanlines.extend([_scaled_scanline(packed, width=bitmap.width, scale=scale)] * scale)
    return _png(
        width=bitmap.width * scale,
        height=bitmap.height * scale,
        depth=_DEPTH_1,
        colour=_COLOUR_GRAY,
        scanlines=scanlines,
    )


def encode_png_rgb(pixels: Sequence[bytes], *, width: int, height: int) -> bytes:
    """Encode 8-bit RGB rows (3 bytes per pixel, top row first) as a PNG.

    Args:
        pixels: One ``bytes`` per row, each ``3 * width`` long.
        width: Pixels per row, at least 1.
        height: Number of rows, at least 1; must equal ``len(pixels)``.

    Returns:
        The PNG file contents.

    Raises:
        ValueError: When a size is below 1, the row count differs from ``height`` or a row has
            the wrong length.

    Example:
        >>> encode_png_rgb([b"\\xff\\x00\\x00"], width=1, height=1)[:8] == PNG_SIGNATURE
        True
    """
    if width < 1 or height < 1:
        msg = f"a PNG needs at least 1 x 1 pixels, got {width} x {height}"
        raise ValueError(msg)
    if len(pixels) != height:
        msg = f"expected {height} rows, got {len(pixels)}"
        raise ValueError(msg)
    if any(len(row) != width * _BYTES_PER_RGB_PIXEL for row in pixels):
        msg = f"every row must be {width * _BYTES_PER_RGB_PIXEL} bytes (3 per pixel)"
        raise ValueError(msg)
    return _png(width=width, height=height, depth=_DEPTH_8, colour=_COLOUR_RGB, scanlines=pixels)


__all__ = ["PNG_SIGNATURE", "encode_png", "encode_png_rgb"]
