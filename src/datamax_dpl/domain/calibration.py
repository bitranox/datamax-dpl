"""The two calibration labels: one to measure lengths across the web, one along the feed.

A user prints both, measures the marked length with a ruler and derives the printer's
``scale_across`` and ``scale_along`` factors from it. Both labels are built in mm.

No two shapes share a dot: printers without field attributes (the Allegro 2 among them) XOR
overlapping fields, so coinciding dots cancel and a calibration print would read as a defect.
Shapes therefore touch at most, and text keeps clear of every line.

Text is word-wrapped with the font 1 cell of a 203 dpi head (the widest any profile prints, as
bitmap fonts are sized in dots), so it fits at every resolution.
"""

from __future__ import annotations

import textwrap

from datamax_dpl.domain.label import Box, Element, Label, Line, Text

__all__ = [
    "ALONG_LENGTH_MM",
    "MIN_LABEL_MM",
    "TICK_SPACING_MM",
    "across_label",
    "along_label",
    "calibration_labels",
]

ALONG_LENGTH_MM = 200.0
"""Default and maximum length of the along bar: the Allegro 2 cut a format off at ~201.5 mm."""

TICK_SPACING_MM = 10.0
MIN_LABEL_MM = 20.0

_FRAME = 0.25
_BAR = 1.0
_ALONG_BAR = 0.5
_TICK_WIDTH = 0.25
_TICK_LENGTH = 3.0
_GAP = 0.25
_MARGIN = 1.5
_ALONG_BAR_X = 5.0
_CHAR_MM = 1.15
_LINE_MM = 2.5
_FONT = 1


def across_label(*, width: float, height: float, title: str) -> Label:
    """Build the across label: a frame on the label edges and a bar filling its inside.

    The frame's OUTER width is the length to measure. The bar runs from the frame's inner left
    edge to its inner right edge, with a tick every 10 mm above it.

    Args:
        width: Label width in mm, at least 20.
        height: Label height in mm, at least 20.
        title: First text line, typically the printer name and the factors in use.

    Returns:
        The label, in mm.

    Raises:
        ValueError: A side is under 20 mm, or the text does not fit.

    Example:
        >>> label = across_label(width=100, height=55, title="LAGER")
        >>> (label.width, label.height)
        (100.0, 55.0)
    """
    _require_min("width", width)
    _require_min("height", height)
    bar_y = height / 2 - _BAR / 2
    tick_y = bar_y + _BAR + _GAP
    text_width = width - 2 * (_FRAME + _MARGIN)
    elements: list[Element] = [
        Box(x=0, y=0, width=width, height=height, thickness=_FRAME),
        Line(x=_FRAME, y=bar_y, x2=width - _FRAME, y2=bar_y, thickness=_BAR),
    ]
    elements += _across_ticks(width, tick_y)
    elements += _text_block(
        title,
        x=_FRAME + _MARGIN,
        top=height - _FRAME - _MARGIN,
        bottom=tick_y + _TICK_LENGTH + _GAP,
        width=text_width,
    )
    elements += _text_block(
        f"MEASURE THE FRAME OUTSIDE: {width:g} MM ACROSS",
        x=_FRAME + _MARGIN,
        top=bar_y - _MARGIN,
        bottom=_FRAME + _GAP,
        width=text_width,
    )
    return Label(width=width, height=height, elements=elements)


def along_label(*, width: float, length: float = ALONG_LENGTH_MM, title: str) -> Label:
    """Build the along label: a bar of ``length`` along the feed with a tick every 10 mm.

    The label is ``length + 5`` mm long, so on gap media it runs over several labels; measure
    the bar on the liner.

    Args:
        width: Label width in mm, at least 20.
        length: Bar length in mm, at most 200.
        title: First text line, typically the printer name and the factors in use.

    Returns:
        The label, in mm.

    Raises:
        ValueError: The width is under 20 mm, the length is not between 20 and 200 mm, or the
            text does not fit.

    Example:
        >>> along_label(width=100, length=60, title="LAGER").height
        65.0
    """
    _require_min("width", width)
    _require_min("length", length)
    if length > ALONG_LENGTH_MM:
        raise ValueError(f"along length {length:g} mm is over {ALONG_LENGTH_MM:g} mm, the longest one format prints")
    bottom = 2.0
    top = bottom + length
    tick_x = _ALONG_BAR_X + _ALONG_BAR + _GAP
    text_x = tick_x + _TICK_LENGTH + 2 * _MARGIN
    elements: list[Element] = [
        Line(x=_ALONG_BAR_X, y=bottom, x2=_ALONG_BAR_X, y2=top, thickness=_ALONG_BAR),
    ]
    elements += _along_ticks(length, tick_x=tick_x, bottom=bottom)
    text = f"{title}\nMEASURE THE LINE: {length:g} MM ALONG THE FEED"
    elements += _text_block(text, x=text_x, top=top, bottom=bottom, width=width - text_x - _MARGIN)
    return Label(width=width, height=length + 5, elements=elements)


def calibration_labels(
    *, width: float, height: float, title: str, along_length: float = ALONG_LENGTH_MM
) -> tuple[Label, Label]:
    """Build both calibration labels for one printer's media.

    Args:
        width: Label width in mm.
        height: Label height in mm (the across label's height).
        title: First text line of both labels.
        along_length: Length of the along bar in mm.

    Returns:
        The across label and the along label.

    Raises:
        ValueError: As :func:`across_label` and :func:`along_label`.

    Example:
        >>> across, along = calibration_labels(width=100, height=55, title="LAGER")
        >>> (across.height, along.height)
        (55.0, 205.0)
    """
    return (
        across_label(width=width, height=height, title=title),
        along_label(width=width, length=along_length, title=title),
    )


def _require_min(name: str, value: float) -> None:
    if value < MIN_LABEL_MM:
        raise ValueError(f"{name} {value:g} mm is under {MIN_LABEL_MM:g} mm, too small to calibrate on")


def _across_ticks(width: float, y: float) -> list[Line]:
    # A tick stops short of the frame's inner right edge so the two never share a dot.
    last = width - _FRAME - _GAP - _TICK_WIDTH
    count = int(last // TICK_SPACING_MM)
    positions = [step * TICK_SPACING_MM for step in range(1, count + 1)]
    return [Line(x=x, y=y, x2=x, y2=y + _TICK_LENGTH, thickness=_TICK_WIDTH) for x in positions]


def _along_ticks(length: float, *, tick_x: float, bottom: float) -> list[Line]:
    count = int(length // TICK_SPACING_MM)
    rows = [bottom + step * TICK_SPACING_MM for step in range(count + 1)]
    return [Line(x=tick_x, y=y, x2=tick_x + _TICK_LENGTH, y2=y, thickness=_TICK_WIDTH) for y in rows]


def _text_block(text: str, *, x: float, top: float, bottom: float, width: float) -> list[Text]:
    """Wrap ``text`` to ``width`` and stack its lines downward from ``top``, refusing an overflow."""
    columns = int(width // _CHAR_MM)
    lines = [wrapped for part in text.split("\n") for wrapped in textwrap.wrap(part, columns)] if columns else []
    if not lines or top - len(lines) * _LINE_MM < bottom:
        raise ValueError(f"the label is too small for its text {text!r}")
    return [Text(x=x, y=top - (row + 1) * _LINE_MM, text=line, font=_FONT) for row, line in enumerate(lines)]
