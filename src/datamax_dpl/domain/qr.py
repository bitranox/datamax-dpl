"""The module matrix of a QR Code, one bitmap pixel per module, made by segno.

No DPL model in ``docs/dpl-reference.md`` has a QR Code barcode record, so the ``qr`` element is
drawn here and sent as an image: one dot per module, placed with the image record's dot width and
height set to the module size. The quiet zone is not part of the bitmap; it is space the encoder
keeps free around the symbol.

Three segno defaults are switched off on purpose, the first two unless the ``qr`` element asks:

* ``micro``: segno picks a Micro QR for short data, which many phone and handheld readers do not
  decode, so the element makes a full QR Code (21 modules or more) unless its ``micro`` is true;
  then a Micro QR (11-17 modules) where the data fits one at its error level, else a full one.
* ``boost_error``: segno raises the error level when the version has room for it, so the level a
  caller asked for would not be the level printed; the element's ``boost_error`` allows it.
* ``encoding="utf-8"``: segno tries Latin-1 and Shift JIS X 0213 first and marks neither, so the
  reader guesses. The zxing-cpp decoder read a degree sign as a half-width katakana and a run of
  a-umlauts as CJK characters. UTF-8 read back correctly in every sample, at two bytes per umlaut
  instead of one.

Example:
    >>> from datamax_dpl.domain.enums import QrError
    >>> bitmap = qr_bitmap("HELLO", error=QrError.M)
    >>> bitmap.width, bitmap.height, bitmap.get(0, 0)
    (21, 21, True)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import segno

from .errors import DatamaxDplError
from .raster import Bitmap

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .enums import QrError
    from .label import Qr

#: Modules packed into one byte of a bitmap row.
_MODULES_PER_BYTE = 8
#: The folds that turn a byte of 0 or 1 per module into one bit per module, the whole matrix at once
#: as one integer, each a shift and the bits kept per group: pairs of module bytes merge into their
#: last byte's two low bits, then fours into four bits, then eights into the eighth byte, first
#: module highest. Three integer operations a fold, where a Python step per module (or per eight)
#: was most of the packing.
_FOLDS = ((7, b"\x00\x03"), (14, b"\x00\x00\x00\x0f"), (28, b"\x00" * 7 + b"\xff"))


def matrix_bitmap(matrix: Sequence[bytes | bytearray]) -> Bitmap:
    """Return segno's module matrix (one row per bytes-like, a byte of 0 or 1 per module) as a bitmap.

    The rows are packed straight into :meth:`Bitmap.from_packed_rows`, which checks their lengths;
    no text row is built and read back, since the code made this data itself. Each row is padded
    white to whole bytes first, so no group of eight modules spans two rows.

    Example:
        >>> matrix_bitmap([bytearray([1, 0, 1]), bytearray([0, 1, 0])]).rows()
        [b'\\xa0', b'@']
    """
    width = len(matrix[0])
    pad = bytes(-width % _MODULES_PER_BYTE)
    modules = pad.join(matrix) + pad
    bits = int.from_bytes(modules, "big")
    for shift, kept in _FOLDS:
        bits = (bits | bits >> shift) & int.from_bytes(kept * (len(modules) // len(kept)), "big")
    packed = bits.to_bytes(len(modules), "big")[_MODULES_PER_BYTE - 1 :: _MODULES_PER_BYTE]
    size = len(packed) // len(matrix)
    return Bitmap.from_packed_rows([packed[at : at + size] for at in range(0, len(packed), size)], width=width)


class QrDataTooLongError(DatamaxDplError, ValueError):
    """The data fits no QR Code version (1-40) at the requested error level."""


def qr_bitmap(data: str, *, error: QrError, micro: bool = False, boost_error: bool = False) -> Bitmap:
    """Return the QR Code symbol for ``data`` as a bitmap, one pixel per module, top row first.

    Args:
        data: The text to encode; segno picks the most compact mode (numeric, alphanumeric, byte as UTF-8).
        error: The error correction level, kept as given unless ``boost_error``.
        micro: Make a Micro QR where the data fits one at ``error``; else, and when False, a full QR Code.
        boost_error: Let segno raise the level where the version has room for a higher one.

    Returns:
        The square module matrix without a quiet zone, black pixels are dark modules.

    Raises:
        QrDataTooLongError: When no QR Code version holds ``data`` at ``error``.

    Example:
        >>> from datamax_dpl.domain.enums import QrError
        >>> qr_bitmap("1", error=QrError.L).width
        21
    """
    try:
        # micro=None is segno's own choice, a Micro QR where one fits; True would refuse the rest.
        symbol = segno.make(
            data, error=error.value, micro=None if micro else False, boost_error=boost_error, encoding="utf-8"
        )
    except segno.DataOverflowError as exc:
        msg = f"QR data of {len(data)} characters does not fit any QR Code version at error level {error.value}"
        raise QrDataTooLongError(msg) from exc
    return matrix_bitmap(symbol.matrix)


def element_symbol(element: Qr) -> Bitmap:
    """Return the symbol of a ``qr`` element as :func:`qr_bitmap` draws it, with the element's switches.

    The one place the job, its overlap check and the preview take a symbol from, so all three draw
    the same one.

    Raises:
        QrDataTooLongError: As :func:`qr_bitmap`.
    """
    return qr_bitmap(element.data, error=element.error, micro=element.micro, boost_error=element.boost_error)


__all__ = ["QrDataTooLongError", "element_symbol", "matrix_bitmap", "qr_bitmap"]
