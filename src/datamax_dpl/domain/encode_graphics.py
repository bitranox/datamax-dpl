"""Encode lines, boxes, circles, polygons and images, falling back to raster images where needed."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .encode_common import column_of, row_of
from .enums import FillPattern, FindingCode
from .label import Box, Circle, Image, Line, Point, Polygon
from .profiles import Feature
from .raster import Bitmap
from .raster_fallback import rasterize
from .records import BoxRecord, circle_record, image_record, line, line_record, polygon_record
from .units import Axis

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


__all__ = ["encode_box", "encode_circle", "encode_image", "encode_line", "encode_polygon"]
