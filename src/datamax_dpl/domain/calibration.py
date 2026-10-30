"""The two calibration labels: one to measure lengths across the web, one along the feed.

A user prints both, measures the marked length with a ruler and derives the printer's
``scale_across`` and ``scale_along`` factors from it. Both labels are built in mm.

No two shapes share a dot: printers without field attributes (the Allegro 2 among them) XOR
overlapping fields, so coinciding dots cancel and a calibration print would read as a defect.
Shapes therefore touch at most, and text keeps clear of every line.

Each text block is word-wrapped in the largest bitmapped font that fits its space (3, then 2,
then 1), sized with the font's widest cell at any resolution, so it fits on every printer.
"""

from __future__ import annotations

import math
import textwrap
from typing import TYPE_CHECKING

from datamax_dpl.domain.charset import CAPITALS_ONLY_FONTS, encode_for_bitmapped_font
from datamax_dpl.domain.enums import FindingCode
from datamax_dpl.domain.label import Box, Element, Label, Line, Text
from datamax_dpl.domain.preview import widest_cell_mm
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.units import MAX_OFFSET_MM, MAX_SCALE, MIN_SCALE

if TYPE_CHECKING:
    from datamax_dpl.domain.render_context import RenderOptions

__all__ = [
    "ACROSS_INSET_MM",
    "ALONG_LENGTH_MM",
    "MIN_LABEL_MM",
    "TICK_SPACING_MM",
    "across_frame_width",
    "across_label",
    "along_label",
    "calibration_labels",
    "default_along_length",
    "longest_along_length",
    "offset_from_borders",
    "scale_from_measurement",
]

ALONG_LENGTH_MM = 200.0
"""Longest along bar: a ruler's reach. The default is the longest that one format also holds
(:func:`default_along_length`)."""

TICK_SPACING_MM = 10.0
MIN_LABEL_MM = 20.0

#: The across label's text needs this height: four lines below the bar on the narrowest label.
_ACROSS_MIN_HEIGHT_MM = 30.0

ACROSS_INSET_MM = 2.0
"""How far the across frame stands in from every label edge: a frame on the label edges loses a
side to any shift of the media, and its end can no longer be found with a ruler."""

_FRAME = 0.25
_BAR = 1.0
_ALONG_BAR = 0.5
_TICK_WIDTH = 0.25
_TICK_LENGTH = 3.0
_GAP = 0.25
_MARGIN = 1.5
_ALONG_BAR_X = 5.0
_LARGEST_FONT_FIRST = (3, 2, 1)
_LINE_GAP_MM = 0.9


def across_frame_width(width: float) -> float:
    """Return the outer width of the across label's frame, the length to measure on it.

    Args:
        width: Label width in mm.

    Returns:
        The label width less :data:`ACROSS_INSET_MM` on each side.

    Example:
        >>> across_frame_width(100)
        96.0
    """
    return width - 2 * ACROSS_INSET_MM


def across_label(*, width: float, height: float, title: str) -> Label:
    """Build the across label: a frame 2 mm in from every edge and a bar filling its inside.

    The frame's OUTER width (:func:`across_frame_width`) is the length to measure. The bar runs
    from the frame's inner left edge to its inner right edge, with a tick every 10 mm, counted
    from the frame's outer left edge, above it.

    Args:
        width: Label width in mm, at least 24 (20 inside the frame).
        height: Label height in mm, at least 30.
        title: First text line, typically the printer name and the factors in use.

    Returns:
        The label, in mm.

    Raises:
        ValueError: The width is under 24 mm, the height under 30 mm, or the text does not fit.

    Example:
        >>> label = across_label(width=100, height=55, title="LAGER")
        >>> (label.width, label.height)
        (100.0, 55.0)
    """
    _require_min("width", width, minimum=MIN_LABEL_MM + 2 * ACROSS_INSET_MM)
    _require_min("height", height, minimum=_ACROSS_MIN_HEIGHT_MM)
    frame = across_frame_width(width)
    left, right = ACROSS_INSET_MM, ACROSS_INSET_MM + frame
    bottom, top = ACROSS_INSET_MM, height - ACROSS_INSET_MM
    bar_y = height / 2 - _BAR / 2
    tick_y = bar_y + _BAR + _GAP
    text_x = left + _FRAME + _MARGIN
    text_width = frame - 2 * (_FRAME + _MARGIN)
    elements: list[Element] = [
        Box(x=left, y=bottom, width=frame, height=top - bottom, thickness=_FRAME),
        Line(x=left + _FRAME, y=bar_y, x2=right - _FRAME, y2=bar_y, thickness=_BAR),
    ]
    elements += _across_ticks(left=left, right=right, y=tick_y)
    elements += _text_block(
        title,
        x=text_x,
        top=top - _FRAME - _MARGIN,
        bottom=tick_y + _TICK_LENGTH + _GAP,
        width=text_width,
    )
    elements += _text_block(
        f"MEASURE THE FRAME OUTSIDE: {frame:g} MM ACROSS\nBORDERS: {ACROSS_INSET_MM:g} MM",
        x=text_x,
        top=bar_y - _MARGIN,
        bottom=bottom + _FRAME + _GAP,
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


#: Step of the along lengths :func:`longest_along_length` tries: a length a ruler reads at once.
_ALONG_STEP_MM = 10.0


def longest_along_length(*, width: float, title: str, options: RenderOptions, below: float) -> float | None:
    """Return the longest along bar under ``below`` mm, in whole 10 mm, that one format holds.

    Each length is rendered as the along label for ``options``, so the answer counts the rows as
    the encoder commands them at this printer's scale and offset; a length fits when its job has
    no ``format_past_max_length`` finding (other findings do not decide it).

    Args:
        width: Label width in mm, as the refused label was built.
        title: First text line, as the refused label was built.
        options: Render options of the refused job.
        below: The refused bar length in mm; only shorter lengths are tried.

    Returns:
        The length in mm, at least 20, or None when not even 20 mm fits.

    Example:
        >>> from datamax_dpl.domain.render_context import RenderOptions
        >>> options = RenderOptions(model="allegro-2", scale_along=0.9)
        >>> longest_along_length(width=100, title="X", options=options, below=200)
        180.0
    """
    step = _ALONG_STEP_MM
    length = min(ALONG_LENGTH_MM, (math.ceil(below / step) - 1) * step)
    while length >= MIN_LABEL_MM:
        result = build_job(along_label(width=width, length=length, title=title), options)
        if all(finding.code is not FindingCode.FORMAT_PAST_MAX_LENGTH for finding in result.findings):
            return length
        length -= step
    return None


def default_along_length(*, width: float, title: str, options: RenderOptions) -> float:
    """Return the along bar length to print and to measure against when none is given.

    It is the longest bar in whole 10 mm, at most :data:`ALONG_LENGTH_MM`, that one format holds
    for ``options`` (:func:`longest_along_length`), so printing it is not refused and the same
    length is the nominal one its measurement is compared with.

    Args:
        width: Label width in mm, as the along label is built.
        title: First text line, as the along label is built.
        options: Render options of the printer the label is for.

    Returns:
        The length in mm; :data:`ALONG_LENGTH_MM` when not even 20 mm fits, so the job is refused
        and its refusal says so.

    Example:
        >>> from datamax_dpl.domain.render_context import RenderOptions
        >>> default_along_length(width=100, title="X", options=RenderOptions(model="allegro-2"))
        190.0
        >>> default_along_length(width=100, title="X", options=RenderOptions(model="dmx-600"))
        200.0
    """
    fits = longest_along_length(width=width, title=title, options=options, below=ALONG_LENGTH_MM + _ALONG_STEP_MM)
    return ALONG_LENGTH_MM if fits is None else fits


def calibration_labels(
    *, width: float, height: float, title: str, along_length: float = ALONG_LENGTH_MM
) -> tuple[Label, Label]:
    """Build both calibration labels for one printer's media.

    Args:
        width: Label width in mm, at least 24.
        height: Label height in mm (the across label's height), at least 30.
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


def scale_from_measurement(current: float, *, measured: float, nominal: float) -> float:
    """Return the scale factor that makes a length print at its nominal size.

    The label was printed with ``current``, so the printer's own ratio is
    ``measured / nominal * current``; that ratio is the new factor.

    Args:
        current: The scale factor the calibration label was printed with.
        measured: The length measured on paper, in mm.
        nominal: The length the label asked for, in mm.

    Returns:
        The new factor, rounded to 4 places.

    Raises:
        ValueError: A length is not positive, or the factor falls outside 0.5-1.5 (a mismeasured
            or mistyped length, more likely than a printer that far off).

    Example:
        >>> scale_from_measurement(0.977, measured=199, nominal=200)
        0.9721
    """
    if measured <= 0 or nominal <= 0:
        raise ValueError(f"measured {measured:g} mm and nominal {nominal:g} mm must both be over 0")
    scale = round(current * measured / nominal, 4)
    if not MIN_SCALE <= scale <= MAX_SCALE:
        raise ValueError(
            f"measured {measured:g} mm of {nominal:g} mm gives a scale of {scale:g}, outside "
            f"{MIN_SCALE:g}-{MAX_SCALE:g}: check the measurement"
        )
    return scale


def offset_from_borders(current: float, *, low: float, high: float) -> float:
    """Return the position offset that centres the across label's frame between two borders.

    The frame stands the same distance from both opposite edges when the print sits where it
    should, so the print has to move half the difference toward the wider border.

    Args:
        current: The offset the label was printed with, in mm.
        low: Border measured on the left (across) or at the bottom (along), in mm.
        high: Border measured on the right (across) or at the top (along), in mm.

    Returns:
        The new offset in mm, rounded to 0.01; positive moves right or up.

    Raises:
        ValueError: A border is negative, or the offset would pass 10 mm either way.

    Example:
        >>> offset_from_borders(0.0, low=2.3, high=1.8)
        -0.25
    """
    if low < 0 or high < 0:
        raise ValueError(f"borders of {low:g} mm and {high:g} mm must both be 0 mm or more")
    offset = round(current + (high - low) / 2, 2)
    if abs(offset) > MAX_OFFSET_MM:
        raise ValueError(
            f"borders of {low:g} mm and {high:g} mm give an offset of {offset:g} mm, past "
            f"{MAX_OFFSET_MM:g} mm: check the measurement"
        )
    return offset


def _require_min(name: str, value: float, *, minimum: float = MIN_LABEL_MM) -> None:
    if value < minimum:
        raise ValueError(f"{name} {value:g} mm is under {minimum:g} mm, too small to calibrate on")


def _across_ticks(*, left: float, right: float, y: float) -> list[Line]:
    # Counted from the frame's outer left edge; a tick stops short of the frame's inner right
    # edge so the two never share a dot.
    last = right - _FRAME - _GAP - _TICK_WIDTH - left
    count = int(last // TICK_SPACING_MM)
    positions = [left + step * TICK_SPACING_MM for step in range(1, count + 1)]
    return [Line(x=x, y=y, x2=x, y2=y + _TICK_LENGTH, thickness=_TICK_WIDTH) for x in positions]


def _along_ticks(length: float, *, tick_x: float, bottom: float) -> list[Line]:
    count = int(length // TICK_SPACING_MM)
    rows = [bottom + step * TICK_SPACING_MM for step in range(count + 1)]
    return [Line(x=tick_x, y=y, x2=tick_x + _TICK_LENGTH, y2=y, thickness=_TICK_WIDTH) for y in rows]


def _text_block(text: str, *, x: float, top: float, bottom: float, width: float) -> list[Text]:
    """Stack ``text`` downward from ``top`` in the largest font that fits above ``bottom``, else refuse.

    Words are kept whole; only font 1, the last resort, breaks a word too long for its line.
    """
    attempts = [(font, False) for font in _LARGEST_FONT_FIRST] + [(_LARGEST_FONT_FIRST[-1], True)]
    for font, break_words in attempts:
        if not _prints_as_given(text, font):
            continue
        advance, height = widest_cell_mm(font)
        pitch = height + _LINE_GAP_MM
        lines = _wrap(text, int(width // advance), break_words=break_words)
        # No gap is needed below the last line.
        if lines and top - height - (len(lines) - 1) * pitch >= bottom:
            return [Text(x=x, y=top - height - row * pitch, text=line, font=font) for row, line in enumerate(lines)]
    raise ValueError(f"the label is too small for its text {text!r}")


def _prints_as_given(text: str, font: int) -> bool:
    # A capitals-only font would print a lower-case title in capitals: keep the caller's text.
    if font in CAPITALS_ONLY_FONTS and text != text.upper():
        return False
    return not encode_for_bitmapped_font(text.replace("\n", ""), font=font).unmappable


def _wrap(text: str, columns: int, *, break_words: bool) -> list[str]:
    """Wrap ``text`` to ``columns``; without ``break_words``, an empty list when a word is too long."""
    if columns < 1:
        return []
    parts = text.split("\n")
    lines = [wrapped for part in parts for wrapped in textwrap.wrap(part, columns, break_long_words=break_words)]
    return lines if all(len(line) <= columns for line in lines) else []
