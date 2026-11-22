"""Encode text fields: bitmapped fonts 0-9 and scalable fonts, with their fallbacks."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .charset import (
    CAPITALS_ONLY_FONTS,
    EncodedText,
    encode_for_bitmapped_font,
    encode_for_symbol_set,
    resolved_symbol_set,
)
from .encode_common import (
    STEPS_PER_UNIT,
    attribute_lines,
    attribute_reset,
    check_field_length,
    checked_column,
    column_of,
    counter_lines,
    report_past_edge,
    row_of,
)
from .enums import FindingCode, TextAlign, Unit
from .label import FONT_9, ScalableText, Text
from .placeholders import placeholders_in
from .profiles import OLD_SERIES_POINTS, Feature
from .records import Header, line
from .text_metrics import font9_field_points, font9_points, label_edge_column, measured, text_column, units_left_of_x
from .units import MM_PER_INCH, Axis

if TYPE_CHECKING:
    from .render_context import RenderContext

_MIN_POINTS = 4
_MAX_POINTS = 72
#: The step a move named in mm takes: the tenth of a millimetre the message states.
_MOVE_STEP_MM = 0.1


def _size_code(points: int, ctx: RenderContext) -> str | None:
    """Return the font 9 size code for ``points`` on this model, or None when it has no such size."""
    if points in ctx.profile.a_series_points:
        return f"A{points:02d}"
    if points in ctx.profile.old_series_points:
        return f"{OLD_SERIES_POINTS.index(points):03d}"
    return None


def font9_size(points: int, *, index: int, ctx: RenderContext) -> str:
    """Return the size code for font 9 at ``points``, substituting the nearest size the model has."""
    code = _size_code(points, ctx)
    if code is not None:
        return code
    nearest = font9_points(points, ctx.profile)
    ctx.findings.deviate(
        FindingCode.FONT_SIZE_SUBSTITUTED,
        f"{ctx.profile.name} has no {points} pt size of font 9",
        fallback=f"printed at {nearest} pt",
        element=index,
    )
    nearest_code = _size_code(nearest, ctx)
    return nearest_code if nearest_code is not None else "000"


def _report_unmappable(encoded: EncodedText, *, index: int, ctx: RenderContext, font: str) -> None:
    if not encoded.unmappable:
        return
    shown = " ".join(f"U+{ord(c):04X}" for c in encoded.unmappable)
    ctx.findings.deviate(
        FindingCode.CHARACTERS_UNMAPPABLE,
        f"{font} cannot print {shown}",
        fallback="each replaced by '?'",
        element=index,
    )


def _text_column(element: Text, *, index: int, ctx: RenderContext) -> int:
    """Return the record column of a text field: ``x``'s, moved back by its alignment and its font 9 shift.

    ``x`` itself is checked against the printable width (``outside_print_width``), so a centred or
    right-aligned line whose middle or end is past it is refused like a field starting there. Where
    the line starts is measured from ``x`` as the offset moves it, before anything keeps it on the
    printhead, so one finding gives the true distance: a line whose ink would start left of the
    label's left edge is refused (``text_past_left_edge``); one on the label but with its record left
    of the printhead's first column, where a negative offset across puts the label's edge, is
    reported as a left-aligned field the offset moves there. Without such an offset only font 9's
    print shift puts a record there (:func:`~.text_metrics.font9_print_shift_dots`): the record
    cannot be written left of the first dot, so a centred or right-aligned line is refused too,
    while a left-aligned one is written at column 0 and prints up to 0.25 mm right of ``x``, with
    no finding. Each is written at
    :func:`~.text_metrics.text_column`, so the job still renders for the findings. A field still
    holding a placeholder in a label rendered without data is checked at ``x`` only: it prints its
    template, and each record is measured once filled.
    """
    shift = units_left_of_x(element, ctx)
    if not shift or (ctx.template and placeholders_in(element.text)):
        # x is where the line starts, or its width is not known yet: x itself is checked.
        column = column_of(element.x, index=index, ctx=ctx)
    else:
        shifted = ctx.shifted(element.x, axis=Axis.ACROSS)
        column = checked_column(max(shifted, 0.0), index=index, ctx=ctx)
        _report_line_start(element, back=shift, index=index, ctx=ctx)
    # text_column places the line as the preview and the overlap and edge checks do; min() keeps a
    # column past four digits at the cap the check above refused and returned.
    return min(text_column(element, ctx), column)


def _report_line_start(element: Text, *, back: int, index: int, ctx: RenderContext) -> None:
    """Record a line whose record starts ``back`` units left of ``x``'s column, if it starts off the label or printhead.

    The label's edge is judged by where the ink starts, which font 9 prints right of the record;
    the printhead by the record, which no column left of the first dot can hold.
    """
    edge = label_edge_column(ctx)
    printed = measured(element, ctx).print_shift
    on_label = _first_start_on_label(edge, printed=printed, ctx=ctx)
    start = _line_start(element.x, back=back, ctx=ctx)
    if start < on_label:
        message = _past_edge_message(element, back=back, edge=edge, on_label=on_label, ctx=ctx)
        ctx.findings.refuse(FindingCode.TEXT_PAST_LEFT_EDGE, message, element=index)
    elif start < 0 and edge < 0:
        # One step of the precision report_past_edge states, so the move named is the smallest it can state.
        move = _clearing_move(element.x, back=back, target=0, step=1 / STEPS_PER_UNIT[ctx.unit], ctx=ctx)
        report_past_edge(move, Axis.ACROSS, index=index, ctx=ctx)
    elif start < 0 and element.align != TextAlign.LEFT:
        message = _past_printhead_message(element, back=back, printed=printed, ctx=ctx)
        ctx.findings.refuse(FindingCode.TEXT_PAST_LEFT_EDGE, message, element=index)
    # A left-aligned line there (font 9 at 30 pt and up on an Allegro 2, x below about one dot) is
    # written at column 0 without a finding and prints up to 0.25 mm right of x. A user decision
    # keeps it silent, so a label laid out at x 0 prints as it did before the print shift existed;
    # the documented exception to reporting every fallback.


def _first_start_on_label(edge: int, *, printed: int, ctx: RenderContext) -> int:
    """Return the first record column whose ink, printed ``printed`` dots right of it, starts on the label.

    The label's edge column itself without a print shift; with one, the first column whose printed
    dots plus the shift reach the edge's, found through the same rounding of units to dots.
    """
    if not printed:
        return edge
    goal = ctx.printed_dots(edge)
    start = edge - math.ceil(printed / ctx.dots_per_unit) - 1
    while ctx.printed_dots(start) + printed < goal:
        start += 1
    return start


def _past_edge_message(element: Text, *, back: int, edge: int, on_label: int, ctx: RenderContext) -> str:
    """Say how far ``x`` must move for the line to start on the label, in mm.

    The move goes to the later of the label's edge and the printhead's first column: with a negative
    offset across the edge lies left of that column, and a line moved only onto the label would
    still be refused (``offset_past_label_edge``), so the message names both moves.
    """
    step = _label_length_of_mm(_MOVE_STEP_MM, ctx)
    line = f"the {TextAlign(element.align).value}-aligned line would start left of the label's left edge"
    if edge >= 0:
        move = _mm(_clearing_move(element.x, back=back, target=max(on_label, 0), step=step, ctx=ctx), ctx)
        return f"{line}; move x right by {move} mm or shorten the text"
    onto_label = _mm(_clearing_move(element.x, back=back, target=on_label, step=step, ctx=ctx), ctx)
    onto_printhead = _mm(_clearing_move(element.x, back=back, target=0, step=step, ctx=ctx), ctx)
    return (
        f"{line}, which the across offset of {ctx.offset_mm(Axis.ACROSS):g} mm puts left of the printhead's "
        f"first dot; move x right by {onto_printhead} mm ({onto_label} mm reaches only the label's edge) "
        "or shorten the text"
    )


def _past_printhead_message(element: Text, *, back: int, printed: int, ctx: RenderContext) -> str:
    """Say why a font 9 line on the label cannot be written, and how far ``x`` must move, in mm."""
    step = _label_length_of_mm(_MOVE_STEP_MM, ctx)
    move = _mm(_clearing_move(element.x, back=back, target=0, step=step, ctx=ctx), ctx)
    points = font9_points(font9_field_points(element), ctx.profile)
    align = TextAlign(element.align).value
    return (
        f"the {ctx.profile.name} prints font 9 at {points} pt {printed} dots right of its record, so its record "
        f"goes {printed} dots left of where the {align}-aligned line starts, left of the printhead's "
        f"first dot; move x right by {move} mm or shorten the text"
    )


def _line_start(x: float, *, back: int, ctx: RenderContext) -> int:
    """Return the column a line starts at when its field sits at ``x`` and starts ``back`` units left of it."""
    return ctx.dpl_units(ctx.shifted(x, axis=Axis.ACROSS), axis=Axis.ACROSS) - back


def _clearing_move(x: float, *, back: int, target: int, step: float, ctx: RenderContext) -> float:
    """Return the smallest multiple of ``step`` that moves ``x`` so its line starts at column ``target`` or later.

    Found by trying each step through the conversion ``x`` itself takes, not by inverting it: that
    conversion rounds the absolute position (the offset included) to whole units, half to even, so
    moving ``x`` by ``d`` units' worth can move its column by only ``d - 1``. The search starts
    below the move the inverse gives by more than a unit, so the first step that clears is the
    smallest. Each candidate is tried as the number a reader types (``x`` plus the move shown), not
    as the float sum, whose last bit can round a half unit the other way. The column only grows
    with ``x``, so the search ends.
    """
    missing = target - _line_start(x, back=back, ctx=ctx)
    estimate = _x_move(missing - 1, ctx)
    steps = max(math.floor(estimate / step) - 1, 0)
    while _line_start(_typed(x + steps * step), back=back, ctx=ctx) < target:
        steps += 1
    return _typed(steps * step)


def _typed(value: float) -> float:
    """Return ``value`` as the decimal a reader would type, without the float sum's last-bit noise."""
    return round(value, 9)


def _x_move(units: int, ctx: RenderContext) -> float:
    """Return about how far ``x`` moves, in the label's unit, to move its column by ``units``.

    Not the dots the units print: a unit is rounded to whole dots on paper, and a scale across
    stretches what ``x`` asks for. A label in dots asks for printed dots, which no scale changes
    (units._printed_inches).
    """
    inches = units * ctx.dots_per_unit / ctx.profile.dpi
    if ctx.unit is Unit.DOTS:
        return inches * ctx.profile.dpi
    inches *= ctx.across.scale
    return inches * MM_PER_INCH if ctx.unit is Unit.MM else inches


def _label_length_of_mm(mm: float, ctx: RenderContext) -> float:
    """Return ``mm`` in the label's unit, at this model's resolution for a label in dots."""
    if ctx.unit is Unit.MM:
        return mm
    inches = mm / MM_PER_INCH
    return inches * ctx.profile.dpi if ctx.unit is Unit.DOTS else inches


def _mm(length: float, ctx: RenderContext) -> str:
    """Return a move of ``length`` in the label's unit as mm to the tenth (a whole number of 0.1 mm steps)."""
    mm = length if ctx.unit is Unit.MM else length / _label_length_of_mm(1.0, ctx)
    return f"{mm:.1f}"


def encode_text(element: Text, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Return the format lines of a text field."""
    size = "000"
    if element.font == FONT_9:
        size = element.font_id or font9_size(font9_field_points(element), index=index, ctx=ctx)
    encoded = encode_for_bitmapped_font(element.text, font=element.font)
    if element.font in CAPITALS_ONLY_FONTS and element.text != element.text.upper():
        ctx.findings.info(FindingCode.TEXT_UPPERCASED, f"font {element.font} prints capitals only", element=index)
    _report_unmappable(encoded, index=index, ctx=ctx, font=f"font {element.font}")
    check_field_length(len(encoded.data), index=index, ctx=ctx)
    head = Header(
        rotation=element.rotation,
        kind=str(element.font),
        width=element.width_multiplier,
        height=element.height_multiplier,
        size=size,
        row=row_of(element.y, index=index, ctx=ctx),
        column=_text_column(element, index=index, ctx=ctx),
    ).encode()
    lines = attribute_lines(element.attribute, index=index, ctx=ctx, text=True)
    lines.append(line(head + encoded.data))
    lines.extend(counter_lines(element.counter))
    lines.extend(attribute_reset(element.attribute, ctx=ctx))
    return lines


def _as_font9(element: ScalableText) -> Text:
    points = round(min(max(element.height_points, _MIN_POINTS), _MAX_POINTS))
    return Text(
        x=element.x,
        y=element.y,
        rotation=element.rotation,
        text=element.text,
        font=FONT_9,
        points=points,
        attribute=element.attribute,
    )


def encode_scalable_text(element: ScalableText, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Return the format lines of a scalable-font field, or its font 9 fallback."""
    if not ctx.supports(Feature.SCALABLE_FONTS):
        ctx.findings.deviate(
            FindingCode.SCALABLE_FONT_UNSUPPORTED,
            f"scalable fonts: {ctx.requirement(Feature.SCALABLE_FONTS)}",
            fallback="printed in font 9 at the nearest point size",
            element=index,
        )
        return encode_text(_as_font9(element), index=index, ctx=ctx)
    encoded = encode_for_symbol_set(element.text, symbol_set=ctx.symbol_set)
    _report_unmappable(encoded, index=index, ctx=ctx, font=f"symbol set {resolved_symbol_set(ctx.symbol_set)}")
    check_field_length(len(encoded.data), index=index, ctx=ctx)
    height = round(element.height_points)
    width = round(element.width_points or element.height_points)
    head = Header(
        rotation=element.rotation,
        kind=str(FONT_9),
        width=1,
        height=1,
        size=element.font_id,
        row=row_of(element.y, index=index, ctx=ctx),
        column=column_of(element.x, index=index, ctx=ctx),
    ).encode()
    lines = attribute_lines(element.attribute, index=index, ctx=ctx, text=True)
    lines.append(line(head + f"P{height:03d}P{width:03d}".encode("ascii") + encoded.data))
    lines.extend(attribute_reset(element.attribute, ctx=ctx))
    return lines


__all__ = ["encode_scalable_text", "encode_text", "font9_size"]
