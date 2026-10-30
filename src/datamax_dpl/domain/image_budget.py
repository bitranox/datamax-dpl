"""The printer's image memory: what a job's image downloads cost, and how a job is fitted into it.

A downloaded image is stored in a memory module of the printer, beside the graphics it already
holds, and a module has a fixed size. A model may carry a budget for the bytes one job's downloads
may hold (``CalibrationDefaults.image_memory_bytes``, overridden by ``[printer]
image_memory_bytes``). Past it, under ``degrade``, every image the job draws or downloads from a
bitmap is halved once and placed at double magnification; a QR Code never is, since its image
holds one dot per module and halving it would merge modules.

What is counted (:func:`image_data_bytes`) is the dot data the download's row records carry: each
row record's byte count, and a repeat record's row once per repeat. The printer stores the bitmap,
not the hex text that carries it, which is twice as long plus framing. What the printer adds per
stored image (its directory entry) is not known and not counted: the margin in a model's default
budget is meant to cover it. None of this is verified on paper yet.
"""

from __future__ import annotations

from dataclasses import dataclass

from .image_records import decode_image
from .raster import Bitmap
from .records import MAX_IMAGE_MAGNIFICATION

_BITS_PER_BYTE = 8
#: How many times smaller a halved image is on each axis, and how much larger it is printed.
HALVING_FACTOR = 2


@dataclass(frozen=True)
class ImageBudget:
    """The bytes one job's image downloads may hold, and where that figure comes from.

    Attributes:
        limit: The budget in bytes of dot data (see :func:`image_data_bytes`).
        source: ``configured``, or ``<model> default`` for a model's measured budget.
        provenance: How a model's budget was found; empty for a configured one.

    Example:
        >>> ImageBudget(limit=500, source="configured").describe()
        'the configured image memory budget is 500 bytes'
    """

    limit: int
    source: str
    provenance: str = ""

    def describe(self) -> str:
        """Name the budget and where it comes from, for a finding's message."""
        text = f"the {self.source} image memory budget is {self.limit} bytes"
        return f"{text} ({self.provenance})" if self.provenance else text


def image_data_bytes(records: bytes) -> int:
    """Return the bytes of dot data a Datamax 7-bit ASCII image holds, as the printer stores it.

    Args:
        records: The row and repeat records ending with ``FFFF<CR>``, without the ``<STX>I``
            header line.

    Returns:
        Rows times bytes per row: each row record's byte count, and a repeated row once per repeat.

    Raises:
        ValueError: The records are malformed (see :func:`~datamax_dpl.domain.image_records.decode_image`).

    Example:
        >>> image_data_bytes(b"800190\\r0000FF02\\rFFFF\\r")
        3
    """
    bitmap = decode_image(records)
    return bitmap.height * (bitmap.width // _BITS_PER_BYTE)


def halved_magnification(magnification: int) -> int | None:
    """Return the magnification a halved image is placed at, or None when it passes the largest.

    Example:
        >>> halved_magnification(4), halved_magnification(5)
        (8, None)
    """
    doubled = magnification * HALVING_FACTOR
    return doubled if doubled <= MAX_IMAGE_MAGNIFICATION else None


def _bit_rows(bitmap: Bitmap) -> list[str]:
    """Return each row as ``1``/``0`` text, top row first, exactly ``bitmap.width`` long."""
    pad = -bitmap.width % _BITS_PER_BYTE
    return [format(int.from_bytes(row, "big") >> pad, f"0{bitmap.width}b") for row in bitmap.rows()]


def _halve_row(bits: str) -> str:
    """OR each pair of neighbouring pixels of an even-length row into one."""
    width = len(bits) // HALVING_FACTOR
    return format(int(bits[0::2], 2) | int(bits[1::2], 2), f"0{width}b")


def halve(bitmap: Bitmap) -> Bitmap:
    """Return ``bitmap`` at half resolution: each 2x2 block becomes one dot, black if any was.

    A one-dot line therefore survives, one dot wide. The blocks start at the lower-left corner,
    where the printer anchors an image: an odd height gains a white row on top and an odd width a
    white column on the right, so placed at double magnification every black dot of the original
    is covered by the block that holds it.

    Example:
        >>> halve(Bitmap.from_rows(["...", "...", "#.."]))
        Bitmap(width=2, height=2, black=1)
    """
    rows = _bit_rows(bitmap)
    if len(rows) % HALVING_FACTOR:
        rows.insert(0, "0" * bitmap.width)
    width = bitmap.width + bitmap.width % HALVING_FACTOR
    rows = [row.ljust(width, "0") for row in rows]
    pairs = zip(rows[0::2], rows[1::2], strict=True)
    merged = [format(int(top, 2) | int(bottom, 2), f"0{width}b") for top, bottom in pairs]
    return Bitmap.from_rows([_halve_row(row) for row in merged])


def magnify(bitmap: Bitmap, factor: int) -> Bitmap:
    """Return ``bitmap`` with each dot drawn as a ``factor`` x ``factor`` square, as the printer prints it.

    Raises:
        ValueError: ``factor`` is below 1.

    Example:
        >>> magnify(Bitmap.from_rows(["#."]), 2)
        Bitmap(width=4, height=2, black=4)
    """
    if factor < 1:
        msg = f"magnification must be at least 1, got {factor}"
        raise ValueError(msg)
    widen = {ord("0"): "0" * factor, ord("1"): "1" * factor}
    return Bitmap.from_rows([row.translate(widen) for row in _bit_rows(bitmap) for _ in range(factor)])


__all__ = ["HALVING_FACTOR", "ImageBudget", "halve", "halved_magnification", "image_data_bytes", "magnify"]
