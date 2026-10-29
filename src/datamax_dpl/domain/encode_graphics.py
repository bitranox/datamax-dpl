"""Encode lines, boxes, circles, polygons, images and QR Codes, falling back to raster images where needed."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .encode_common import column_of, row_of
from .enums import FillPattern, FindingCode, Unit
from .label import Box, Circle, Image, Line, Point, Polygon, Qr
from .profiles import Feature
from .qr import QrDataTooLongError, qr_bitmap
from .raster import Bitmap
from .raster_fallback import rasterize
from .records import BoxRecord, circle_record, image_record, line, line_record, polygon_record
from .units import MM_PER_INCH, Axis, to_inches

if TYPE_CHECKING:
    from .render_context import RenderContext


def _polygons(ctx: RenderContext) -> bool:
    return ctx.supports(Feature.CIRCLE_POLYGON)


def _points(points: list[Point], *, index: int, ctx: RenderContext) -> list[tuple[int, int]]:
    return [(row_of(p.y, index=index, ctx=ctx), column_of(p.x, index=index, ctx=ctx)) for p in points]


def _thin_quad(element: Line) -> list[Point]:
    """The four corners of a slanted line of the given thickness."""
    dx, dy = element.x2 - element.x, element.y2 - element.y
    length = math.hypot(dx, dy)
    nx, ny = -dy / length * element.thickness / 2, dx / length * element.thickness / 2
    corners = [
        (element.x + nx, element.y + ny),
        (element.x2 + nx, element.y2 + ny),
        (element.x2 - nx, element.y2 - ny),
        (element.x - nx, element.y - ny),
    ]
    return [Point(x=max(x, 0.0), y=max(y, 0.0)) for x, y in corners]


def encode_line(element: Line, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Return a line record, a thin filled polygon for a slanted line, or a raster fallback."""
    if element.is_axis_aligned:
        horizontal = element.y == element.y2 and element.x != element.x2
        length = abs(element.x2 - element.x) if horizontal else abs(element.y2 - element.y)
        thickness_axis, length_axis = (Axis.ALONG, Axis.ACROSS) if horizontal else (Axis.ACROSS, Axis.ALONG)
        thickness = ctx.distance(element.thickness, axis=thickness_axis, element=index, what="line thickness") or 1
        extent = ctx.distance(length, axis=length_axis, element=index, what="line length") or thickness
        record = line_record(
            row=row_of(min(element.y, element.y2), index=index, ctx=ctx),
            column=column_of(min(element.x, element.x2), index=index, ctx=ctx),
            horizontal=extent if horizontal else thickness,
            vertical=thickness if horizontal else extent,
        )
        return [line(record)]
    if _polygons(ctx):
        quad = _thin_quad(element)
        return [line(polygon_record(_points(quad, index=index, ctx=ctx), fill=FillPattern.SOLID))]
    return rasterize(element, index=index, ctx=ctx, reason=f"slanted lines: {ctx.requirement(Feature.CIRCLE_POLYGON)}")


def _box_outline(element: Box, *, index: int, ctx: RenderContext, solid: bool = False) -> bytes:
    width = ctx.distance(element.width, axis=Axis.ACROSS, element=index, what="box width")
    height = ctx.distance(element.height, axis=Axis.ALONG, element=index, what="box height")
    edge = ctx.distance(element.thickness, axis=Axis.ALONG, element=index, what="box edge") or 1
    side = (
        ctx.distance(element.side_thickness or element.thickness, axis=Axis.ACROSS, element=index, what="box side") or 1
    )
    if solid:
        edge, side = math.ceil(height / 2), math.ceil(width / 2)
    record = BoxRecord(
        row=row_of(element.y, index=index, ctx=ctx),
        column=column_of(element.x, index=index, ctx=ctx),
        width=width,
        height=height,
        edge=edge,
        side=side,
    )
    return line(record.encode())


def _box_corners(element: Box) -> list[Point]:
    right, top = element.x + element.width, element.y + element.height
    return [
        Point(x=element.x, y=element.y),
        Point(x=right, y=element.y),
        Point(x=right, y=top),
        Point(x=element.x, y=top),
    ]


def encode_box(element: Box, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Return a box record; fills use a polygon, thick edges (solid) or a raster fallback."""
    if element.fill is FillPattern.NONE:
        return [_box_outline(element, index=index, ctx=ctx)]
    if _polygons(ctx):
        fill = line(polygon_record(_points(_box_corners(element), index=index, ctx=ctx), fill=element.fill))
        return [fill, _box_outline(element, index=index, ctx=ctx)]
    if element.fill is FillPattern.SOLID:
        ctx.findings.info(FindingCode.SOLID_FILL_AS_EDGES, "solid box drawn as a box whose edges meet", element=index)
        return [_box_outline(element, index=index, ctx=ctx, solid=True)]
    return rasterize(
        element, index=index, ctx=ctx, reason=f"patterned fills: {ctx.requirement(Feature.CIRCLE_POLYGON)}"
    )


def encode_circle(element: Circle, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Return a circle record or its raster fallback."""
    if not _polygons(ctx):
        return rasterize(element, index=index, ctx=ctx, reason=f"circles: {ctx.requirement(Feature.CIRCLE_POLYGON)}")
    record = circle_record(
        row=row_of(element.y, index=index, ctx=ctx),
        column=column_of(element.x, index=index, ctx=ctx),
        radius=ctx.distance(element.radius, axis=Axis.ACROSS, element=index, what="radius") or 1,
        fill=element.fill,
    )
    return [line(record)]


def encode_polygon(element: Polygon, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Return a polygon record or its raster fallback."""
    if not _polygons(ctx):
        return rasterize(element, index=index, ctx=ctx, reason=f"polygons: {ctx.requirement(Feature.CIRCLE_POLYGON)}")
    return [line(polygon_record(_points(element.points, index=index, ctx=ctx), fill=element.fill))]


def encode_image(element: Image, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Queue the image download (when a bitmap is given) and return its placing record."""
    if element.bitmap is not None:
        ctx.download_image(element.name, Bitmap.from_rows(element.bitmap))
    else:
        ctx.findings.info(
            FindingCode.IMAGE_MUST_BE_STORED, f"image {element.name} must already be on the printer", element=index
        )
    record = image_record(
        row=row_of(element.y, index=index, ctx=ctx),
        column=column_of(element.x, index=index, ctx=ctx),
        name=element.name,
    )
    return [line(record)]


def _label_length_of_dots(dots: int, ctx: RenderContext) -> float:
    """Return ``dots`` printer dots as a length in the label's unit."""
    inches = dots / ctx.profile.dpi
    if ctx.unit is Unit.DOTS:
        return float(dots)
    return inches * MM_PER_INCH if ctx.unit is Unit.MM else inches


def _check_right_edge(element: Qr, *, width_dots: int, index: int, ctx: RenderContext) -> None:
    """Refuse a QR Code whose area, quiet zone included, ends past the model's print width.

    The left edge is the field's position after the printer's offset; a calibrated length prints
    at its nominal size, so the edge in printer dots is that position at the model's resolution.
    """
    dpi = ctx.profile.dpi
    left = round(to_inches(max(ctx.shifted(element.x, axis=Axis.ACROSS), 0.0), ctx.unit, dpi=dpi) * dpi)
    limit = round(ctx.profile.print_width_in * dpi)
    if left + width_dots > limit:
        ctx.findings.refuse(
            FindingCode.QR_PAST_PRINT_WIDTH,
            f"the QR Code with its quiet zone ends at dot {left + width_dots}; it passes the {limit}-dot "
            f"print width of {ctx.profile.name}",
            element=index,
        )


def encode_qr(element: Qr, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Queue the QR Code's module matrix as an image and return its magnified placing record.

    The image holds one dot per module, the smallest download; the record's dot width and height
    print each module ``module_dots`` dots square. ``(x, y)`` goes through the same position rules
    as every other field, and the symbol starts the quiet zone in from it.
    """
    try:
        symbol = qr_bitmap(element.data, error=element.error)
    except QrDataTooLongError as exc:
        ctx.findings.refuse(FindingCode.QR_DATA_TOO_LONG, str(exc), element=index)
        return []
    quiet_dots = element.quiet_zone * element.module_dots
    _check_right_edge(element, width_dots=symbol.width * element.module_dots + 2 * quiet_dots, index=index, ctx=ctx)
    name = ctx.next_image_name()
    ctx.download_image(name, symbol)
    quiet = _label_length_of_dots(quiet_dots, ctx)
    record = image_record(
        row=row_of(element.y, index=index, ctx=ctx)
        + ctx.distance(quiet, axis=Axis.ALONG, element=index, what="quiet zone"),
        column=column_of(element.x, index=index, ctx=ctx)
        + ctx.distance(quiet, axis=Axis.ACROSS, element=index, what="quiet zone"),
        name=name,
        magnification=(element.module_dots, element.module_dots),
    )
    return [line(record)]


__all__ = ["encode_box", "encode_circle", "encode_image", "encode_line", "encode_polygon", "encode_qr"]
