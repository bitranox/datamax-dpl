"""A minimal PNG writer built on the standard library (``zlib`` and ``struct``) only.

It writes one kind of image, for the label preview: 1-bit grayscale from a
:class:`~datamax_dpl.domain.raster.Bitmap`. Every scanline uses filter type 0 (none) and there is
no interlacing, which keeps the encoder small; the label images are mostly white, so zlib alone
compresses them well.
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
_DEPTH_1 = 1
_FILTER_NONE = b"\x00"
_COMPRESSION_LEVEL = 9
_BITS_PER_BYTE = 8
#: Bitmap black (1) becomes PNG black (0) and white the other way round, each widened by scale.
_INVERT = str.maketrans("01", "10")


def _chunk(kind: bytes, data: bytes) -> bytes:
    """Return one PNG chunk: length, type, data and the CRC over type plus data."""
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _png(*, width: int, height: int, scanlines: Sequence[bytes]) -> bytes:
    """Assemble a non-interlaced 1-bit grayscale PNG from unfiltered scanlines."""
    header = struct.pack(">IIBBBBB", width, height, _DEPTH_1, _COLOUR_GRAY, 0, 0, 0)
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
    return _png(width=bitmap.width * scale, height=bitmap.height * scale, scanlines=scanlines)


__all__ = ["PNG_SIGNATURE", "encode_png"]
