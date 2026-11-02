"""Positions and sizes on a grid of printer dots, as the raster graphics and the preview use them.

One bitmap pixel is one printer dot, and row 0 is the TOP row (:class:`~datamax_dpl.domain.raster.Bitmap`
and the preview both have their origin at the top-left corner). These are named tuples because
the drawing code unpacks them (``fill_rect(*rect)``, ``x, y = point``); a DPL record's row and
column, counted up from the label's bottom edge, is a different thing
(:class:`~datamax_dpl.domain.records.RecordPosition`).
"""

from __future__ import annotations

from typing import NamedTuple


class DotPoint(NamedTuple):
    """One pixel: column ``x`` and row ``y``, row 0 at the top.

    Example:
        >>> DotPoint(3, 4).y
        4
    """

    x: int
    y: int


class Rect(NamedTuple):
    """A rectangle of pixels: its top-left pixel and its size.

    Example:
        >>> Rect(left=1, top=2, width=3, height=4).width
        3
    """

    left: int
    top: int
    width: int
    height: int


class Size(NamedTuple):
    """Width and height in dots.

    Example:
        >>> Size(width=8, height=5).height
        5
    """

    width: int
    height: int


class Span(NamedTuple):
    """A run of dots along one direction: where it starts and how many dots it takes.

    Example:
        >>> Span(start=10, length=4).length
        4
    """

    start: int
    length: int


__all__ = ["DotPoint", "Rect", "Size", "Span"]
