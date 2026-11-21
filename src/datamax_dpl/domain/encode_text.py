"""Encode text fields: bitmapped fonts 0-9 and scalable fonts, with their fallbacks."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .charset import CAPITALS_ONLY_FONTS, EncodedText, encode_for_bitmapped_font, encode_for_symbol_set
from .encode_common import (
    attribute_lines,
    attribute_reset,
    check_field_length,
    checked_column,
    column_of,
    counter_lines,
    report_past_edge,
    row_of,
)
from .enums import FindingCode, Unit
from .label import FONT_9, ScalableText, Text
from .placeholders import placeholders_in
from .profiles import OLD_SERIES_POINTS, Feature
from .records import Header, line
from .text_metrics import align_shift_units, font9_points, label_edge_column, text_column
from .units import MM_PER_INCH, Axis

if TYPE_CHECKING:
    from .render_context import RenderContext

_MIN_POINTS = 4
_MAX_POINTS = 72


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
    """Return the record column of a text field: ``x``'s, moved back by its alignment.

    ``x`` itself is checked against the printable width (``outside_print_width``), so a centred or
    right-aligned line whose middle or end is past it is refused like a field starting there. Where
    the line starts is measured from ``x`` as the offset moves it, before anything keeps it on the
    printhead, so one finding gives the true distance: a line that would start left of the label's
    left edge is refused (``text_past_left_edge``); one on the label but left of the printhead's
    first column, where a negative offset across puts the label's edge, is reported as a
    left-aligned field the offset moves there. Either is written at
    :func:`~.text_metrics.text_column`, so the job still renders for the findings. A field still
    holding a placeholder in a label rendered without data is checked at ``x`` only: it prints its
    template, and each record is measured once filled.
    """
    shift = align_shift_units(element, ctx)
    if not shift or (ctx.template and placeholders_in(element.text)):
        # x is where the line starts, or its width is not known yet: x itself is checked.
        column = column_of(element.x, index=index, ctx=ctx)
    else:
        shifted = ctx.shifted(element.x, axis=Axis.ACROSS)
        column = checked_column(max(shifted, 0.0), index=index, ctx=ctx)
        start = ctx.dpl_units(shifted, axis=Axis.ACROSS) - shift
        _report_line_start(element, start=start, index=index, ctx=ctx)
    # text_column places the line as the preview and the overlap and edge checks do; min() keeps a
    # column past four digits at the cap the check above refused and returned.
    return min(text_column(element, ctx), column)


def _report_line_start(element: Text, *, start: int, index: int, ctx: RenderContext) -> None:
    """Record an aligned line starting at column ``start`` if that is left of the label's edge or the printhead's."""
    edge = label_edge_column(ctx)
    if start < edge:
        ctx.findings.refuse(
            FindingCode.TEXT_PAST_LEFT_EDGE, _past_edge_message(element, start=start, edge=edge, ctx=ctx), element=index
        )
    elif start < 0:
        report_past_edge(_label_length(start, ctx), Axis.ACROSS, index=index, ctx=ctx)


def _past_edge_message(element: Text, *, start: int, edge: int, ctx: RenderContext) -> str:
    """Say how far a line starting at column ``start`` lies left of the label, and how far ``x`` must move.

    The move goes to the later of the label's edge and the printhead's first column: with a negative
    offset across the edge lies left of that column, and a line moved only onto the label would
    still be refused (``offset_past_label_edge``).
    """
    line = (
        f"the {element.align.value}-aligned line would start about {_mm_up(edge - start, ctx)} mm left of "
        "the label's left edge"
    )
    if edge >= 0:
        return f"{line}; move x right by that or shorten the text"
    return (
        f"{line}, which the across offset of {ctx.offset_mm(Axis.ACROSS):g} mm puts left of the printhead's "
        f"first dot; move x right by about {_mm_up(-start, ctx)} mm or shorten the text"
    )


def _mm_up(units: int, ctx: RenderContext) -> str:
    """Return the move of ``x`` by ``units`` in mm, rounded up to the tenth shown so moving by it is enough."""
    return f"{math.ceil(round(_x_move_mm(units, ctx) * 10, 6)) / 10:.1f}"


def _x_move_mm(units: int, ctx: RenderContext) -> float:
    """Return how many mm ``x`` must move to move its column by ``units``, the inverse of its conversion.

    Not the dots the units print: a unit is rounded to whole dots on paper, and a scale across
    stretches what ``x`` asks for, so a distance in printed dots falls short of the move needed.
    """
    mm = units * ctx.dots_per_unit / ctx.profile.dpi * MM_PER_INCH
    # A label in dots asks for printed dots, which no scale changes (units._printed_inches).
    return mm if ctx.unit is Unit.DOTS else mm * ctx.across.scale


def _label_length(units: int, ctx: RenderContext) -> float:
    """Return ``units`` of this job as a length in the label's unit, at the dots they print."""
    dots = ctx.printed_dots(units)
    if ctx.unit is Unit.DOTS:
        return float(dots)
    inches = dots / ctx.profile.dpi
    return inches * MM_PER_INCH if ctx.unit is Unit.MM else inches


def encode_text(element: Text, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Return the format lines of a text field."""
    size = "000"
    if element.font == FONT_9:
        size = element.font_id or font9_size(element.points or _MIN_POINTS, index=index, ctx=ctx)
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
    _report_unmappable(encoded, index=index, ctx=ctx, font=f"symbol set {ctx.symbol_set or 'PC'}")
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
