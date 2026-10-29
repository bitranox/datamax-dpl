"""The module matrix of a QR Code, one bitmap pixel per module, made by segno.

No DPL model in ``docs/dpl-reference.md`` has a QR Code barcode record, so the ``qr`` element is
drawn here and sent as an image: one dot per module, placed with the image record's dot width and
height set to the module size. The quiet zone is not part of the bitmap; it is space the encoder
keeps free around the symbol.

Two segno defaults are switched off on purpose:

* ``micro=False``: segno picks a Micro QR for short data, which many phone and handheld readers
  do not decode; the element always makes a full QR Code (21 modules or more).
* ``boost_error=False``: segno raises the error level when the version has room for it, so the
  level a caller asked for would not be the level printed.

Example:
    >>> from datamax_dpl.domain.enums import QrError
    >>> bitmap = qr_bitmap("HELLO", error=QrError.M)
    >>> bitmap.width, bitmap.height, bitmap.get(0, 0)
    (21, 21, True)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import segno

from .raster import Bitmap

if TYPE_CHECKING:
    from .enums import QrError


class QrDataTooLongError(ValueError):
    """The data fits no QR Code version (1-40) at the requested error level."""


def qr_bitmap(data: str, *, error: QrError) -> Bitmap:
    """Return the QR Code symbol for ``data`` as a bitmap, one pixel per module, top row first.

    Args:
        data: The text to encode; segno picks the most compact mode (numeric, alphanumeric, byte).
        error: The error correction level, kept as given.

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
        symbol = segno.make(data, error=error.value, micro=False, boost_error=False)
    except segno.DataOverflowError as exc:
        msg = f"QR data of {len(data)} characters does not fit any QR Code version at error level {error.value}"
        raise QrDataTooLongError(msg) from exc
    return Bitmap.from_rows(["".join("#" if module else "." for module in row) for row in symbol.matrix])


__all__ = ["QrDataTooLongError", "qr_bitmap"]
