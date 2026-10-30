"""Draw a graphic the model cannot express as a downloaded image instead.

Circles, polygons, slanted lines and patterned fills need the circle/polygon records that older
models lack. On those models the element is rasterised at the printer's resolution, downloaded
with ``<STX>I`` before the format, and placed with an image record. Images print in rotation 1
only, which is fine here because the rasterised shape already has its final orientation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, get_args

from .encode_common import column_of, row_of
from .enums import FillPattern, FindingCode
from .label import Box, Circle, Line, Polygon
from .raster import Bitmap
from .records import image_record, line
from .units import Axis

if TYPE_CHECKING:
    from .render_context import RenderContext

Graphic = Line | Box | Circle | Polygon

# Keyed by class rather than built as f"{element.type}_rasterized", so the code is a FindingCode
# member a type checker sees, and a graphic added to the union without one fails at import.
_RASTERIZED: dict[type[Graphic], FindingCode] = {
    Line: FindingCode.LINE_RASTERIZED,
    Box: FindingCode.BOX_RASTERIZED,
    Circle: FindingCode.CIRCLE_RASTERIZED,
    Polygon: FindingCode.POLYGON_RASTERIZED,
}
if set(_RASTERIZED) != set(get_args(Graphic)):  # pragma: no cover - import-time guard
    raise RuntimeError("every Graphic type needs a *_rasterized FindingCode")


@dataclass(frozen=True)
class _Frame:
    """Bounding box of a graphic in label units, and its size in dots."""

    left: float
    bottom: float
    width_dots: int
    height_dots: int


def _extent(element: Graphic) -> tuple[float, float, float, float]:
    """Return ``(left, bottom, right, top)`` of the graphic in label units."""
    if isinstance(element, Circle):
        r = element.radius
        return element.x - r, element.y - r, element.x + r, element.y + r
    if isinstance(element, Box):
        return element.x, element.y, element.x + element.width, element.y + element.height
    if isinstance(element, Line):
        half = element.thickness / 2
        xs, ys = (element.x, element.x2), (element.y, element.y2)
        return min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half
    xs = [p.x for p in element.points]
    ys = [p.y for p in element.points]
    return min(xs), min(ys), max(xs), max(ys)


def _frame(element: Graphic, ctx: RenderContext) -> _Frame:
    left, bottom, right, top = _extent(element)
    left, bottom = max(left, 0.0), max(bottom, 0.0)
    return _Frame(
        left,
        bottom,
        ctx.offset_dots(right - left, axis=Axis.ACROSS) + 1,
        ctx.offset_dots(top - bottom, axis=Axis.ALONG) + 1,
    )


def _pixel(x: float, y: float, frame: _Frame, ctx: RenderContext) -> tuple[int, int]:
    """Map a label point to bitmap pixel coordinates (bitmap row 0 is the top)."""
    column = ctx.offset_dots(x - frame.left, axis=Axis.ACROSS)
    from_bottom = ctx.offset_dots(y - frame.bottom, axis=Axis.ALONG)
    return column, frame.height_dots - 1 - from_bottom


def _draw(element: Graphic, bitmap: Bitmap, frame: _Frame, ctx: RenderContext) -> None:
    if isinstance(element, Circle):
        cx, cy = _pixel(element.x, element.y, frame, ctx)
        bitmap.draw_circle(cx, cy, ctx.dots(element.radius, axis=Axis.ACROSS), fill=element.fill)
    elif isinstance(element, Line):
        x0, y0 = _pixel(element.x, element.y, frame, ctx)
        x1, y1 = _pixel(element.x2, element.y2, frame, ctx)
        bitmap.draw_line(x0, y0, x1, y1, thickness=ctx.dots(element.thickness, axis=Axis.ACROSS))
    elif isinstance(element, Box):
        _draw_box(element, bitmap, ctx)
    else:
        points = [_pixel(p.x, p.y, frame, ctx) for p in element.points]
        bitmap.draw_polygon(points, fill=element.fill)


def _draw_box(element: Box, bitmap: Bitmap, ctx: RenderContext) -> None:
    width, height = bitmap.width, bitmap.height
    if element.fill is not FillPattern.NONE:
        bitmap.fill_rect(0, 0, width, height, pattern=element.fill)
    edge = ctx.dots(element.thickness, axis=Axis.ALONG)
    side = ctx.dots(element.side_thickness or element.thickness, axis=Axis.ACROSS)
    bitmap.fill_rect(0, 0, width, edge)
    bitmap.fill_rect(0, height - edge, width, edge)
    bitmap.fill_rect(0, 0, side, height)
    bitmap.fill_rect(width - side, 0, side, height)


def rasterize(element: Graphic, *, index: int, ctx: RenderContext, reason: str) -> list[bytes]:
    """Draw ``element`` as an image, queue its download and return the placing format line.

    Args:
        element: The graphic to draw.
        index: Its position in ``Label.elements``, for findings.
        ctx: The render context; receives the download command and the finding.
        reason: Why the native record cannot be used, for the finding's message.

    Returns:
        The format line that places the image, or nothing when the image is refused as too wide.
    """
    frame = _frame(element, ctx)
    bitmap = Bitmap(frame.width_dots, frame.height_dots)
    _draw(element, bitmap, frame, ctx)
    name = ctx.download_drawn_image(bitmap, element=index)
    if name is None:
        return []
    ctx.findings.deviate(
        _RASTERIZED[type(element)],
        f"{element.type} cannot be drawn natively: {reason}",
        fallback=f"drawn as downloaded image {name} ({frame.width_dots}x{frame.height_dots} dots)",
        element=index,
    )
    # Through row_of/column_of like every other field, so the printer's offset moves it too.
    row = row_of(frame.bottom, index=index, ctx=ctx)
    column = column_of(frame.left, index=index, ctx=ctx)
    return [line(image_record(row=row, column=column, name=name))]


__all__ = ["Graphic", "rasterize"]
