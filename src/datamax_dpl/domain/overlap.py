"""Which fields of a label print dots on the same spot.

A model without the ``A`` command prints every field XOR, so a dot two fields share prints white.
To emulate a transparent label there, the fields that share dots are drawn into one image (see
:mod:`~datamax_dpl.domain.transparency`); this module finds them.

Detection runs on the PRINTED geometry, in printhead dots at this job's calibration: column from
the label's left edge, row up from its bottom. Each field sits where its own record puts it: at
the DPL row and column the encoder writes (moved by the printer's offset, kept on the label's 0
edge, :func:`~datamax_dpl.domain.encode_common.position_units`), turned into dots. Rounding the
offset into each position can move two fields a unit apart or together, so detection and the
merged image both start from those units, never from the label lengths. Two kinds of field take
part:

* EXACT fields, whose dots the library knows: lines, boxes, circles, polygons, QR Codes, images
  with a bitmap, and stored-only references to a name another element of the job downloads (the
  printer prints that download there). A horizontal or vertical line and a box without a patterned
  fill are the rectangles their line and box records print (each length converted to DPL units,
  then to dots, as the printer is told); every other graphic is the raster fallback's drawing; an
  image or QR Code is its bitmap magnified. Two exact fields overlap when they share a dot; groups chain
  (A meets B, B meets C).
* INEXACT fields, whose dots the printer draws from its own fonts and symbologies: text, scalable
  text, barcodes, and images only the printer holds. They take part by the rectangle the preview
  draws for them, and one "overlaps" when that rectangle meets another field's dots or rectangle.
  This over-reports on purpose: a rectangle that only meets white space inside a letter still
  counts.

Candidates are found by sorting the rectangles by their left edge and sweeping, so a label of many
small fields apart costs about its field count, and only the dots of two candidates' common
rectangle are compared.

The geometry is that of a model WITHOUT circle and polygon records, the only models whose
transparency is emulated: there circles, polygons, slanted lines and patterned boxes are sent as
the raster fallback draws them. Halving is not part of it. A job past its image memory budget
sends its images halved (a merged one or one standing alone), and a halved image prints each
black dot as its whole 2x2 block: up to one dot further than detection saw it, and one dot past
the right or top edge of an image whose width or height is odd. That dot can land on a
neighbouring field OUTSIDE the group, which then shares it, and XOR prints it white. Inside one
merged image this does not matter, since its members are OR-ed anyway. For example, on an
Allegro 2 past its budget, an image 3 dots wide whose last column is black and a box starting
3 dots to its right only touch here, yet the halved image prints 4 dots wide, onto the box.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .encode_common import position_units
from .encode_graphics import qr_symbol_position
from .enums import FillPattern
from .image_budget import magnify
from .label import Barcode, Box, Circle, Image, Line, Polygon, Qr, ScalableText, Text
from .preview import STORED_IMAGE_DOTS, Canvas, PreviewGeometry, field_area
from .qr import QrDataTooLongError, qr_bitmap
from .raster import Bitmap
from .raster_fallback import draw_graphic, frame_of
from .units import Axis

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .label import Element
    from .render_context import RenderContext


@dataclass(frozen=True)
class DotArea:
    """A rectangle of printhead dots; ``left`` and ``bottom`` are its first column and lowest row.

    Example:
        >>> DotArea(left=0, bottom=0, width=4, height=2).meets(DotArea(left=3, bottom=1, width=4, height=4))
        DotArea(left=3, bottom=1, width=1, height=1)
        >>> DotArea(left=0, bottom=0, width=4, height=2).meets(DotArea(left=4, bottom=0, width=1, height=1)) is None
        True
    """

    left: int
    bottom: int
    width: int
    height: int

    @property
    def right(self) -> int:
        """The last column."""
        return self.left + self.width - 1

    @property
    def top(self) -> int:
        """The highest row."""
        return self.bottom + self.height - 1

    def meets(self, other: DotArea) -> DotArea | None:
        """Return the dots both rectangles cover, or None when they share none (touching is not meeting)."""
        left, bottom = max(self.left, other.left), max(self.bottom, other.bottom)
        right, top = min(self.right, other.right), min(self.top, other.top)
        if right < left or top < bottom:
            return None
        return DotArea(left=left, bottom=bottom, width=right - left + 1, height=top - bottom + 1)


@dataclass(frozen=True)
class PrintedDots:
    """The dots one exact field prints.

    Attributes:
        area: The rectangle ``bitmap`` covers.
        bitmap: The dots, row 0 being the area's top row.
        column: DPL column the field's record is written at; ``area.left`` is its dot.
        row: DPL row the field's record is written at; ``area.bottom`` is its dot.
    """

    area: DotArea
    bitmap: Bitmap
    column: int
    row: int

    def shares_dots(self, other: PrintedDots) -> bool:
        """Return whether both fields print at least one dot on the same spot."""
        return self.bitmap.shares_dots(other.bitmap, other.area.left - self.area.left, self.area.top - other.area.top)

    def shares_area(self, area: DotArea) -> bool:
        """Return whether the field prints at least one dot inside ``area``."""
        block = Bitmap(area.width, area.height)
        block.fill_rect(0, 0, area.width, area.height)
        return self.bitmap.shares_dots(block, area.left - self.area.left, self.area.top - area.top)


@dataclass(frozen=True)
class OverlapReport:
    """Which fields of a label share dots.

    Attributes:
        groups: Exact fields that share dots, each group two or more element indices, sorted.
        inexact: Inexact fields whose rectangle meets another field's dots or rectangle, sorted.
    """

    groups: tuple[tuple[int, ...], ...] = ()
    inexact: tuple[int, ...] = ()


def _dots_at(value: float, *, axis: Axis, ctx: RenderContext) -> int:
    """The dot a label position prints at: the DPL unit its record is written at, in dots."""
    return ctx.printed_dots(position_units(value, axis=axis, ctx=ctx))


def _length_dots(units: int, ctx: RenderContext) -> int:
    """Dots a record length prints as, at least one so that a bitmap can hold it."""
    return max(1, ctx.printed_dots(units))


def _at_units(bitmap: Bitmap, *, column: int, row: int, ctx: RenderContext) -> PrintedDots:
    left, bottom = ctx.printed_dots(column), ctx.printed_dots(row)
    return PrintedDots(DotArea(left, bottom, bitmap.width, bitmap.height), bitmap, column, row)


def _placed(bitmap: Bitmap, *, x: float, y: float, ctx: RenderContext) -> PrintedDots:
    """Place ``bitmap`` where a record for the label position ``(x, y)`` is written."""
    column = position_units(x, axis=Axis.ACROSS, ctx=ctx)
    return _at_units(bitmap, column=column, row=position_units(y, axis=Axis.ALONG, ctx=ctx), ctx=ctx)


def _line_dots(element: Line, ctx: RenderContext) -> PrintedDots:
    """The rectangle a line record prints, sized as ``encode_line`` writes it."""
    horizontal = element.y == element.y2 and element.x != element.x2
    length = abs(element.x2 - element.x) if horizontal else abs(element.y2 - element.y)
    thickness_axis, length_axis = (Axis.ALONG, Axis.ACROSS) if horizontal else (Axis.ACROSS, Axis.ALONG)
    thickness = ctx.dpl_units(element.thickness, axis=thickness_axis) or 1
    extent = ctx.dpl_units(length, axis=length_axis) or thickness
    across, along = (extent, thickness) if horizontal else (thickness, extent)
    bitmap = Bitmap(_length_dots(across, ctx), _length_dots(along, ctx))
    bitmap.fill_rect(0, 0, bitmap.width, bitmap.height)
    return _placed(bitmap, x=min(element.x, element.x2), y=min(element.y, element.y2), ctx=ctx)


def _box_dots(element: Box, ctx: RenderContext) -> PrintedDots:
    """The outline a box record prints, sized as ``encode_box`` writes it; solid when the fill is."""
    width = _length_dots(ctx.dpl_units(element.width, axis=Axis.ACROSS), ctx)
    height = _length_dots(ctx.dpl_units(element.height, axis=Axis.ALONG), ctx)
    edge = ctx.printed_dots(ctx.dpl_units(element.thickness, axis=Axis.ALONG) or 1)
    side = ctx.printed_dots(ctx.dpl_units(element.side_thickness or element.thickness, axis=Axis.ACROSS) or 1)
    bitmap = Bitmap(width, height)
    if element.fill is FillPattern.SOLID:
        edge, side = height, width
    for x, y, w, h in ((0, 0, width, edge), (0, height - edge, width, edge), (0, 0, side, height)):
        bitmap.fill_rect(x, y, w, h)
    bitmap.fill_rect(width - side, 0, side, height)
    return _placed(bitmap, x=element.x, y=element.y, ctx=ctx)


def _fallback_dots(element: Line | Box | Circle | Polygon, ctx: RenderContext) -> PrintedDots:
    """The image the raster fallback draws for a graphic, placed where it places it."""
    frame = frame_of(element, ctx)
    bitmap = Bitmap(frame.width_dots, frame.height_dots)
    draw_graphic(element, bitmap, frame, ctx)
    return _placed(bitmap, x=frame.left, y=frame.bottom, ctx=ctx)


def _qr_dots(element: Qr, ctx: RenderContext) -> PrintedDots | None:
    """The QR Code's modules, magnified, the quiet zone in from ``(x, y)``; None when no version holds it.

    Placed by ``encode_qr``'s own :func:`~datamax_dpl.domain.encode_graphics.qr_symbol_position`.
    """
    try:
        symbol = qr_bitmap(element.data, error=element.error)
    except QrDataTooLongError:
        return None
    column, row = qr_symbol_position(
        element,
        column=position_units(element.x, axis=Axis.ACROSS, ctx=ctx),
        row=position_units(element.y, axis=Axis.ALONG, ctx=ctx),
        ctx=ctx,
    )
    return _at_units(magnify(symbol, element.module_dots), column=column, row=row, ctx=ctx)


def _image_dots(element: Image, ctx: RenderContext) -> PrintedDots | None:
    """The image's bitmap magnified, or None for an image only the printer holds."""
    rows = ctx.image_rows(element)
    if rows is None:
        return None
    return _placed(magnify(Bitmap.from_rows(rows), element.magnification), x=element.x, y=element.y, ctx=ctx)


def printed_dots(element: Element, ctx: RenderContext) -> PrintedDots | None:
    """Return the dots an exact field prints, or None for an inexact one.

    A QR Code whose data fits no version prints nothing (it is refused) and is None too.
    """
    if isinstance(element, Line) and element.is_axis_aligned:
        return _line_dots(element, ctx)
    if isinstance(element, Box) and element.fill in (FillPattern.NONE, FillPattern.SOLID):
        return _box_dots(element, ctx)
    if isinstance(element, (Line, Box, Circle, Polygon)):
        return _fallback_dots(element, ctx)
    if isinstance(element, Image):
        return _image_dots(element, ctx)
    if isinstance(element, Qr):
        return _qr_dots(element, ctx)
    return None


def _field_rectangle(element: Text | ScalableText | Barcode, ctx: RenderContext) -> DotArea:
    """The rectangle the preview draws for a text field or barcode, turned, at the printed position.

    The size is the preview's (fonts and bars are counted in dots, which no scale changes); the
    reference point is where the record puts it.
    """
    # A one-pixel canvas puts the reference point's row at minus its height above the bottom.
    area = field_area(element, Canvas(width=1, height=1, geometry=PreviewGeometry(ctx)))
    x, y = _dots_at(element.x, axis=Axis.ACROSS, ctx=ctx), _dots_at(element.y, axis=Axis.ALONG, ctx=ctx)
    left, top, width, height = dataclasses.replace(area, x=x, y=-y).box()
    return DotArea(left=left, bottom=-(top + height - 1), width=width, height=height)


def inexact_rectangle(element: Element, ctx: RenderContext) -> DotArea | None:
    """Return the rectangle an inexact field takes part by, or None for an exact one."""
    if isinstance(element, (Text, ScalableText, Barcode)):
        return _field_rectangle(element, ctx)
    if isinstance(element, Image) and ctx.image_rows(element) is None:
        size = STORED_IMAGE_DOTS * element.magnification
        left, bottom = _dots_at(element.x, axis=Axis.ACROSS, ctx=ctx), _dots_at(element.y, axis=Axis.ALONG, ctx=ctx)
        return DotArea(left=left, bottom=bottom, width=size, height=size)
    return None


class _Groups:
    """Union-find over element indices."""

    def __init__(self) -> None:
        self._parent: dict[int, int] = {}

    def find(self, index: int) -> int:
        root = self._parent.setdefault(index, index)
        while root != self._parent[root]:
            root = self._parent[root]
        self._parent[index] = root
        return root

    def join(self, first: int, second: int) -> None:
        self._parent[self.find(first)] = self.find(second)

    def groups(self) -> tuple[tuple[int, ...], ...]:
        members: dict[int, list[int]] = {}
        for index in self._parent:
            members.setdefault(self.find(index), []).append(index)
        return tuple(sorted(tuple(sorted(group)) for group in members.values() if len(group) > 1))


def _candidates(areas: dict[int, DotArea]) -> list[tuple[int, int]]:
    """Return every pair of indices whose rectangles share a dot, by a sweep over their left edges."""
    order = sorted(areas, key=lambda index: areas[index].left)
    active: list[int] = []
    pairs: list[tuple[int, int]] = []
    for index in order:
        area = areas[index]
        active = [other for other in active if areas[other].right >= area.left]
        pairs.extend((min(other, index), max(other, index)) for other in active if areas[other].meets(area))
        active.append(index)
    return pairs


@dataclass
class _Fields:
    """What each field of the label takes part by: its dots, or its rectangle."""

    exact: dict[int, PrintedDots]
    inexact: dict[int, DotArea]

    def area(self, index: int) -> DotArea:
        found = self.exact.get(index)
        return found.area if found is not None else self.inexact[index]

    def meet(self, first: int, second: int) -> bool:
        """Whether an inexact field's rectangle meets the other field's dots or rectangle."""
        exact = self.exact.get(first) or self.exact.get(second)
        common = self.area(first).meets(self.area(second))
        if common is None:
            return False
        return exact is None or exact.shares_area(common)


def _fields(elements: Sequence[Element], ctx: RenderContext) -> _Fields:
    fields = _Fields(exact={}, inexact={})
    for index, element in enumerate(elements):
        dots = printed_dots(element, ctx)
        rectangle = inexact_rectangle(element, ctx) if dots is None else None
        if dots is not None:
            fields.exact[index] = dots
        elif rectangle is not None:
            fields.inexact[index] = rectangle
    return fields


def find_overlaps(elements: Sequence[Element], ctx: RenderContext) -> OverlapReport:
    """Return which fields share printed dots: groups of exact fields and the inexact fields that meet any.

    Graphics are taken as a model without circle and polygon records sends them (module docstring).

    Args:
        elements: The label's elements, placeholders already filled.
        ctx: The render context; its calibration scales every length. Nothing is recorded in it.

    Returns:
        The groups and inexact fields, by index into ``elements``.
    """
    fields = _fields(elements, ctx)
    groups = _Groups()
    inexact: set[int] = set()
    for first, second in _candidates({index: fields.area(index) for index in (*fields.exact, *fields.inexact)}):
        if first in fields.exact and second in fields.exact:
            if fields.exact[first].shares_dots(fields.exact[second]):
                groups.join(first, second)
        elif fields.meet(first, second):
            inexact.update(index for index in (first, second) if index in fields.inexact)
    return OverlapReport(groups=groups.groups(), inexact=tuple(sorted(inexact)))


__all__ = ["DotArea", "OverlapReport", "PrintedDots", "find_overlaps", "inexact_rectangle", "printed_dots"]
