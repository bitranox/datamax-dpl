"""The label model: what a caller, a CLI user or an LLM describes, independent of any printer.

A :class:`Label` holds job settings (quantity, cutting, speeds, heat, media) and a list of
elements. Every length is a number in the label's :attr:`Label.unit` (millimetres by default).

Coordinates follow DPL: ``x`` is the distance from the left edge of the label, ``y`` the distance
from the BOTTOM edge, and a field's reference point is its lower-left corner before rotation.
Circles are placed by their centre. ``datamax-dpl preview`` draws a label so the placement can be
checked without paper.

Text fields may contain ``{name}`` placeholders, filled per record by
:func:`~datamax_dpl.domain.placeholders.fill_label`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import Attribute, FillPattern, MediaSensor, Rotation, SpeedCode, Unit
from .symbology import Symbology

MAX_FIELD_DATA = 255
MAX_PDF417_DATA = 3000
MAX_MULTIPLIER = 24
MAX_IMAGE_NAME = 8
MAX_FORMAT_NAME = 16
MAX_QUANTITY = 9999
#: The font number that selects CG Triumvirate point sizes (and downloaded fonts).
FONT_9 = 9

NonNegative = Annotated[float, Field(ge=0)]
Positive = Annotated[float, Field(gt=0)]
Multiplier = Annotated[int, Field(ge=1, le=MAX_MULTIPLIER)]


class _Frozen(BaseModel):
    # revalidate_instances: model_copy(update=...) skips validation, so placeholder filling
    # re-validates the filled copy through Label.model_validate; without this an instance
    # passed back in is trusted as-is and a filled value past a field limit slips through.
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class Counter(_Frozen):
    """Make a field count up or down from label to label within one batch.

    Attributes:
        step: Amount added (or subtracted) per label, 1-99.
        decrement: Count down instead of up.
        alphanumeric: Count letters too (``>``/``<``) instead of digits only (``+``/``-``).
        fill: Character that pads the field on the left as it grows, usually ``0`` or a space.

    Example:
        >>> Counter(step=1).decrement
        False
    """

    step: int = Field(default=1, ge=1, le=99)
    decrement: bool = False
    alphanumeric: bool = False
    fill: str = Field(default="0", min_length=1, max_length=1)


class _Placed(_Frozen):
    x: NonNegative = Field(description="Distance from the left edge of the label.")
    y: NonNegative = Field(description="Distance from the bottom edge of the label.")
    rotation: Rotation = Rotation.DEG_0


class Text(_Placed):
    """Text in one of the printer's built-in fonts.

    Fonts 0-8 are fixed bitmapped fonts scaled by the two multipliers. Font 9 is CG Triumvirate in
    point sizes; give ``points`` (4-72) and the encoder picks the size code the model knows.
    ``font_id`` selects a downloaded or cartridge font by its DPL id (``100``-``999``, ``B04``...)
    instead and needs ``font=9``.

    Example:
        >>> Text(x=5, y=10, text="Hello").font
        2
    """

    type: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=MAX_FIELD_DATA)
    font: int = Field(default=2, ge=0, le=9)
    points: int | None = Field(default=None, ge=4, le=72)
    font_id: str | None = Field(default=None, pattern=r"^[0-9A-Za-z]{3}$")
    width_multiplier: Multiplier = 1
    height_multiplier: Multiplier = 1
    attribute: Attribute | None = None
    counter: Counter | None = None

    @model_validator(mode="after")
    def _font9_needs_a_size(self) -> Text:
        if self.font == FONT_9 and self.points is None and self.font_id is None:
            msg = "font 9 needs 'points' or 'font_id'"
            raise ValueError(msg)
        if self.font != FONT_9 and (self.points is not None or self.font_id is not None):
            msg = "'points' and 'font_id' apply to font 9 only"
            raise ValueError(msg)
        return self


class ScalableText(_Placed):
    """Text in a scalable (outline) font, sized in points.

    Only some models have scalable fonts; elsewhere the encoder falls back to the nearest font 9
    size. ``font_id`` ``S00`` is the resident CG Triumvirate Bold Condensed.

    Example:
        >>> ScalableText(x=0, y=0, text="Big", height_points=36).font_id
        'S00'
    """

    type: Literal["scalable_text"] = "scalable_text"
    text: str = Field(min_length=1, max_length=MAX_FIELD_DATA)
    font_id: str = Field(default="S00", pattern=r"^S[0-9A-Za-z]{2}$")
    height_points: float = Field(ge=4, le=999)
    width_points: float | None = Field(default=None, ge=4, le=999)
    attribute: Attribute | None = None


class Line(_Frozen):
    """A straight line from ``(x, y)`` to ``(x2, y2)``.

    Horizontal and vertical lines become DPL line records on every model. A slanted line needs
    polygon support, or is drawn as a downloaded image where that is missing.

    Example:
        >>> Line(x=0, y=0, x2=50, y2=0, thickness=0.3).is_axis_aligned
        True
    """

    type: Literal["line"] = "line"
    x: NonNegative
    y: NonNegative
    x2: NonNegative
    y2: NonNegative
    thickness: Positive

    @property
    def is_axis_aligned(self) -> bool:
        """True when the line is horizontal or vertical."""
        return self.x == self.x2 or self.y == self.y2


class Box(_Frozen):
    """A rectangle; ``(x, y)`` is its lower-left corner.

    ``thickness`` is the top and bottom edge, ``side_thickness`` the left and right edge (defaults
    to ``thickness``). A ``fill`` other than none needs polygon support; a solid fill falls back to
    a box whose edges meet in the middle.

    Example:
        >>> Box(x=0, y=0, width=40, height=20, thickness=0.5).side_thickness is None
        True
    """

    type: Literal["box"] = "box"
    x: NonNegative
    y: NonNegative
    width: Positive
    height: Positive
    thickness: Positive
    side_thickness: Positive | None = None
    fill: FillPattern = FillPattern.NONE


class Circle(_Frozen):
    """A circle around ``(x, y)``.

    Example:
        >>> Circle(x=20, y=20, radius=5).fill
        <FillPattern.NONE: 0>
    """

    type: Literal["circle"] = "circle"
    x: NonNegative
    y: NonNegative
    radius: Positive
    fill: FillPattern = FillPattern.NONE


class Point(_Frozen):
    """One vertex of a polygon."""

    x: NonNegative
    y: NonNegative


class Polygon(_Frozen):
    """A closed polygon through ``points`` (the last point connects back to the first).

    Example:
        >>> len(Polygon(points=[Point(x=0, y=0), Point(x=10, y=0), Point(x=5, y=8)]).points)
        3
    """

    type: Literal["polygon"] = "polygon"
    points: list[Point] = Field(min_length=2, max_length=60)
    fill: FillPattern = FillPattern.NONE


class Pdf417Options(_Frozen):
    """PDF417 parameters (``T``/``F``, security, aspect ratio, rows, columns).

    Zero rows or columns lets the printer choose.
    """

    truncated: bool = False
    security_level: int = Field(default=2, ge=0, le=8)
    aspect_ratio: int = Field(default=0, ge=0, le=99)
    rows: int = Field(default=0, ge=0, le=90)
    columns: int = Field(default=0, ge=0, le=30)


class DataMatrixOptions(_Frozen):
    """DataMatrix parameters: ECC level, format id, rows and columns (0 = automatic)."""

    ecc: Literal[0, 50, 80, 100, 140, 200] = 200
    format_id: int = Field(default=0, ge=0, le=6)
    rows: int = Field(default=0, ge=0, le=144)
    columns: int = Field(default=0, ge=0, le=144)


class Barcode(_Placed):
    """A one- or two-dimensional barcode.

    ``narrow_dots`` and ``wide_dots`` are bar widths in printer dots for ratio codes (Code 39,
    Interleaved 2 of 5, Codabar ...); for module codes (Code 128, EAN, UPC, 2-D) ``narrow_dots`` is
    the module width and ``wide_dots`` is ignored. Zero lets the printer use its default.
    ``height`` of None uses the printer's default height.

    Example:
        >>> Barcode(x=5, y=5, symbology=Symbology.CODE128, data="ABC123").human_readable
        True
    """

    type: Literal["barcode"] = "barcode"
    symbology: Symbology
    data: str = Field(min_length=1, max_length=MAX_PDF417_DATA)
    height: Positive | None = None
    narrow_dots: int = Field(default=0, ge=0, le=MAX_MULTIPLIER)
    wide_dots: int = Field(default=0, ge=0, le=MAX_MULTIPLIER)
    human_readable: bool = True
    pdf417: Pdf417Options | None = None
    datamatrix: DataMatrixOptions | None = None
    counter: Counter | None = None


class Image(_Frozen):
    """A monochrome image, placed by its lower-left corner (DPL images cannot be rotated).

    With ``bitmap`` the image is downloaded to the printer under ``name`` before the label;
    without it ``name`` must already be stored on the printer. Each bitmap row is a string of
    ``1``/``#`` (black) and ``0``/``.`` (white), top row first.

    Example:
        >>> Image(x=0, y=0, name="LOGO", bitmap=["#.", ".#"]).name
        'LOGO'
    """

    type: Literal["image"] = "image"
    x: NonNegative
    y: NonNegative
    name: str = Field(pattern=r"^[A-Za-z0-9_]{1,8}$")
    bitmap: list[str] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _rows_are_bits(self) -> Image:
        if self.bitmap is None:
            return self
        widths = {len(row) for row in self.bitmap}
        if len(widths) != 1 or 0 in widths:
            msg = "every bitmap row must have the same, non-zero length"
            raise ValueError(msg)
        if any(set(row) - set("10#.") for row in self.bitmap):
            msg = "bitmap rows may only contain 1, 0, # and ."
            raise ValueError(msg)
        return self


Element = Annotated[
    Text | ScalableText | Line | Box | Circle | Polygon | Barcode | Image,
    Field(discriminator="type"),
]


class Cut(_Frozen):
    """Cutter instructions for the job.

    Attributes:
        every: Cut after every N labels (1 cuts each label).
        at_end: Also cut after the last label when the quantity is not a multiple of ``every``;
            without it the printer cuts that last group only when the next job starts.

    Example:
        >>> Cut(every=5).at_end
        True
    """

    every: int = Field(default=1, ge=1, le=MAX_QUANTITY)
    at_end: bool = True


class DotSize(_Frozen):
    """Dot size command ``Dwh``: width 1-2, height 1-3 (``D22`` is the printer default)."""

    width: int = Field(ge=1, le=2)
    height: int = Field(ge=1, le=3)


class Label(_Frozen):
    """A complete label job.

    Example:
        >>> label = Label(elements=[Text(x=5, y=5, text="Hi")])
        >>> label.unit, label.quantity
        (<Unit.MM: 'mm'>, 1)
    """

    unit: Unit = Unit.MM
    width: Positive | None = Field(default=None, description="Label width; defaults to the model's print width.")
    height: Positive | None = Field(default=None, description="Label length in feed direction.")
    quantity: int = Field(default=1, ge=1, le=MAX_QUANTITY)
    elements: list[Element] = Field(min_length=1)
    cut: Cut | None = None
    heat: int | None = Field(default=None, ge=0, le=30)
    print_speed: SpeedCode | None = None
    slew_speed: SpeedCode | None = None
    backup_speed: SpeedCode | None = None
    dot_size: DotSize | None = None
    column_offset: NonNegative | None = None
    row_offset: NonNegative | None = None
    attribute: Attribute | None = None
    mirror: bool = False
    symbol_set: str | None = Field(default=None, pattern=r"^[A-Z0-9]{2}$")
    media: MediaSensor | None = None
    continuous_length: Positive | None = Field(default=None, description="Label length on continuous media.")
    stop_position: Positive | None = Field(default=None, description="Feed past the start of print, back on next job.")
    present_distance: Positive | None = Field(default=None, description="Distance to present a label for removal.")
    pause_each_label: bool = False
    count_by: int | None = Field(default=None, ge=1, le=99)
    plain_zero: bool = Field(default=False, description="Print 0 without the slash in fonts 0-8 and barcodes.")


__all__ = [
    "FONT_9",
    "MAX_FIELD_DATA",
    "MAX_FORMAT_NAME",
    "MAX_IMAGE_NAME",
    "MAX_MULTIPLIER",
    "MAX_PDF417_DATA",
    "MAX_QUANTITY",
    "Barcode",
    "Box",
    "Circle",
    "Counter",
    "Cut",
    "DataMatrixOptions",
    "DotSize",
    "Element",
    "Image",
    "Label",
    "Line",
    "Pdf417Options",
    "Point",
    "Polygon",
    "ScalableText",
    "Text",
]
