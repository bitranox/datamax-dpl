"""Encode lines, boxes, circles, polygons, images and QR Codes, falling back to raster images where needed."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .encode_common import column_of, last_column, row_of
from .enums import FillPattern, FindingCode
from .label import Box, Circle, Image, Line, Point, Polygon, Qr
from .profiles import Feature
from .qr import QrDataTooLongError, qr_bitmap
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


def _box_record(element: Box, *, index: int, ctx: RenderContext, solid: bool = False) -> BoxRecord:
    width = ctx.distance(element.width, axis=Axis.ACROSS, element=index, what="box width")
    height = ctx.distance(element.height, axis=Axis.ALONG, element=index, what="box height")
    edge = ctx.distance(element.thickness, axis=Axis.ALONG, element=index, what="box edge") or 1
    side = (
        ctx.distance(element.side_thickness or element.thickness, axis=Axis.ACROSS, element=index, what="box side") or 1
    )
    if solid:
        edge, side = math.ceil(height / 2), math.ceil(width / 2)
    return BoxRecord(
        row=row_of(element.y, index=index, ctx=ctx),
        column=column_of(element.x, index=index, ctx=ctx),
        width=width,
        height=height,
        edge=edge,
        side=side,
    )


def _box_corners(element: Box) -> list[Point]:
    right, top = element.x + element.width, element.y + element.height
    return [
        Point(x=element.x, y=element.y),
        Point(x=right, y=element.y),
        Point(x=right, y=top),
        Point(x=element.x, y=top),
    ]


def _units_of_dots(dots: int, ctx: RenderContext) -> int:
    """The fewest DPL units that span at least ``dots`` printer dots."""
    return math.ceil(dots / ctx.dots_per_unit)


def _inset_fill(outline: BoxRecord, *, pattern: FillPattern, ctx: RenderContext) -> list[bytes]:
    """Return the polygon that fills the inside of ``outline`` without touching it, or nothing.

    Most models print overlapping fields XOR, so a dot both records print would come out white.
    The polygon keeps one dot clear of the edges and sides, since the printer turns the box's
    position, size and thicknesses into dots one by one and each can round a dot either way; its
    far corners keep a second dot clear, because a polygon's line (brush 1) is drawn on its
    vertices while the box's width counts dots from its column. Sides or edges that meet leave
    no inside, and no polygon is sent.
    """
    near, far = _units_of_dots(1, ctx), _units_of_dots(2, ctx)
    bottom, left = outline.row + outline.edge + near, outline.column + outline.side + near
    top = outline.row + outline.height - outline.edge - far
    right = outline.column + outline.width - outline.side - far
    if top < bottom or right < left:
        return []
    corners = [(bottom, left), (bottom, right), (top, right), (top, left)]
    return [line(polygon_record(corners, fill=pattern))]


def encode_box(element: Box, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Return a box record; fills use a polygon, thick edges (solid) or a raster fallback.

    A solid fill is one solid polygon over the whole box, which covers the outline too; a
    patterned fill is a polygon inside the outline that shares no dot with it, since on most
    models two fields' common dots print white (XOR).
    """
    if element.fill is FillPattern.NONE:
        return [line(_box_record(element, index=index, ctx=ctx).encode())]
    if _polygons(ctx) and element.fill is FillPattern.SOLID:
        return [line(polygon_record(_points(_box_corners(element), index=index, ctx=ctx), fill=FillPattern.SOLID))]
    if _polygons(ctx):
        outline = _box_record(element, index=index, ctx=ctx)
        return [*_inset_fill(outline, pattern=element.fill, ctx=ctx), line(outline.encode())]
    if element.fill is FillPattern.SOLID:
        ctx.findings.info(FindingCode.SOLID_FILL_AS_EDGES, "solid box drawn as a box whose edges meet", element=index)
        return [line(_box_record(element, index=index, ctx=ctx, solid=True).encode())]
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
    """Queue the image download (when a bitmap is given) and return its placing record.

    A bitmap refused for its width places nothing. The record places what the context says:
    the element's name at its magnification, or, when the job halves its images, the halved image's
    content name at double the magnification (also for a stored-only reference to a name the job
    downloads).
    """
    if element.bitmap is not None:
        bitmap = Bitmap.from_rows(element.bitmap)
        placed = ctx.download_image(element.name, bitmap, element=index, magnification=element.magnification)
    else:
        ctx.findings.info(
            FindingCode.IMAGE_MUST_BE_STORED, f"image {element.name} must already be on the printer", element=index
        )
        placed = ctx.place_stored_image(element.name, element=index, magnification=element.magnification)
    if placed is None:
        return []
    record = image_record(
        row=row_of(element.y, index=index, ctx=ctx),
        column=column_of(element.x, index=index, ctx=ctx),
        name=placed.name,
        magnification=placed.dots,
    )
    return [line(record)]


def _check_right_edge(element: Qr, *, start_column: int, extent_dots: int, index: int, ctx: RenderContext) -> None:
    """Refuse a QR Code whose area, quiet zone included, ends past the printhead's last dot.

    Counted in printhead dots from the column the placing record writes (corrected like every
    column), since the printer addresses the head by dot, whatever the calibration says the dots
    measure on paper.

    Args:
        element: The QR element.
        start_column: The record's column, where the symbol starts.
        extent_dots: The symbol plus one quiet zone, the part right of ``start_column``.
        index: Position of the element in the label.
        ctx: The render context.
    """
    end = ctx.printed_dots(start_column) + extent_dots
    limit = round(ctx.profile.print_width_in * ctx.profile.dpi)
    if end > limit:
        ctx.findings.refuse(
            FindingCode.QR_PAST_PRINT_WIDTH,
            f"the {element.type} element with its quiet zone ends at dot {end}; it passes the {limit}-dot "
            f"print width of {ctx.profile.name}",
            element=index,
        )


def encode_qr(element: Qr, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Queue the QR Code's module matrix as an image and return its magnified placing record.

    The image holds one dot per module, the smallest download; the record's dot width and height
    print each module ``module_dots`` dots square. ``(x, y)`` goes through the same position rules
    as every other field, and the symbol starts the quiet zone in from it. A field already refused
    for starting past the last column gets no second finding for its right edge.
    """
    try:
        symbol = qr_bitmap(element.data, error=element.error)
    except QrDataTooLongError as exc:
        ctx.findings.refuse(FindingCode.QR_DATA_TOO_LONG, str(exc), element=index)
        return []
    quiet_dots = element.quiet_zone * element.module_dots
    quiet = ctx.length_of_dots(quiet_dots)
    row = row_of(element.y, index=index, ctx=ctx) + ctx.distance(
        quiet, axis=Axis.ALONG, element=index, what="quiet zone"
    )
    field_column = column_of(element.x, index=index, ctx=ctx)
    column = field_column + ctx.distance(quiet, axis=Axis.ACROSS, element=index, what="quiet zone")
    if field_column <= last_column(ctx):
        extent = symbol.width * element.module_dots + quiet_dots
        _check_right_edge(element, start_column=column, extent_dots=extent, index=index, ctx=ctx)
    # Never halved: the image holds one dot per module, so halving it would merge modules.
    placed = ctx.download_drawn_image(symbol, element=index, magnification=element.module_dots, halvable=False)
    if placed is None:
        return []
    return [line(image_record(row=row, column=column, name=placed.name, magnification=placed.dots))]


__all__ = ["encode_box", "encode_circle", "encode_image", "encode_line", "encode_polygon", "encode_qr"]
