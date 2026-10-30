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
as a mask (``preview_bitmap``'s ``masks``), and the camera photo of a real print is the final check.
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
from typing import TYPE_CHECKING

from .encode_common import position_units
from .encode_graphics import qr_symbol_position
from .enums import Attribute, CombineMode, FillPattern, Rotation, Unit
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

#: Character cell (height, width, gap) in dots at 203 / 300 / 289.56 dpi, for fonts 0-8.
_CELLS: dict[int, tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = {
    0: ((7, 5, 1), (10, 7, 1), (10, 7, 1)),
    1: ((13, 7, 2), (19, 10, 3), (18, 10, 3)),
    2: ((18, 10, 2), (27, 15, 3), (26, 14, 3)),
    3: ((27, 14, 2), (40, 21, 3), (39, 20, 3)),
    4: ((36, 18, 3), (53, 27, 4), (52, 26, 4)),
    5: ((52, 18, 3), (77, 27, 4), (75, 26, 4)),
    6: ((64, 32, 4), (95, 47, 6), (92, 46, 6)),
    7: ((32, 15, 5), (47, 22, 7), (46, 22, 7)),
    8: ((28, 15, 5), (41, 22, 7), (40, 22, 7)),
}
_DPI_300 = 300
_DPI_289 = 289
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

    def point(self, x: float, y: float) -> tuple[int, int]:
        """Label point (origin bottom-left) to pixel (origin top-left)."""
        return self.geometry.dots(x, axis=Axis.ACROSS), self.height - 1 - self.geometry.dots(y, axis=Axis.ALONG)


def _cell(font: int, dpi: float) -> tuple[int, int, int]:
    column = 1 if dpi >= _DPI_300 else 2 if dpi >= _DPI_289 else 0
    return _CELLS[font][column]


def widest_cell_mm(font: int) -> tuple[float, float]:
    """Return the largest character advance and height in mm of bitmapped font 0-8 at any resolution.

    Text sized with these fits on every printer, since bitmapped fonts are sized in dots.

    Args:
        font: Font number 0-8.

    Returns:
        The advance (cell width plus gap) and the cell height, in mm.

    Example:
        >>> [round(mm, 2) for mm in widest_cell_mm(1)]
        [1.14, 1.63]
    """
    resolutions = (203, _DPI_300, 289.56)
    cells = [(cell, dpi) for cell, dpi in zip(_CELLS[font], resolutions, strict=True)]
    advance = max((width + gap) / dpi for (_, width, gap), dpi in cells)
    height = max(height / dpi for (height, _, _), dpi in cells)
    return advance * _MM_PER_INCH, height * _MM_PER_INCH


def _text_size(element: Text, dpi: float) -> tuple[int, int, int]:
    """Return (height, cell width, gap) in dots of one character."""
    if element.font == FONT_9:
        height = round((element.points or 10) * dpi / _POINTS_PER_INCH)
        return height, round(height * _PROPORTIONAL_WIDTH), 0
    height, width, gap = _cell(element.font, dpi)
    return height * element.height_multiplier, width * element.width_multiplier, gap * element.width_multiplier


#: A rectangle in pixels: (left, top, width, height).
Rect = tuple[int, int, int, int]


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
            (10, 50, 8, 30)
        """
        return self.part()

    def part(self, *, along: tuple[int, int] | None = None, up: tuple[int, int] | None = None) -> Rect:
        """Return a part of the upright field, turned.

        Args:
            along: ``(start, length)`` of the part along the reading direction; all of it when None.
            up: ``(lift, height)`` of the part above the upright field's bottom edge; all of it
                when None.

        Returns:
            The part's (left, top, width, height) in pixels after the turn.

        Example:
            >>> area = FieldArea(x=10, y=50, length=30, height=8, rotation=Rotation.DEG_180)
            >>> area.part(along=(0, 10))
            (1, 50, 10, 8)
        """
        start, length = along if along is not None else (0, self.length)
        lift, height = up if up is not None else (0, self.height)
        x, y = self.x, self.y
        if self.rotation is Rotation.DEG_90:
            return x + lift, y + start, height, length
        if self.rotation is Rotation.DEG_180:
            return x - start - length + 1, y + lift, length, height
        if self.rotation is Rotation.DEG_270:
            return x - lift - height + 1, y - start - length + 1, height, length
        return x + start, y - lift - height + 1, length, height

    def strip(self, *, lift: int, height: int) -> FieldArea:
        """Return the part ``lift`` dots above the upright field's bottom edge, ``height`` tall, as a field.

        It turns about its own lower-left corner exactly as the part does within this field.

        Example:
            >>> area = FieldArea(x=10, y=50, length=30, height=8, rotation=Rotation.DEG_90)
            >>> area.strip(lift=6, height=2).box() == area.part(up=(6, 2))
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


def _barcode_size(element: Barcode, canvas: Canvas) -> tuple[int, int]:
    module = element.narrow_dots or _DEFAULT_MODULE_DOTS
    width = len(element.data) * _MODULES_PER_CHAR * module
    if not element.height:
        return width, round(_DEFAULT_BARCODE_HEIGHT_IN * canvas.dpi)
    # The height runs along the feed unless the field is turned a quarter turn (``encode_barcode``).
    axis = Axis.ACROSS if element.rotation in (Rotation.DEG_90, Rotation.DEG_270) else Axis.ALONG
    return width, canvas.length(element.height, axis=axis)


def field_area(element: Text | ScalableText | Barcode, canvas: Canvas) -> FieldArea:
    """Return the reference point, upright size and rotation of a text field's line or a barcode."""
    x, y = canvas.point(element.x, element.y)
    if isinstance(element, Barcode):
        length, height = _barcode_size(element, canvas)
        return FieldArea(x=x, y=y, length=length, height=height, rotation=element.rotation)
    text = _as_text(element) if isinstance(element, ScalableText) else element
    height, cell, gap = _text_size(text, canvas.dpi)
    return FieldArea(x=x, y=y, length=len(text.text) * (cell + gap), height=height, rotation=text.rotation)


def mask_area(element: Text | ScalableText | Barcode, canvas: Canvas) -> FieldArea:
    """Return the area a lettering mask for ``element`` covers (``preview_bitmap``'s ``masks``).

    For a text field that is the whole field; for a barcode the top quarter of the upright field
    (at least one row), where its data is lettered over the hatching.
    """
    area = field_area(element, canvas)
    if not isinstance(element, Barcode):
        return area
    band = max(1, area.height // _BARCODE_TEXT_SHARE)
    return area.strip(lift=area.height - band, height=band)


def text_box(element: Text | ScalableText | Barcode, canvas: Canvas) -> Rect:
    """Return (left, top, width, height) in pixels of a text field's whole line or a barcode's area."""
    return field_area(element, canvas).box()


def _draw_text(element: Text, bitmap: Bitmap, canvas: Canvas) -> None:
    """Draw one grey cell per character, advancing from the reference point the way the field turns."""
    area = field_area(element, canvas)
    _, cell, gap = _text_size(element, canvas.dpi)
    for index, char in enumerate(element.text):
        if not char.isspace():
            bitmap.fill_rect(*area.part(along=(index * (cell + gap), cell)), pattern=FillPattern.SHADE_50)


def _draw_barcode(element: Barcode, bitmap: Bitmap, canvas: Canvas) -> None:
    left, top, w, h = text_box(element, canvas)
    bitmap.fill_rect(left, top, w, h, pattern=FillPattern.RIGHT_DIAGONAL)
    bitmap.draw_polygon([(left, top), (left + w - 1, top), (left + w - 1, top + h - 1), (left, top + h - 1)])


def _draw_masked(element: Text | ScalableText | Barcode, mask: Bitmap, bitmap: Bitmap, canvas: Canvas) -> None:
    """Draw a text field as its mask's dots, or a barcode hatched with its band whitened under its mask."""
    left, top, _, _ = mask_area(element, canvas).box()
    if isinstance(element, Barcode):
        _draw_barcode(element, bitmap, canvas)
        bitmap.combine(mask, left, top, mode=CombineMode.OPAQUE)
    else:
        bitmap.blit(mask, x=left, y=top)


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
    column, row = qr_symbol_position(
        element,
        column=position_units(element.x, axis=Axis.ACROSS, ctx=geometry.ctx),
        row=position_units(element.y, axis=Axis.ALONG, ctx=geometry.ctx),
        ctx=geometry.ctx,
    )
    size = element.module_dots
    left = geometry.at_units(column, axis=Axis.ACROSS)
    top = canvas.height - 1 - geometry.at_units(row, axis=Axis.ALONG) - symbol.height * size + 1
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


#: The combine mode of each attribute that has one. Inverse (5) prints a field white on black,
#: which the preview does not draw, so it has no mode and shows as XOR.
_ATTRIBUTE_MODES: dict[Attribute, CombineMode] = {
    Attribute.XOR: CombineMode.XOR,
    Attribute.TRANSPARENT: CombineMode.OR,
    Attribute.OPAQUE: CombineMode.OPAQUE,
}


def _has_attributes(profile: PrinterProfile) -> bool:
    """Whether the model takes the ``A`` command whatever its firmware and options."""
    support = profile.support(Feature.ATTRIBUTE)
    return support.available and support.min_firmware is None and support.option is None


def _combine_mode(element: Element, label: Label) -> CombineMode:
    """The mode a field prints in on a model with attributes: its own, the label's, else XOR."""
    own = element.attribute if isinstance(element, (Text, ScalableText)) else None
    attribute = own or label.attribute or Attribute.XOR
    return _ATTRIBUTE_MODES.get(attribute, CombineMode.XOR)


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
        _, _, width, height = mask_area(element, canvas).box()
        if (mask.width, mask.height) != (width, height):
            msg = f"mask for element {index} is {mask.width}x{mask.height}, its area {width}x{height}"
            raise ValueError(msg)


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
        masks: Lettering per element index, each the size of its ``mask_area`` box: a text
            field is drawn as its mask's dots instead of grey cells, a barcode's mask letters
            the top band of its hatched area. Combined like the field, so a field crossing the
            lettering gaps only under its dots. None draws every text field as cells.

    Returns:
        The picture, one pixel per printer dot. Each element (or merged group, at its first
        element) is drawn on its own layer and combined in the mode it prints in (module
        docstring): XOR on a model without the ``A`` command, else the field's or the label's
        attribute.

    Raises:
        ValueError: ``options`` are for another model than ``profile``; a mask is for an element
            that is neither a text field nor a barcode, or is not the size of its area.
    """
    sent = sent_as or SentAs()
    groups = {group[0]: group for group in sent.merged}
    merged = {index for group in sent.merged for index in group}
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
    bitmap.draw_polygon([(0, 0), (canvas.width - 1, 0), (canvas.width - 1, canvas.height - 1), (0, canvas.height - 1)])
    return bitmap


__all__ = [
    "STORED_IMAGE_DOTS",
    "Canvas",
    "FieldArea",
    "PreviewGeometry",
    "Rect",
    "SentAs",
    "canvas_for",
    "draw_element",
    "field_area",
    "mask_area",
    "preview_bitmap",
    "text_box",
    "widest_cell_mm",
]
