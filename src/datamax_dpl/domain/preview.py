"""Approximate picture of a label at the printer's resolution (one pixel per printer dot).

Every field sits where its record puts it, through one conversion (:class:`PreviewGeometry`): the
label position goes to the DPL row or column the encoder writes (the printer's offset added, kept on
the label's 0 edge, rounded to the job's unit), that to the dots the printer prints for it (2 per
1/100 in on the Allegro 2), and then the job's scale and offset are undone. So the picture is the
paper at nominal size, as the calibration means it to print, and a length the job's unit cannot
hold exactly (millimetres in 1/100 in) shows the dot the printer really uses. A graphic's lengths
(a line's, a box's sides, a radius) take the same way without the offset. Sizes the printer counts
in dots (characters, bars, image and QR Code dots) are drawn as those dots; the platen's scale
along the feed is not undone for them. A horizontal or vertical line is the line record's
rectangle, growing up or right from its lower-left end; a slanted one is a centred stroke.
A patterned box fill is drawn up to its outline; on a model with polygons it prints one or two dots
short of it, which keeps the fill off the outline's dots (``encode_box``).

Text is drawn as one grey block per character cell, sized from the font tables in
``docs/dpl-reference.md`` section 6; barcodes as a hatched area of their estimated size. The
picture answers "does everything sit where I meant and fit the label", not "what does the text
look like": the optional Pillow renderer in ``adapters/preview`` hands in each field's lettering
as a mask (``preview_bitmap``'s ``masks``), and a real print is the final check.
A mask takes the place of a text field's cells, so an overlapping field combines with the letters'
dots: an Allegro 2 printed a line through text with gaps only under the strokes (2026-10-30). A
barcode's mask is its data lettered in the top quarter of its hatched area (``mask_area``).

Rotation follows the manual's record header field ``a`` (printed page 94, PDF page 104), which lists
1 (0 degrees), 2 (90), 3 (180) and 4 (270) and says: "Figure 6-2 shows the direction and amount of
rotation clockwise, relative to the label feed direction." Figure 7-1 (page 92) prints ROTATION 1-4
from one reference point (row 200, column 200 in Table 7-1, page 91), drawn with the home position
at the lower left as this preview is. The rule read from it: a text field or barcode turns clockwise
about its reference point, the lower-left corner of the upright field, which stays where the record
puts it. Rotation 1 (0) runs right and grows up; 2 (90) runs down and grows right; 3 (180) runs left
and grows down; 4 (270) runs up and grows left. Characters advance from the reference point along
the running direction. Images are placed in rotation 1 only. Unverified until a test print.

A transparent label on a model without the ``A`` command prints the fields that share dots as one
downloaded image (:mod:`~datamax_dpl.domain.transparency`); the preview draws each such group OR
on one layer, then combines that layer like any other field.

Overlapping fields combine the way the printer's attribute says (manual, ``A`` command, page 65;
the DMX 600/800 default, page 66):
1 XOR prints only dots an odd number of fields share, 2 transparent prints both, 3 opaque lets the
later field whiten the rectangle its dots span (for text, the run of its character cells, or of
its lettering's dots when a mask draws it) first.
Inverse (5) prints white on black, which the preview does not draw; it shows as XOR. A model without
the ``A`` command (``docs/capabilities.md``) always XORs, as an Allegro 2 did on paper. A label
that sets no attribute shows XOR, each format's default; a DMX 600/800 takes its default from the
operator panel instead, which the preview cannot know.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

from .encode_common import position_units
from .encode_graphics import qr_symbol_position
from .enums import Attribute, CombineMode, FillPattern, Rotation, Unit
from .errors import PreviewTooLongError
from .geometry import DotPoint, Rect, Size, Span
from .image_budget import HALVING_FACTOR, halve, magnify
from .label import FONT_9, Barcode, Box, Circle, Element, Image, Label, Line, Polygon, Qr, ScalableText, Text
from .profiles import Feature, get_profile
from .qr import QrDataTooLongError, qr_bitmap
from .raster import Bitmap
from .render_context import RenderOptions
from .units import Axis, to_inches

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .profiles import PrinterProfile
    from .render_context import RenderContext

_DPI_203 = 203
_DPI_300 = 300
_DPI_289 = 289
#: The DMX 430's resolution, the one the third column of the font tables is for.
_DPI_289_56 = 289.56


@dataclass(frozen=True)
class _CharCell:
    """One character of a font in dots: its height, its width and the gap after it."""

    height: int
    width: int
    gap: int


@dataclass(frozen=True)
class _FontCells:
    """A bitmapped font's character cell at each resolution the font tables list."""

    dpi_203: _CharCell
    dpi_300: _CharCell
    dpi_289: _CharCell

    def at(self, dpi: float) -> _CharCell:
        """The cell at ``dpi``: 300 dpi and up, 289.56 dpi (the DMX 430), else 203 dpi."""
        if dpi >= _DPI_300:
            return self.dpi_300
        if dpi >= _DPI_289:
            return self.dpi_289
        return self.dpi_203


#: Character cell (height, width, gap) in dots at 203 / 300 / 289.56 dpi, for fonts 0-8.
_CELLS: Mapping[int, _FontCells] = MappingProxyType(
    {
        0: _FontCells(_CharCell(7, 5, 1), _CharCell(10, 7, 1), _CharCell(10, 7, 1)),
        1: _FontCells(_CharCell(13, 7, 2), _CharCell(19, 10, 3), _CharCell(18, 10, 3)),
        2: _FontCells(_CharCell(18, 10, 2), _CharCell(27, 15, 3), _CharCell(26, 14, 3)),
        3: _FontCells(_CharCell(27, 14, 2), _CharCell(40, 21, 3), _CharCell(39, 20, 3)),
        4: _FontCells(_CharCell(36, 18, 3), _CharCell(53, 27, 4), _CharCell(52, 26, 4)),
        5: _FontCells(_CharCell(52, 18, 3), _CharCell(77, 27, 4), _CharCell(75, 26, 4)),
        6: _FontCells(_CharCell(64, 32, 4), _CharCell(95, 47, 6), _CharCell(92, 46, 6)),
        7: _FontCells(_CharCell(32, 15, 5), _CharCell(47, 22, 7), _CharCell(46, 22, 7)),
        8: _FontCells(_CharCell(28, 15, 5), _CharCell(41, 22, 7), _CharCell(40, 22, 7)),
    }
)
_POINTS_PER_INCH = 72
_MM_PER_INCH = 25.4
#: Average glyph width of a proportional font as a share of its point size.
_PROPORTIONAL_WIDTH = 0.6
#: Estimated modules per character for module barcodes (Code 128 is 11).
_MODULES_PER_CHAR = 11
_DEFAULT_BARCODE_HEIGHT_IN = 0.5
_DEFAULT_MODULE_DOTS = 2
_MARGIN_DOTS = 8
#: A barcode's data is lettered in this share of its height, at the top of the upright field.
_BARCODE_TEXT_SHARE = 4
#: Side of the grid square drawn for an image only the printer holds (no element of the job
#: downloads its name), at magnification 1: its real size is unknown.
STORED_IMAGE_DOTS = 40
_MIN_POINTS = 4
_MAX_POINTS = 72


@dataclass(frozen=True)
class PreviewGeometry:
    """Label lengths to dots on the paper, by the arithmetic the encoder and the printer use.

    A position becomes the DPL row or column its record is written at
    (:func:`~datamax_dpl.domain.encode_common.position_units`), the dots the printer prints for
    those units (:meth:`RenderContext.printed_dots`), and then the paper at nominal size: the
    job's scale (which the encoder divided the length by) and its offset (which it added) are
    undone. A length takes the same way without the offset.

    Attributes:
        ctx: The context the job is encoded with (``job_context``).

    Example:
        >>> from datamax_dpl.domain.label import Label, Text
        >>> from datamax_dpl.domain.render import job_context
        >>> label = Label(unit=Unit.MM, elements=[Text(x=10, y=10, text="x")])
        >>> # 10 mm is 79.9 dots, but the job writes 39/100 in, which prints as 79 dots.
        >>> PreviewGeometry(job_context(label, RenderOptions(model="generic"))).dots(10, axis=Axis.ACROSS)
        79
    """

    ctx: RenderContext

    @property
    def dpi(self) -> float:
        """The printer's resolution."""
        return self.ctx.profile.dpi

    def dots(self, value: float, *, axis: Axis) -> int:
        """Return where a field at label position ``value`` lies on the paper, in dots from the 0 edge."""
        return self.at_units(position_units(value, axis=axis, ctx=self.ctx), axis=axis)

    def at_units(self, units: int, *, axis: Axis) -> int:
        """Return where a record's row or column of ``units`` lies on the paper, in dots from the 0 edge."""
        return round(self._paper(units, axis) - self._offset_dots(axis))

    def units(self, value: float, *, axis: Axis) -> int:
        """Return a label length on ``axis`` in the job's DPL unit, as a record writes it."""
        return self.ctx.dpl_units(value, axis=axis)

    def span(self, units: int, *, axis: Axis) -> int:
        """Return how many dots a record length of ``units`` takes on the paper."""
        return round(self._paper(units, axis))

    def length(self, value: float, *, axis: Axis) -> int:
        """Return how many dots a label length takes on the paper, written as a record writes it."""
        return self.span(self.units(value, axis=axis), axis=axis)

    def _paper(self, units: int, axis: Axis) -> float:
        """The dots ``units`` print as, at the paper's nominal size."""
        return self.ctx.printed_dots(units) * self._scale(axis)

    def _scale(self, axis: Axis) -> float:
        """The scale the encoder divided this axis by; a length in dots is sent unscaled."""
        return 1.0 if self.ctx.unit is Unit.DOTS else self.ctx.correction(axis).scale

    def _offset_dots(self, axis: Axis) -> float:
        """The offset the encoder added on this axis, in nominal dots."""
        return to_inches(self.ctx.shifted(0.0, axis=axis), self.ctx.unit, dpi=self.dpi) * self.dpi


@dataclass(frozen=True)
class Canvas:
    """Size of the preview in dots and where label lengths land on it.

    Attributes:
        width: Pixels across.
        height: Pixels along; row 0 is the label's top edge.
        geometry: The conversion from label lengths to dots.
    """

    width: int
    height: int
    geometry: PreviewGeometry

    @property
    def dpi(self) -> float:
        """The printer's resolution."""
        return self.geometry.dpi

    def length(self, value: float, *, axis: Axis) -> int:
        """Return how many dots a label length on ``axis`` takes on the paper (a size)."""
        return self.geometry.length(value, axis=axis)

    def point(self, x: float, y: float) -> DotPoint:
        """Label point (origin bottom-left) to pixel (origin top-left)."""
        return DotPoint(
            self.geometry.dots(x, axis=Axis.ACROSS), self.height - 1 - self.geometry.dots(y, axis=Axis.ALONG)
        )


@dataclass(frozen=True)
class CellMm:
    """A character cell of a bitmapped font in mm.

    Attributes:
        advance: Cell width plus the gap after it.
        height: Cell height.
    """

    advance: float
    height: float


def widest_cell_mm(font: int) -> CellMm:
    """Return the largest character advance and height in mm of bitmapped font 0-8 at any resolution.

    Text sized with these fits on every printer, since bitmapped fonts are sized in dots.

    Args:
        font: Font number 0-8.

    Returns:
        The largest advance and the largest cell height.

    Example:
        >>> cell = widest_cell_mm(1)
        >>> round(cell.advance, 2), round(cell.height, 2)
        (1.14, 1.63)
    """
    font_cells = _CELLS[font]
    cells = ((font_cells.dpi_203, _DPI_203), (font_cells.dpi_300, _DPI_300), (font_cells.dpi_289, _DPI_289_56))
    advance = max((cell.width + cell.gap) / dpi for cell, dpi in cells)
    height = max(cell.height / dpi for cell, dpi in cells)
    return CellMm(advance=advance * _MM_PER_INCH, height=height * _MM_PER_INCH)


def _text_size(element: Text, dpi: float) -> _CharCell:
    """Return the cell in dots of one character, multiplied as the field asks."""
    if element.font == FONT_9:
        height = round((element.points or 10) * dpi / _POINTS_PER_INCH)
        return _CharCell(height=height, width=round(height * _PROPORTIONAL_WIDTH), gap=0)
    cell = _CELLS[element.font].at(dpi)
    return _CharCell(
        height=cell.height * element.height_multiplier,
        width=cell.width * element.width_multiplier,
        gap=cell.gap * element.width_multiplier,
    )


class UprightPart(NamedTuple):
    """A part of an upright field, as :meth:`FieldArea.part` takes it.

    Attributes:
        along: Where it starts along the reading direction, and its length.
        up: How far it is lifted above the upright field's bottom edge, and its height.
    """

    along: Span
    up: Span


@dataclass(frozen=True)
class FieldArea:
    """Where a text field or barcode lies: its reference point, its upright size and its turn.

    The rule (module docstring) turns the upright field clockwise about the reference point, so
    every part of it (one character cell, the band a barcode's lettering takes) turns with it.

    Attributes:
        x: Column of the reference point, the lower-left pixel of the upright field.
        y: Row of the reference point.
        length: Dots along the reading direction: the text line, the barcode's width.
        height: Dots across it: the character cell, the bar height.
        rotation: How far the field turns clockwise.
    """

    x: int
    y: int
    length: int
    height: int
    rotation: Rotation

    def box(self) -> Rect:
        """Return the whole field, turned.

        Example:
            >>> FieldArea(x=10, y=50, length=30, height=8, rotation=Rotation.DEG_90).box()
            Rect(left=10, top=50, width=8, height=30)
        """
        return self.part()

    def part(self, *, along: Span | None = None, up: Span | None = None) -> Rect:
        """Return a part of the upright field, turned.

        Args:
            along: Where the part starts along the reading direction, and its length; all of it
                when None.
            up: How far the part is lifted above the upright field's bottom edge, and its height;
                all of it when None.

        Returns:
            The part in pixels after the turn.

        Example:
            >>> area = FieldArea(x=10, y=50, length=30, height=8, rotation=Rotation.DEG_180)
            >>> area.part(along=Span(start=0, length=10))
            Rect(left=1, top=50, width=10, height=8)
        """
        start, length = along if along is not None else Span(0, self.length)
        lift, height = up if up is not None else Span(0, self.height)
        x, y = self.x, self.y
        if self.rotation is Rotation.DEG_90:
            return Rect(left=x + lift, top=y + start, width=height, height=length)
        if self.rotation is Rotation.DEG_180:
            return Rect(left=x - start - length + 1, top=y + lift, width=length, height=height)
        if self.rotation is Rotation.DEG_270:
            return Rect(left=x - lift - height + 1, top=y - start - length + 1, width=height, height=length)
        return Rect(left=x + start, top=y - lift - height + 1, width=length, height=height)

    def on_canvas(self, *, width: int, height: int) -> UprightPart | None:
        """Return the part of the upright field on a canvas of that size, None when none of it is.

        Example:
            >>> FieldArea(x=90, y=30, length=30, height=8, rotation=Rotation.DEG_0).on_canvas(width=100, height=40)
            UprightPart(along=Span(start=0, length=10), up=Span(start=0, length=8))
            >>> FieldArea(x=10, y=30, length=30, height=8, rotation=Rotation.DEG_180).on_canvas(width=100, height=40)
            UprightPart(along=Span(start=0, length=11), up=Span(start=0, length=8))
            >>> FieldArea(x=200, y=30, length=30, height=8, rotation=Rotation.DEG_0).on_canvas(width=100, height=40)
        """
        box = self.box()
        left, top = max(box.left, 0), max(box.top, 0)
        width = min(box.left + box.width, width) - left
        height = min(box.top + box.height, height) - top
        if width <= 0 or height <= 0:
            return None
        if self.rotation is Rotation.DEG_90:
            return UprightPart(along=Span(top - self.y, height), up=Span(left - self.x, width))
        if self.rotation is Rotation.DEG_180:
            return UprightPart(along=Span(self.x - left - width + 1, width), up=Span(top - self.y, height))
        if self.rotation is Rotation.DEG_270:
            return UprightPart(along=Span(self.y - top - height + 1, height), up=Span(self.x - left - width + 1, width))
        return UprightPart(along=Span(left - self.x, width), up=Span(self.y - top - height + 1, height))

    def strip(self, *, lift: int, height: int) -> FieldArea:
        """Return the part ``lift`` dots above the upright field's bottom edge, ``height`` tall, as a field.

        It turns about its own lower-left corner exactly as the part does within this field.

        Example:
            >>> area = FieldArea(x=10, y=50, length=30, height=8, rotation=Rotation.DEG_90)
            >>> area.strip(lift=6, height=2).box() == area.part(up=Span(start=6, length=2))
            True
        """
        x, y = {
            Rotation.DEG_0: (self.x, self.y - lift),
            Rotation.DEG_90: (self.x + lift, self.y),
            Rotation.DEG_180: (self.x, self.y + lift),
            Rotation.DEG_270: (self.x - lift, self.y),
        }[self.rotation]
        return FieldArea(x=x, y=y, length=self.length, height=height, rotation=self.rotation)

    def rise(self) -> int:
        """Return how many rows the turned field reaches up from its reference row, that row included."""
        if self.rotation is Rotation.DEG_0:
            return self.height
        if self.rotation is Rotation.DEG_270:
            return self.length
        return 1


def _barcode_size(element: Barcode, canvas: Canvas) -> Size:
    """The barcode's estimated upright size: its width along the bars' run, its bar height."""
    module = element.narrow_dots or _DEFAULT_MODULE_DOTS
    width = len(element.data) * _MODULES_PER_CHAR * module
    if not element.height:
        return Size(width=width, height=round(_DEFAULT_BARCODE_HEIGHT_IN * canvas.dpi))
    # The height runs along the feed unless the field is turned a quarter turn (``encode_barcode``).
    axis = Axis.ACROSS if element.rotation in (Rotation.DEG_90, Rotation.DEG_270) else Axis.ALONG
    return Size(width=width, height=canvas.length(element.height, axis=axis))


def field_area(element: Text | ScalableText | Barcode, canvas: Canvas) -> FieldArea:
    """Return the reference point, upright size and rotation of a text field's line or a barcode."""
    x, y = canvas.point(element.x, element.y)
    if isinstance(element, Barcode):
        size = _barcode_size(element, canvas)
        return FieldArea(x=x, y=y, length=size.width, height=size.height, rotation=element.rotation)
    text = _as_text(element) if isinstance(element, ScalableText) else element
    cell = _text_size(text, canvas.dpi)
    return FieldArea(
        x=x, y=y, length=len(text.text) * (cell.width + cell.gap), height=cell.height, rotation=text.rotation
    )


def mask_area(element: Text | ScalableText | Barcode, canvas: Canvas) -> FieldArea:
    """Return the area a lettering mask for ``element`` covers (``preview_bitmap``'s ``masks``).

    For a text field that is the whole field; for a barcode the top quarter of the upright field
    (at least one row), where its data is lettered over the hatching. That band is not
    print-accurate: a field crossing it on an XOR model combines with the letters' dots there,
    where the printer has bars.
    """
    area = field_area(element, canvas)
    if not isinstance(element, Barcode):
        return area
    band = max(1, area.height // _BARCODE_TEXT_SHARE)
    return area.strip(lift=area.height - band, height=band)


def mask_box(element: Text | ScalableText | Barcode, canvas: Canvas) -> Rect | None:
    """Return where a lettering mask for ``element`` lands: its ``mask_area`` cut to the canvas.

    None when none of that area lands on the canvas, so the field takes no mask. A label may state
    a field far wider than the canvas (255 characters at 24 times the width), and a mask of what is
    never drawn costs as much as the whole field.
    """
    area = mask_area(element, canvas)
    part = area.on_canvas(width=canvas.width, height=canvas.height)
    return None if part is None else area.part(along=part.along, up=part.up)


def text_box(element: Text | ScalableText | Barcode, canvas: Canvas) -> Rect:
    """Return the rectangle in pixels of a text field's whole line or a barcode's area."""
    return field_area(element, canvas).box()


def _draw_text(element: Text, bitmap: Bitmap, canvas: Canvas) -> None:
    """Draw one grey cell per character, advancing from the reference point the way the field turns."""
    area = field_area(element, canvas)
    cell = _text_size(element, canvas.dpi)
    for index, char in enumerate(element.text):
        if not char.isspace():
            along = Span(start=index * (cell.width + cell.gap), length=cell.width)
            bitmap.fill_rect(*area.part(along=along), pattern=FillPattern.SHADE_50)


def _draw_barcode(element: Barcode, bitmap: Bitmap, canvas: Canvas) -> None:
    left, top, w, h = text_box(element, canvas)
    bitmap.fill_rect(left, top, w, h, pattern=FillPattern.RIGHT_DIAGONAL)
    right, bottom = left + w - 1, top + h - 1
    bitmap.draw_polygon([DotPoint(left, top), DotPoint(right, top), DotPoint(right, bottom), DotPoint(left, bottom)])


def _draw_masked(element: Text | ScalableText | Barcode, mask: Bitmap, bitmap: Bitmap, canvas: Canvas) -> None:
    """Draw a text field as its mask's dots, or a barcode hatched with its band whitened under its mask."""
    box = mask_box(element, canvas)
    if box is None:
        return
    if isinstance(element, Barcode):
        _draw_barcode(element, bitmap, canvas)
        bitmap.combine(mask, box.left, box.top, mode=CombineMode.OPAQUE)
    else:
        bitmap.blit(mask, x=box.left, y=box.top)


def _draw_box(element: Box, bitmap: Bitmap, canvas: Canvas) -> None:
    """Draw a box as its box record prints it: each side's thickness at least one DPL unit."""
    geometry = canvas.geometry
    left, bottom = canvas.point(element.x, element.y)
    width, height = canvas.length(element.width, axis=Axis.ACROSS), canvas.length(element.height, axis=Axis.ALONG)
    top = bottom - height + 1
    if element.fill is not FillPattern.NONE:
        bitmap.fill_rect(left, top, width, height, pattern=element.fill)
    edge = geometry.span(geometry.units(element.thickness, axis=Axis.ALONG) or 1, axis=Axis.ALONG)
    side_units = geometry.units(element.side_thickness or element.thickness, axis=Axis.ACROSS) or 1
    side = geometry.span(side_units, axis=Axis.ACROSS)
    for rect in ((left, top, width, edge), (left, bottom - edge + 1, width, edge)):
        bitmap.fill_rect(*rect)
    for rect in ((left, top, side, height), (left + width - side, top, side, height)):
        bitmap.fill_rect(*rect)


def _draw_axis_line(element: Line, bitmap: Bitmap, canvas: Canvas) -> None:
    """Draw a horizontal or vertical line as the encoder's line record places it.

    The record's corner is the lower-left end (the smaller x and y); the length runs right or up from
    there and the thickness up or right, never centred on the line. A line of no length is a square
    of its thickness, as in ``encode_line``.
    """
    geometry = canvas.geometry
    horizontal = element.y == element.y2 and element.x != element.x2
    thickness_axis, length_axis = (Axis.ALONG, Axis.ACROSS) if horizontal else (Axis.ACROSS, Axis.ALONG)
    length = abs(element.x2 - element.x) if horizontal else abs(element.y2 - element.y)
    # In DPL units, as the record holds them: at least one unit thick, a point as long as thick.
    thickness = geometry.units(element.thickness, axis=thickness_axis) or 1
    extent = geometry.units(length, axis=length_axis) or thickness
    across, along = (extent, thickness) if horizontal else (thickness, extent)
    width, height = geometry.span(across, axis=Axis.ACROSS), geometry.span(along, axis=Axis.ALONG)
    left, bottom = canvas.point(min(element.x, element.x2), min(element.y, element.y2))
    bitmap.fill_rect(left, bottom - height + 1, width, height)


def _draw_graphic(element: Line | Circle | Polygon, bitmap: Bitmap, canvas: Canvas) -> None:
    if isinstance(element, Line) and element.is_axis_aligned:
        _draw_axis_line(element, bitmap, canvas)
    elif isinstance(element, Line):
        x0, y0 = canvas.point(element.x, element.y)
        x1, y1 = canvas.point(element.x2, element.y2)
        bitmap.draw_line(x0, y0, x1, y1, thickness=max(1, canvas.length(element.thickness, axis=Axis.ACROSS)))
    elif isinstance(element, Circle):
        cx, cy = canvas.point(element.x, element.y)
        geometry = canvas.geometry
        radius = geometry.span(geometry.units(element.radius, axis=Axis.ACROSS) or 1, axis=Axis.ACROSS)
        bitmap.draw_circle(cx, cy, radius, fill=element.fill)
    else:
        bitmap.draw_polygon([canvas.point(p.x, p.y) for p in element.points], fill=element.fill)


def _draw_image(element: Image, bitmap: Bitmap, canvas: Canvas, *, halved: bool = False) -> None:
    """Draw the image as the printer prints it: magnified, and at half resolution when halved.

    A stored-only reference to a name the job downloads prints that download, so it is drawn as
    that bitmap (``RenderContext.image_rows``); only an image the printer alone holds is a grid.
    """
    x, y = canvas.point(element.x, element.y)
    rows = canvas.geometry.ctx.image_rows(element)
    if rows is None:
        size = STORED_IMAGE_DOTS * element.magnification
        bitmap.fill_rect(x, y - size + 1, size, size, pattern=FillPattern.GRID)
        return
    picture = Bitmap.from_rows(rows)
    factor = element.magnification
    if halved:
        picture, factor = halve(picture), factor * HALVING_FACTOR
    picture = magnify(picture, factor)
    bitmap.blit(picture, x=x, y=y - picture.height + 1)


def _draw_halved_graphic(element: Element, bitmap: Bitmap, canvas: Canvas) -> None:
    """Draw a graphic the printer gets as a halved image: its dots in 2x2 blocks."""
    layer = Bitmap(canvas.width, canvas.height)
    draw_element(element, layer, canvas)
    _blit_halved(layer, bitmap, canvas)


def _blit_halved(layer: Bitmap, bitmap: Bitmap, canvas: Canvas) -> None:
    """Put ``layer``'s dots onto ``bitmap`` in 2x2 blocks, as the printer prints them halved.

    The layer is cut at its lowest row and leftmost column (where the encoder's image starts),
    halved and doubled there. The blocks line up with the printed ones as far as the preview's
    drawing matches the encoder's raster, to a dot or so.
    """
    area = layer.bounds()
    if area is None:
        return
    left, top, _, height = area
    bottom = top + height - 1
    cut = Bitmap(canvas.width - left, bottom + 1)
    cut.blit(layer, x=-left, y=0)
    picture = magnify(halve(cut), HALVING_FACTOR)
    bitmap.blit(picture, x=left, y=bottom - picture.height + 1)


def _qr_symbol(element: Qr) -> Bitmap | None:
    """The QR Code's module matrix, or None when the data fits no version (the render refuses it)."""
    try:
        return qr_bitmap(element.data, error=element.error)
    except QrDataTooLongError:
        return None


def _draw_qr(element: Qr, bitmap: Bitmap, canvas: Canvas) -> None:
    """Draw each dark module ``module_dots`` square where ``encode_qr`` places the symbol."""
    symbol = _qr_symbol(element)
    if symbol is None:
        return
    geometry = canvas.geometry
    symbol_at = qr_symbol_position(
        element,
        column=position_units(element.x, axis=Axis.ACROSS, ctx=geometry.ctx),
        row=position_units(element.y, axis=Axis.ALONG, ctx=geometry.ctx),
        ctx=geometry.ctx,
    )
    size = element.module_dots
    left = geometry.at_units(symbol_at.column, axis=Axis.ACROSS)
    top = canvas.height - 1 - geometry.at_units(symbol_at.row, axis=Axis.ALONG) - symbol.height * size + 1
    for row in range(symbol.height):
        for column in range(symbol.width):
            if symbol.get(column, row):
                bitmap.fill_rect(left + column * size, top + row * size, size, size)


def _as_text(element: ScalableText) -> Text:
    points = round(min(max(element.height_points, _MIN_POINTS), _MAX_POINTS))
    return Text(x=element.x, y=element.y, rotation=element.rotation, text=element.text, font=FONT_9, points=points)


def draw_element(element: Element, bitmap: Bitmap, canvas: Canvas) -> None:
    """Draw one element onto ``bitmap``."""
    if isinstance(element, ScalableText):
        element = _as_text(element)
    if isinstance(element, Text):
        _draw_text(element, bitmap, canvas)
    elif isinstance(element, Barcode):
        _draw_barcode(element, bitmap, canvas)
    elif isinstance(element, Box):
        _draw_box(element, bitmap, canvas)
    elif isinstance(element, Image):
        _draw_image(element, bitmap, canvas)
    elif isinstance(element, Qr):
        _draw_qr(element, bitmap, canvas)
    else:
        _draw_graphic(element, bitmap, canvas)


#: The longest side a preview draws unless told otherwise: about 16000 dot rows at 203 dpi.
DEFAULT_PREVIEW_MAX_LENGTH_MM = 2000.0


def canvas_for(label: Label, profile: PrinterProfile, *, options: RenderOptions | None = None) -> Canvas:
    """Size the preview: the label size when given, else the print width and the tallest field.

    Args:
        label: The label.
        profile: The model.
        options: The options the job is rendered with; None renders for ``profile`` alone. Their
            scale, offset and unit decide where every field lands.

    Raises:
        ValueError: ``options`` are for another model than ``profile``.
    """
    return _canvas(label, PreviewGeometry(_job_context(label, profile, options)))


def _job_context(label: Label, profile: PrinterProfile, options: RenderOptions | None) -> RenderContext:
    """The context the job for ``label`` is encoded with, for ``profile``."""
    # render imports SentAs from here, and its transparency step reaches Canvas through overlap.
    from .render import job_context  # noqa: PLC0415 - import cycle: render imports this module

    chosen = options or RenderOptions(model=profile.id)
    if get_profile(chosen.model).id != profile.id:
        msg = f"options are for {chosen.model}, not {profile.id}"
        raise ValueError(msg)
    return job_context(label, chosen)


def check_preview_length(canvas: Canvas, *, max_length_mm: float) -> None:
    """Refuse a preview whose canvas would be longer than ``max_length_mm`` either way.

    The canvas is only a size at this point: one number in a label decides how much memory the
    picture takes (100000 mm took 118 s and 1.47 GB), so this runs before anything is drawn.

    Args:
        canvas: The preview's size, from :func:`canvas_for`.
        max_length_mm: The longest side allowed, in millimetres.

    Raises:
        PreviewTooLongError: A side is longer than the limit.
        ValueError: ``max_length_mm`` is not a finite length in dots.

    Example:
        >>> from datamax_dpl.domain.label import Label, Text
        >>> from datamax_dpl.domain.profiles import get_profile
        >>> label = Label(height=30, width=30, elements=[Text(x=1, y=1, text="A")])
        >>> check_preview_length(canvas_for(label, get_profile("dmx-600")), max_length_mm=30)
    """
    dpi = canvas.geometry.dpi
    # In dots, rounded as _canvas rounds a label's size: a label of exactly the limit passes.
    try:
        limit_dots = max_length_mm / _MM_PER_INCH * dpi
        limit = round(limit_dots)
    except (OverflowError, ValueError) as exc:
        # round() refuses both infinity and NaN, and a huge int fails on the way to a float.
        msg = f"max_length_mm must be a finite length, got {max_length_mm!r}"
        raise ValueError(msg) from exc
    # Each side holds one dot more than its length: the frame's far edge.
    longest = max(canvas.width, canvas.height) - 1
    if longest > limit:
        msg = f"the preview would be {longest / dpi * _MM_PER_INCH:.0f} mm long, past the {max_length_mm:g} mm it draws"
        raise PreviewTooLongError(msg)


def _canvas(label: Label, geometry: PreviewGeometry) -> Canvas:
    dpi = geometry.dpi
    width_in = to_inches(label.width, label.unit, dpi=dpi) if label.width else geometry.ctx.profile.print_width_in
    if label.height:
        height = round(to_inches(label.height, label.unit, dpi=dpi) * dpi)
    else:
        height = max(_top_dots(element, geometry) for element in label.elements) + _MARGIN_DOTS
    return Canvas(width=round(width_in * dpi) + 1, height=height + 1, geometry=geometry)


def _field_top(element: Text | ScalableText | Barcode, geometry: PreviewGeometry) -> int:
    """Highest row of a text field or barcode, turned, in dots above the label bottom."""
    # Only the sizes are read here; a one-pixel canvas converts lengths like the real one.
    area = field_area(element, Canvas(width=1, height=1, geometry=geometry))
    return geometry.dots(element.y, axis=Axis.ALONG) + area.rise()


def _image_top(element: Image, geometry: PreviewGeometry) -> int:
    rows = geometry.ctx.image_rows(element)
    height = STORED_IMAGE_DOTS if rows is None else len(rows)
    return geometry.dots(element.y, axis=Axis.ALONG) + height * element.magnification


def _qr_top(element: Qr, geometry: PreviewGeometry) -> int:
    symbol = _qr_symbol(element)
    modules = (symbol.height if symbol is not None else 0) + 2 * element.quiet_zone
    return geometry.dots(element.y, axis=Axis.ALONG) + modules * element.module_dots


def _graphic_top(element: Element) -> float:
    """Highest point of a box, circle, line or polygon, in label units."""
    if isinstance(element, Box):
        return element.y + element.height
    if isinstance(element, Circle):
        return element.y + element.radius
    if isinstance(element, Line):
        return max(element.y, element.y2) + element.thickness
    if isinstance(element, Polygon):
        return max(p.y for p in element.points)
    return 0.0


def _top_dots(element: Element, geometry: PreviewGeometry) -> int:
    """Highest point of ``element`` above the label bottom, in dots."""
    if isinstance(element, (Text, ScalableText, Barcode)):
        return _field_top(element, geometry)
    if isinstance(element, Image):
        return _image_top(element, geometry)
    if isinstance(element, Qr):
        return _qr_top(element, geometry)
    return geometry.dots(_graphic_top(element), axis=Axis.ALONG)


#: The combine mode of each attribute. Inverse (5) prints a field white on black, which the
#: preview does not draw, so it shows as XOR.
_ATTRIBUTE_MODES: Mapping[Attribute, CombineMode] = MappingProxyType(
    {
        Attribute.XOR: CombineMode.XOR,
        Attribute.TRANSPARENT: CombineMode.OR,
        Attribute.OPAQUE: CombineMode.OPAQUE,
        Attribute.INVERSE: CombineMode.XOR,
    }
)


def _has_attributes(profile: PrinterProfile) -> bool:
    """Whether the model takes the ``A`` command whatever its firmware and options."""
    support = profile.support(Feature.ATTRIBUTE)
    return support.available and support.min_firmware is None and support.option is None


def _combine_mode(element: Element, label: Label) -> CombineMode:
    """The mode a field prints in on a model with attributes: its own, the label's, else XOR."""
    own = element.attribute if isinstance(element, (Text, ScalableText)) else None
    attribute = own or label.attribute or Attribute.XOR
    return _ATTRIBUTE_MODES[attribute]


def _draw_layer(element: Element, layer: Bitmap, canvas: Canvas, *, halved: bool, mask: Bitmap | None) -> None:
    """Draw one element on its own layer: by its mask when one is given, in 2x2 blocks when halved."""
    if mask is not None and isinstance(element, (Text, ScalableText, Barcode)):
        _draw_masked(element, mask, layer, canvas)
    elif not halved:
        draw_element(element, layer, canvas)
    elif isinstance(element, Image):
        _draw_image(element, layer, canvas, halved=True)
    else:
        _draw_halved_graphic(element, layer, canvas)


def _combine_layer(bitmap: Bitmap, layer: Bitmap, *, mode: CombineMode) -> None:
    """Combine the part of ``layer`` holding dots into ``bitmap``; a blank layer changes nothing.

    Cutting the layer to its black dots keeps the combine as small as the field, and gives OPAQUE
    the field's rectangle (for text, its character cells) to whiten.
    """
    area = layer.bounds()
    if area is None:
        return
    left, top, width, height = area
    cut = Bitmap(width, height)
    cut.blit(layer, x=-left, y=-top)
    bitmap.combine(cut, left, top, mode=mode)


@dataclass(frozen=True)
class SentAs:
    """How a job sends fields where that changes what they print (``RenderResult.sent_as``).

    Attributes:
        halved: Elements sent at half resolution, which print in 2x2 blocks.
        merged: Groups of elements drawn together OR into one image (an emulated transparent label).

    Example:
        >>> SentAs().halved, SentAs().merged
        (frozenset(), ())
    """

    halved: frozenset[int] = frozenset()
    merged: tuple[tuple[int, ...], ...] = ()


def _draw_group(group: tuple[int, ...], label: Label, layer: Bitmap, canvas: Canvas, *, halved: bool) -> None:
    """Draw a merged group OR onto ``layer``, in 2x2 blocks when its image is halved."""
    drawn = Bitmap(canvas.width, canvas.height) if halved else layer
    for index in group:
        draw_element(label.elements[index], drawn, canvas)
    if halved:
        _blit_halved(drawn, layer, canvas)


def _check_masks(label: Label, canvas: Canvas, masks: Mapping[int, Bitmap]) -> None:
    """Refuse a mask for an element that takes none, or of another size than the area it covers."""
    fields = {i: e for i, e in enumerate(label.elements) if isinstance(e, (Text, ScalableText, Barcode))}
    for index, mask in masks.items():
        element = fields.get(index)
        if element is None:
            msg = f"element {index} is not a text field or barcode, so it takes no mask"
            raise ValueError(msg)
        box = mask_box(element, canvas)
        if box is None:
            msg = f"element {index} lies off the canvas, so it takes no mask"
            raise ValueError(msg)
        if (mask.width, mask.height) != (box.width, box.height):
            msg = f"mask for element {index} is {mask.width}x{mask.height}, its area {box.width}x{box.height}"
            raise ValueError(msg)


def check_preview_fields(label: Label, profile: PrinterProfile, *, sent_as: SentAs | None = None) -> None:
    """Refuse a picture of more fields than the model's format takes, before any is lettered or drawn.

    The encoder stops at the same limit; every field drawn here costs a layer of the whole canvas.
    A merged group counts as the one field the job sends it as.

    Raises:
        PreviewTooLongError: The job would send more fields than the model's format takes.
    """
    sent = sent_as or SentAs()
    merged = sum(len(group) for group in sent.merged)
    fields = len(label.elements) - merged + len(sent.merged)
    if fields > profile.max_fields:
        msg = f"preview of {fields} fields: {profile.name} takes at most {profile.max_fields}; nothing was drawn"
        raise PreviewTooLongError(msg)


def preview_bitmap(
    label: Label,
    profile: PrinterProfile,
    *,
    sent_as: SentAs | None = None,
    options: RenderOptions | None = None,
    masks: Mapping[int, Bitmap] | None = None,
) -> Bitmap:
    """Draw ``label`` for ``profile`` with a one-dot frame marking the label (or print) edge.

    Args:
        label: The label.
        profile: The model; decides the resolution and the print width.
        sent_as: How the job sends its fields (``RenderResult.sent_as``): halved elements are
            drawn in 2x2 blocks, and each merged group OR on one layer. None draws every field
            as the label states it.
        options: The options the job is rendered with, so that the picture places each field at
            the row and column that job writes; None renders for ``profile`` alone.
        masks: Lettering per element index, each the size of its ``mask_box``: a text
            field is drawn as its mask's dots instead of grey cells, a barcode's mask letters
            the top band of its hatched area. Combined like the field, so a field crossing the
            lettering gaps only under its dots. None draws every text field as cells.

    Returns:
        The picture, one pixel per printer dot. Each element (or merged group, at its first
        element) is drawn on its own layer and combined in the mode it prints in (module
        docstring): XOR on a model without the ``A`` command, else the field's or the label's
        attribute.

    Raises:
        PreviewTooLongError: The job would send more fields than the model's format takes.
        ValueError: ``options`` are for another model than ``profile``; a mask is for an element
            that is neither a text field nor a barcode, or is not the size of its area.
    """
    sent = sent_as or SentAs()
    groups = {group[0]: group for group in sent.merged}
    merged = {index for group in sent.merged for index in group}
    check_preview_fields(label, profile, sent_as=sent)
    canvas = canvas_for(label, profile, options=options)
    lettering = masks or {}
    _check_masks(label, canvas, lettering)
    bitmap = Bitmap(canvas.width, canvas.height)
    attributes = _has_attributes(profile)
    for index, element in enumerate(label.elements):
        if index in merged and index not in groups:
            continue
        layer = Bitmap(canvas.width, canvas.height)
        if index in groups:
            _draw_group(groups[index], label, layer, canvas, halved=not sent.halved.isdisjoint(groups[index]))
        else:
            _draw_layer(element, layer, canvas, halved=index in sent.halved, mask=lettering.get(index))
        mode = _combine_mode(element, label) if attributes else CombineMode.XOR
        _combine_layer(bitmap, layer, mode=mode)
    # The frame is not printed, so it goes on last and no field's XOR can erase it.
    right, bottom = canvas.width - 1, canvas.height - 1
    bitmap.draw_polygon([DotPoint(0, 0), DotPoint(right, 0), DotPoint(right, bottom), DotPoint(0, bottom)])
    return bitmap


__all__ = [
    "DEFAULT_PREVIEW_MAX_LENGTH_MM",
    "STORED_IMAGE_DOTS",
    "Canvas",
    "CellMm",
    "FieldArea",
    "PreviewGeometry",
    "SentAs",
    "canvas_for",
    "check_preview_fields",
    "check_preview_length",
    "draw_element",
    "field_area",
    "mask_area",
    "mask_box",
    "preview_bitmap",
    "text_box",
    "widest_cell_mm",
]
