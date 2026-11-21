"""Encode text fields: bitmapped fonts 0-9 and scalable fonts, with their fallbacks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .charset import CAPITALS_ONLY_FONTS, EncodedText, encode_for_bitmapped_font, encode_for_symbol_set
from .encode_common import (
    attribute_lines,
    attribute_reset,
    check_field_length,
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
from .text_metrics import align_shift_units, font9_points, label_edge_column
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
    right-aligned line whose middle or end is past it is refused like a field starting there. A
    line that would start left of the label's left edge is refused (``text_past_left_edge``); one
    on the label but left of the printhead's first column, where a negative offset across puts the
    label's edge, is reported as a left-aligned field the offset moves there. Either is written at
    :func:`~.text_metrics.text_column`, so the job still renders for the findings. A field still
    holding a placeholder in a label rendered without data is not refused: it prints its template,
    and each record is measured once filled.
    """
    column = column_of(element.x, index=index, ctx=ctx)
    start = column - align_shift_units(element, ctx)
    edge = label_edge_column(ctx)
    if start >= max(edge, 0):
        return start
    if not (ctx.template and placeholders_in(element.text)):
        _report_line_past_edge(element, start=start, edge=edge, index=index, ctx=ctx)
    return max(edge, 0)


def _report_line_past_edge(element: Text, *, start: int, edge: int, index: int, ctx: RenderContext) -> None:
    """Record an aligned line that starts at column ``start``, left of the label's edge or the printhead's."""
    if start < edge:
        past_mm = ctx.printed_dots(edge - start) / ctx.profile.dpi * MM_PER_INCH
        ctx.findings.refuse(
            FindingCode.TEXT_PAST_LEFT_EDGE,
            f"the {element.align.value}-aligned line would start about {past_mm:.1f} mm left of "
            "the label's left edge; move x right by that or shorten the text",
            element=index,
        )
        return
    report_past_edge(_label_length(start, ctx), Axis.ACROSS, index=index, ctx=ctx)


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
