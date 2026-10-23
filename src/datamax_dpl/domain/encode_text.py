"""Encode text fields: bitmapped fonts 0-9 and scalable fonts, with their fallbacks."""

from __future__ import annotations

from .charset import EncodedText, encode_for_bitmapped_font, encode_for_symbol_set
from .encode_common import (
    attribute_lines,
    attribute_reset,
    check_field_length,
    column_of,
    counter_lines,
    row_of,
)
from .label import FONT_9, ScalableText, Text
from .profiles import OLD_SERIES_POINTS, Feature
from .records import Header, line
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


def _available_points(ctx: RenderContext) -> list[int]:
    return sorted(set(ctx.profile.a_series_points) | set(ctx.profile.old_series_points))


def font9_size(points: int, *, index: int, ctx: RenderContext) -> str:
    """Return the size code for font 9 at ``points``, substituting the nearest size the model has."""
    code = _size_code(points, ctx)
    if code is not None:
        return code
    nearest = min(_available_points(ctx), key=lambda p: (abs(p - points), p))
    ctx.findings.deviate(
        "font_size_substituted",
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
        "characters_unmappable",
        f"{font} cannot print {shown}",
        fallback="each replaced by '?'",
        element=index,
    )


def encode_text(element: Text, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Return the format lines of a text field."""
    size = "000"
    if element.font == FONT_9:
        size = element.font_id or font9_size(element.points or _MIN_POINTS, index=index, ctx=ctx)
    encoded = encode_for_bitmapped_font(element.text, font=element.font)
    _report_unmappable(encoded, index=index, ctx=ctx, font=f"font {element.font}")
    check_field_length(len(encoded.data), index=index, ctx=ctx)
    head = Header(
        rotation=element.rotation,
        kind=str(element.font),
        width=element.width_multiplier,
        height=element.height_multiplier,
        size=size,
        row=row_of(element.y, index=index, ctx=ctx),
        column=column_of(element.x, index=index, ctx=ctx),
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
            "scalable_font_unsupported",
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
