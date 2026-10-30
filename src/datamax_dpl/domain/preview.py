"""Approximate picture of a label at the printer's resolution (one pixel per printer dot).

Graphics (lines, boxes, circles, polygons, images, QR Codes) are drawn exactly as the encoder places them.
Text is drawn as one grey block per character cell, sized from the font tables in
``docs/dpl-reference.md`` section 6; barcodes as a hatched area of their estimated size. The
picture answers "does everything sit where I meant and fit the label", not "what does the text
look like": the optional Pillow renderer in ``adapters/preview`` adds real lettering, and the
camera photo of a real print is the final check.

Rotation is approximated by turning each field's box about its reference point; the direction
the manual's rotations turn is unverified until a test print.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .enums import FillPattern, Rotation, Unit
from .image_budget import HALVING_FACTOR, halve, magnify
from .label import FONT_9, Barcode, Box, Circle, Element, Image, Label, Line, Polygon, Qr, ScalableText, Text
from .qr import QrDataTooLongError, qr_bitmap
from .raster import Bitmap
from .units import to_inches

if TYPE_CHECKING:
    from .profiles import PrinterProfile

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
#: Side of the grid square drawn for an image that is stored on the printer, not in the label.
_PLACEHOLDER_IMAGE_DOTS = 40
_MIN_POINTS = 4
_MAX_POINTS = 72


@dataclass(frozen=True)
class Canvas:
    """Size of the preview in dots and the conversion from label units."""

    width: int
    height: int
    dpi: float
    unit: Unit

    def dots(self, value: float) -> int:
        """Label length to whole dots."""
        return round(to_inches(value, self.unit, dpi=self.dpi) * self.dpi)

    def point(self, x: float, y: float) -> tuple[int, int]:
        """Label point (origin bottom-left) to pixel (origin top-left)."""
        return self.dots(x), self.height - 1 - self.dots(y)


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


def _rotated_box(x: int, y: int, width: int, height: int, rotation: Rotation) -> tuple[int, int, int, int]:
    """Return (left, top, width, height) in pixels of a field whose bottom-left pixel is (x, y)."""
    if rotation in (Rotation.DEG_90, Rotation.DEG_270):
        width, height = height, width
    return x, y - height + 1, width, height


def _barcode_size(element: Barcode, canvas: Canvas) -> tuple[int, int]:
    module = element.narrow_dots or _DEFAULT_MODULE_DOTS
    width = len(element.data) * _MODULES_PER_CHAR * module
    height = canvas.dots(element.height) if element.height else round(_DEFAULT_BARCODE_HEIGHT_IN * canvas.dpi)
    return width, height


def text_box(element: Text | ScalableText | Barcode, canvas: Canvas) -> tuple[int, int, int, int]:
    """Return (left, top, width, height) in pixels of a text field's whole line or a barcode's area."""
    x, y = canvas.point(element.x, element.y)
    if isinstance(element, Barcode):
        width, height = _barcode_size(element, canvas)
        return _rotated_box(x, y, width, height, element.rotation)
    text = _as_text(element) if isinstance(element, ScalableText) else element
    height, cell, gap = _text_size(text, canvas.dpi)
    return _rotated_box(x, y, len(text.text) * (cell + gap), height, text.rotation)


def _draw_text(element: Text, bitmap: Bitmap, canvas: Canvas) -> None:
    height, cell, gap = _text_size(element, canvas.dpi)
    x, y = canvas.point(element.x, element.y)
    step = cell + gap
    for index, char in enumerate(element.text):
        if char.isspace():
            continue
        left, top, w, h = _rotated_box(x + index * step, y, cell, height, element.rotation)
        bitmap.fill_rect(left, top, w, h, pattern=FillPattern.SHADE_50)


def _draw_barcode(element: Barcode, bitmap: Bitmap, canvas: Canvas) -> None:
    left, top, w, h = text_box(element, canvas)
    bitmap.fill_rect(left, top, w, h, pattern=FillPattern.RIGHT_DIAGONAL)
    bitmap.draw_polygon([(left, top), (left + w - 1, top), (left + w - 1, top + h - 1), (left, top + h - 1)])


def _draw_box(element: Box, bitmap: Bitmap, canvas: Canvas) -> None:
    left, bottom = canvas.point(element.x, element.y)
    width, height = canvas.dots(element.width), canvas.dots(element.height)
    top = bottom - height + 1
    if element.fill is not FillPattern.NONE:
        bitmap.fill_rect(left, top, width, height, pattern=element.fill)
    edge = max(1, canvas.dots(element.thickness))
    side = max(1, canvas.dots(element.side_thickness or element.thickness))
    for rect in ((left, top, width, edge), (left, bottom - edge + 1, width, edge)):
        bitmap.fill_rect(*rect)
    for rect in ((left, top, side, height), (left + width - side, top, side, height)):
        bitmap.fill_rect(*rect)


def _draw_graphic(element: Line | Circle | Polygon, bitmap: Bitmap, canvas: Canvas) -> None:
    if isinstance(element, Line):
        x0, y0 = canvas.point(element.x, element.y)
        x1, y1 = canvas.point(element.x2, element.y2)
        bitmap.draw_line(x0, y0, x1, y1, thickness=max(1, canvas.dots(element.thickness)))
    elif isinstance(element, Circle):
        cx, cy = canvas.point(element.x, element.y)
        bitmap.draw_circle(cx, cy, canvas.dots(element.radius), fill=element.fill)
    else:
        bitmap.draw_polygon([canvas.point(p.x, p.y) for p in element.points], fill=element.fill)


def _draw_image(element: Image, bitmap: Bitmap, canvas: Canvas, *, halved: bool = False) -> None:
    """Draw the image as the printer prints it: magnified, and at half resolution when halved."""
    x, y = canvas.point(element.x, element.y)
    if element.bitmap is None:
        size = _PLACEHOLDER_IMAGE_DOTS * element.magnification
        bitmap.fill_rect(x, y - size + 1, size, size, pattern=FillPattern.GRID)
        return
    picture = Bitmap.from_rows(element.bitmap)
    factor = element.magnification
    if halved:
        picture, factor = halve(picture), factor * HALVING_FACTOR
    picture = magnify(picture, factor)
    bitmap.blit(picture, x=x, y=y - picture.height + 1)


def _lowest_row(layer: Bitmap) -> int | None:
    """Return the lowest row of ``layer`` holding a black dot, or None when it is blank."""
    rows = layer.rows()
    return next((y for y in range(len(rows) - 1, -1, -1) if any(rows[y])), None)


def _leftmost_column(layer: Bitmap) -> int:
    """Return the leftmost column of a non-blank ``layer`` holding a black dot."""
    pad = -layer.width % 8
    union = 0
    for row in layer.rows():
        union |= int.from_bytes(row, "big") >> pad
    return layer.width - union.bit_length()


def _draw_halved_graphic(element: Element, bitmap: Bitmap, canvas: Canvas) -> None:
    """Draw a graphic the printer gets as a halved image: its dots in 2x2 blocks.

    The element is drawn alone, cut at its lowest row and leftmost column (where the encoder's
    image starts), halved and doubled there. The blocks line up with the printed ones as far as
    the preview's drawing matches the encoder's raster, to a dot or so.
    """
    layer = Bitmap(canvas.width, canvas.height)
    draw_element(element, layer, canvas)
    bottom = _lowest_row(layer)
    if bottom is None:
        return
    left = _leftmost_column(layer)
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
    """Draw each dark module ``module_dots`` square, the quiet zone in from ``(x, y)``."""
    symbol = _qr_symbol(element)
    if symbol is None:
        return
    size, quiet = element.module_dots, element.quiet_zone * element.module_dots
    x, y = canvas.point(element.x, element.y)
    left, top = x + quiet, y - quiet - symbol.height * size + 1
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


def canvas_for(label: Label, profile: PrinterProfile) -> Canvas:
    """Size the preview: the label size when given, else the print width and the tallest field."""
    dpi = profile.dpi
    width_in = to_inches(label.width, label.unit, dpi=dpi) if label.width else profile.print_width_in
    height_in = to_inches(label.height, label.unit, dpi=dpi) if label.height else _content_height_in(label, dpi)
    return Canvas(width=round(width_in * dpi) + 1, height=round(height_in * dpi) + 1, dpi=dpi, unit=label.unit)


def _text_top(element: Text | ScalableText, unit: Unit, dpi: float) -> float:
    text = _as_text(element) if isinstance(element, ScalableText) else element
    return to_inches(text.y, unit, dpi=dpi) + _text_size(text, dpi)[0] / dpi


def _barcode_top(element: Barcode, unit: Unit, dpi: float) -> float:
    height = to_inches(element.height, unit, dpi=dpi) if element.height else _DEFAULT_BARCODE_HEIGHT_IN
    return to_inches(element.y, unit, dpi=dpi) + height


def _image_top(element: Image, unit: Unit, dpi: float) -> float:
    rows = len(element.bitmap) if element.bitmap else _PLACEHOLDER_IMAGE_DOTS
    return to_inches(element.y, unit, dpi=dpi) + rows * element.magnification / dpi


def _qr_top(element: Qr, unit: Unit, dpi: float) -> float:
    symbol = _qr_symbol(element)
    modules = (symbol.height if symbol is not None else 0) + 2 * element.quiet_zone
    return to_inches(element.y, unit, dpi=dpi) + modules * element.module_dots / dpi


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


def _top_inches(element: Element, unit: Unit, dpi: float) -> float:
    """Highest point of ``element`` above the label bottom, in inches."""
    if isinstance(element, (Text, ScalableText)):
        return _text_top(element, unit, dpi)
    if isinstance(element, Barcode):
        return _barcode_top(element, unit, dpi)
    if isinstance(element, Image):
        return _image_top(element, unit, dpi)
    if isinstance(element, Qr):
        return _qr_top(element, unit, dpi)
    return to_inches(_graphic_top(element), unit, dpi=dpi)


def _content_height_in(label: Label, dpi: float) -> float:
    highest = max(_top_inches(element, label.unit, dpi) for element in label.elements)
    return highest + _MARGIN_DOTS / dpi


def preview_bitmap(label: Label, profile: PrinterProfile, *, halved: frozenset[int] = frozenset()) -> Bitmap:
    """Draw ``label`` for ``profile`` with a one-dot frame marking the label (or print) edge.

    Args:
        label: The label.
        profile: The model; decides the resolution and the print width.
        halved: Elements the job sends at half resolution (``RenderResult.halved_elements``):
            they are drawn the way they print, in 2x2 blocks.
    """
    canvas = canvas_for(label, profile)
    bitmap = Bitmap(canvas.width, canvas.height)
    bitmap.draw_polygon([(0, 0), (canvas.width - 1, 0), (canvas.width - 1, canvas.height - 1), (0, canvas.height - 1)])
    for index, element in enumerate(label.elements):
        if index not in halved:
            draw_element(element, bitmap, canvas)
        elif isinstance(element, Image):
            _draw_image(element, bitmap, canvas, halved=True)
        else:
            _draw_halved_graphic(element, bitmap, canvas)
    return bitmap


__all__ = ["Canvas", "canvas_for", "draw_element", "preview_bitmap", "text_box", "widest_cell_mm"]
