"""The Datamax 7-bit ASCII image format sent after ``<STX>IAAF<name><CR>``.

The manual (``docs/dpl-reference.md`` section 5, "Image download") defines three records, each
ending with ``<CR>``:

* ``80nn<hex bytes>`` - one image row: ``nn`` is the row's byte count as two hex digits, followed
  by that many bytes as hex pairs (pixels packed MSB-first, a set bit is a black dot);
* ``0000FFnn`` - repeat the previous row ``nn`` more times;
* ``FFFF`` - end of the image.

Two readings here come from a printer, not from the manual (measured on an Allegro 2, firmware
CA02 03.04):

* The manual does not say which end of the image the first row record is. The printer draws the
  first record at the BOTTOM, so :func:`encode_image` sends the bottom row first and
  :func:`decode_image` reads the first record as the bottom row.
* :func:`encode_image` never emits a repeat record. On that printer every image holding repeat
  records came out wrong - one row short per repeat record, or empty, or garbled - while the same
  images sent one row record per row printed right. Sending every row costs bytes (about 10 KB
  for a 100 x 15 mm image at 8 dots/mm); a wrong label costs more. :func:`decode_image` still
  reads repeat records, since DPL written elsewhere may hold them.

Example:
    >>> from datamax_dpl.domain.raster import Bitmap
    >>> encode_image(Bitmap.from_rows(["#.......", "#.......", "........"]))
    b'800100\\r800180\\r800180\\rFFFF\\r'
"""

from __future__ import annotations

import re

from .raster import Bitmap

#: Start-of-text byte that opens every DPL system-level command.
STX = b"\x02"
#: Record terminator.
CR = b"\r"
#: Command that starts a 7-bit ASCII image download into the default module.
DOWNLOAD_COMMAND = STX + b"IAAF"
#: Prefix of a row record.
ROW_PREFIX = b"80"
#: Prefix of a repeat-previous-row record.
REPEAT_PREFIX = b"0000FF"
#: The record that ends an image.
END_RECORD = b"FFFF"
#: Largest row byte count and largest repeat count: both are written as two hex digits.
MAX_COUNT = 0xFF

_HEX_PAIRS = re.compile(rb"(?:[0-9A-Fa-f]{2})*")
_COUNT_DIGITS = 2
_REPEAT_RECORD_LENGTH = len(REPEAT_PREFIX) + _COUNT_DIGITS
_ROW_HEADER_LENGTH = len(ROW_PREFIX) + _COUNT_DIGITS
_BITS_PER_BYTE = 8
#: The end record plus the empty piece after its final carriage return.
_TAIL_RECORDS = 2
_NAME_PATTERN = re.compile(r"[A-Za-z0-9_]{1,8}")


def _count(value: int) -> bytes:
    """Return ``value`` as two upper-case hex digits."""
    return b"%02X" % value


def _row_record(row: bytes) -> bytes:
    """Return the row record for one packed row."""
    return ROW_PREFIX + _count(len(row)) + row.hex().upper().encode("ascii") + CR


def encode_image(bitmap: Bitmap) -> bytes:
    """Encode a bitmap as Datamax 7-bit ASCII image records, bottom row first.

    Every row gets its own row record; no repeat record is emitted (see the module docstring).

    Args:
        bitmap: The image; at most 255 bytes (2040 pixels) wide.

    Returns:
        The row and repeat records followed by the ``FFFF`` end record.

    Raises:
        ValueError: When a packed row needs more than 255 bytes.

    Example:
        >>> encode_image(Bitmap.from_rows(["#..#"]))
        b'800190\\rFFFF\\r'
    """
    rows = bitmap.rows()
    if len(rows[0]) > MAX_COUNT:
        msg = (
            f"an image row may be at most {MAX_COUNT} bytes ({MAX_COUNT * _BITS_PER_BYTE} pixels), "
            f"got {bitmap.width} pixels"
        )
        raise ValueError(msg)
    return b"".join(_row_record(row) for row in reversed(rows)) + END_RECORD + CR


def _parse_count(digits: bytes, *, what: str) -> int:
    """Parse two hex digits that must name a count from 1 to 255."""
    if len(digits) != _COUNT_DIGITS or not _HEX_PAIRS.fullmatch(digits):
        msg = f"{what} must be two hex digits, got {digits!r}"
        raise ValueError(msg)
    value = int(digits, 16)
    if value == 0:
        msg = f"{what} must be at least 1"
        raise ValueError(msg)
    return value


def _parse_row(record: bytes) -> bytes:
    """Parse a row record into its packed row bytes."""
    size = _parse_count(record[len(ROW_PREFIX) : _ROW_HEADER_LENGTH], what="row byte count")
    body = record[_ROW_HEADER_LENGTH:]
    if len(body) != 2 * size or not _HEX_PAIRS.fullmatch(body):
        msg = f"row record must hold {size} bytes as hex pairs: {record!r}"
        raise ValueError(msg)
    return bytes.fromhex(body.decode("ascii"))


def _parse_record(record: bytes, *, previous: bytes | None) -> list[bytes]:
    """Return the rows one record adds to the image."""
    if record.startswith(REPEAT_PREFIX) and len(record) == _REPEAT_RECORD_LENGTH:
        if previous is None:
            msg = "a repeat record needs a row before it"
            raise ValueError(msg)
        return [previous] * _parse_count(record[len(REPEAT_PREFIX) :], what="repeat count")
    if record.startswith(ROW_PREFIX):
        return [_parse_row(record)]
    msg = f"unknown image record {record!r}"
    raise ValueError(msg)


def _split_records(data: bytes) -> list[bytes]:
    """Split the data into records and check it ends with the ``FFFF`` record."""
    records = data.split(CR)
    if records[-1] != b"":
        msg = "image data must end with a carriage return"
        raise ValueError(msg)
    if len(records) < _TAIL_RECORDS or records[-_TAIL_RECORDS] != END_RECORD:
        msg = "image data must end with the FFFF record"
        raise ValueError(msg)
    return records[:-_TAIL_RECORDS]


def decode_image(data: bytes) -> Bitmap:
    """Decode Datamax 7-bit ASCII image records back into a bitmap.

    The decoded width is the row byte count times 8, since the format does not carry the exact
    pixel width; padding pixels come back white when the encoder padded with white.

    Args:
        data: Row and repeat records ending with ``FFFF<CR>``, without the ``<STX>IAAF`` header.

    Returns:
        The image; the first row record is its bottom row.

    Raises:
        ValueError: On an unknown or malformed record, a repeat before any row, rows of different
            byte counts, no rows at all, or a missing end record.

    Example:
        >>> decode_image(b"800190\\r0000FF02\\rFFFF\\r").height
        3
    """
    rows: list[bytes] = []
    for record in _split_records(data):
        rows.extend(_parse_record(record, previous=rows[-1] if rows else None))
    if not rows:
        msg = "image data holds no rows"
        raise ValueError(msg)
    if len({len(row) for row in rows}) != 1:
        msg = "every image row must have the same byte count"
        raise ValueError(msg)
    return Bitmap.from_packed_rows(rows[::-1], width=len(rows[0]) * _BITS_PER_BYTE)


def download_command(name: str, bitmap: Bitmap) -> bytes:
    """Return the complete command that stores ``bitmap`` on the printer under ``name``.

    Args:
        name: Image name, 1-8 characters of letters, digits and underscore.
        bitmap: The image.

    Returns:
        ``<STX>IAAF<name><CR>`` followed by :func:`encode_image` of the bitmap.

    Raises:
        ValueError: When the name is not 1-8 letters, digits or underscores, or the image is too
            wide.

    Example:
        >>> download_command("DOT", Bitmap(1, 1))
        b'\\x02IAAFDOT\\r800100\\rFFFF\\r'
    """
    if not _NAME_PATTERN.fullmatch(name):
        msg = f"image name must be 1-8 letters, digits or underscores, got {name!r}"
        raise ValueError(msg)
    return DOWNLOAD_COMMAND + name.encode("ascii") + CR + encode_image(bitmap)


__all__ = [
    "CR",
    "DOWNLOAD_COMMAND",
    "END_RECORD",
    "MAX_COUNT",
    "REPEAT_PREFIX",
    "ROW_PREFIX",
    "STX",
    "decode_image",
    "download_command",
    "encode_image",
]
