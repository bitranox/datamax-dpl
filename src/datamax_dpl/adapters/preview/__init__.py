"""Preview PNG with real lettering, when Pillow (the ``preview`` extra) is installed.

The domain preview draws text as grey character cells, which shows placement but not content. With
Pillow available, each text field and each barcode's data is lettered in Nimbus Sans Bold, URW's
Helvetica (``fonts/``, AGPL-3 with a font exception, see ``fonts/LICENSE-NimbusSans.txt``), so a reader
(or an LLM looking at the PNG) sees what the label says. Font 9 is a Helvetica Bold, so its letters keep
their shapes, a '1' among them being a bare stem as on paper. The font ships with the package rather than
coming from Pillow or the system: Pillow's built-in font has no umlauts or other accented letters (122 of
code page 437's characters drew as the same empty box), and a system font would make the picture
differ from machine to machine. The lettering goes to the domain as a mask of its glyph pixels
(``preview_bitmap``'s ``masks``) and is combined with the other fields there, so a line crossing
text gaps only under the strokes, as on paper. The lettering is not the printer's font: shapes
differ from a real print. Fonts 0-8 are lettered as one
string, whose widths differ from the printer's cells too; font 9 and scalable text are lettered one
character at a time, each where the printer's advance puts it (``domain.text_metrics``) and no
wider than that glyph's ink, so the field ends where the printed line is estimated to end and no
letter is cut there. A turned field is
lettered upright in a picture of its own and that picture is turned clockwise (exactly, a quarter
turn being a transpose) into the field's turned box, by the rule in ``domain.preview``.

Without Pillow text stays grey cells that combine as whole cells; :func:`preview_findings` says so.

Pillow is reached through :mod:`importlib` and the small Protocols below rather than a plain
import: the extra exists only on Python 3.11 and later, and a static ``import PIL`` would make the
type check fail on 3.10, where it is not installed.
"""

from __future__ import annotations

import importlib
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol, cast

from ...domain.enums import FindingCode, Severity
from ...domain.findings import Finding
from ...domain.label import FONT_9, Barcode, ScalableText, Text
from ...domain.png import encode_png
from ...domain.preview import (
    DEFAULT_PREVIEW_MAX_LENGTH_MM,
    Canvas,
    FieldArea,
    canvas_for,
    check_preview_fields,
    check_preview_length,
    letter_ink_spans,
    mask_area,
    preview_bitmap,
    printed_text,
)
from ...domain.raster import Bitmap

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...domain.geometry import Span
    from ...domain.label import Label
    from ...domain.preview import SentAs
    from ...domain.profiles import PrinterProfile
    from ...domain.render_context import RenderOptions

_MIN_FONT_PIXELS = 6
#: The lettering font, shipped beside this module so every machine draws the same picture.
_FONT_FILE = Path(__file__).resolve().parent / "fonts" / "NimbusSans-Bold.otf"
#: Fonts kept by pixel size; a label uses a handful of sizes, and the bound keeps a long batch of
#: varied sizes from holding a font object per size it ever met.
_FONT_CACHE_SIZES = 32
#: The narrowest visible character of the lettering font (an apostrophe, 0.238 of its size in
#: Nimbus Sans Bold) advances more than a fifth of its size, so this many dots along the field
#: hold at most one character per this share of the font size.
_NARROWEST_LETTER_SHARE = 5
#: Where a letter's baseline sits below the top of its field: the ascent of a Helvetica-metric TrueType
#: font (Arial, Liberation Sans: 1854 of 2048 units per em). Nimbus Sans's own ascent is its cap height,
#: which would put the accent of a capital (an A umlaut, an E acute) above the field, where it is cut off.
_BASELINE_UNITS, _UNITS_PER_EM = 1854, 2048
#: FreeType's 26.6 fixed point, in which the baseline is rounded as it rounds a font's ascent.
_SUBPIXELS = 64
#: In a mode "1" image a set bit (255) is a glyph pixel; the background and a turn's corners stay 0.
_INK = 255
_NONE = 0


class _Image(Protocol):
    @property
    def size(self) -> tuple[int, int]: ...
    def rotate(self, angle: float, *, expand: bool) -> _Image: ...
    def tobytes(self) -> bytes: ...
    def getbbox(self) -> tuple[int, int, int, int] | None: ...
    def crop(self, box: tuple[int, int, int, int]) -> _Image: ...
    def resize(self, size: tuple[int, int]) -> _Image: ...
    def paste(self, im: _Image, box: tuple[int, int], mask: _Image) -> None: ...


class _ImageModule(Protocol):
    def new(self, mode: str, size: tuple[int, int], color: int) -> _Image: ...


class _Draw(Protocol):
    fontmode: str

    def text(self, xy: tuple[int, int], text: str, fill: int, font: object, anchor: str) -> None: ...


class _DrawModule(Protocol):
    def Draw(self, image: _Image) -> _Draw: ...  # noqa: N802 - Pillow's own name


class _FontModule(Protocol):
    def truetype(self, font: str, size: int) -> object: ...


class _Pillow(Protocol):
    image: _ImageModule
    draw: _DrawModule
    font: _FontModule


class _Modules:
    def __init__(self, image: _ImageModule, draw: _DrawModule, font: _FontModule) -> None:
        self.image, self.draw, self.font = image, draw, font


def _pillow() -> _Pillow | None:
    """Pillow's Image, ImageDraw and ImageFont modules, or None when the extra is not installed."""
    try:
        image = cast("_ImageModule", importlib.import_module("PIL.Image"))
        draw = cast("_DrawModule", importlib.import_module("PIL.ImageDraw"))
        font = cast("_FontModule", importlib.import_module("PIL.ImageFont"))
    except ImportError:
        return None
    return _Modules(image, draw, font)


def _baseline(size: int) -> int:
    """The row of a letter's baseline, counted from the top of a field ``size`` pixels high.

    Rounded up to a whole pixel from FreeType's 26.6 fixed point, as FreeType rounds a font's ascent.

    Example:
        >>> _baseline(39), _baseline(100)
        (36, 91)
    """
    subpixels = (size * _BASELINE_UNITS * _SUBPIXELS + _UNITS_PER_EM // 2) // _UNITS_PER_EM
    return -(-subpixels // _SUBPIXELS)


@lru_cache(maxsize=_FONT_CACHE_SIZES)
def _lettering_font(fonts: _FontModule, size: int) -> object:
    """The lettering font at ``size`` pixels, loaded once per size."""
    return fonts.truetype(str(_FONT_FILE), size)


def pillow_available() -> bool:
    """Whether the ``preview`` extra (Pillow) is installed, which letters the text of a preview picture.

    Returns:
        True when Pillow can be imported; without it a preview approximates text and says so
        (:func:`preview_findings`).

    Example:
        >>> isinstance(pillow_available(), bool)
        True
    """
    return _pillow() is not None


def _letter_spans(element: Text | ScalableText | Barcode, canvas: Canvas) -> tuple[Span, ...] | None:
    """Where each letter goes for a field lettered one character at a time, None for one lettered whole.

    Font 9 (without a ``font_id``) and scalable text go letter by letter; fonts 0-8, a downloaded
    font and a barcode's data are lettered as one string.
    """
    if isinstance(element, ScalableText) or (
        isinstance(element, Text) and element.font == FONT_9 and element.font_id is None
    ):
        return letter_ink_spans(element, canvas)
    return None


class _Lettering(NamedTuple):
    """What every letter of one field is drawn with.

    Attributes:
        font: The lettering font at ``size`` pixels.
        size: The font's pixel size, the field's height.
        top: The row the letters are drawn from, above the visible part when the canvas cuts the field.
        origin: Where the visible part starts along the line, so a span's start becomes a column.
    """

    font: object
    size: int
    top: int
    origin: int


def _letter_in_span(pillow: _Pillow, upright: _Image, char: str, span: Span, lettering: _Lettering) -> None:
    """Draw one letter's ink inside the printer's ink for it, ``span``, on ``upright``.

    The lettering font's glyphs are not the printer's: font 9 inks each letter within the box
    :func:`~datamax_dpl.domain.preview.letter_ink_spans` gives (a '1' a narrow stem a third of its cell wide),
    and a lettering glyph can be wider than that box. Drawn at its own width, such a letter ran over its
    neighbour and, at the end of a field, lost what ran past the line's end. So the ink is cut out of a picture
    of its own, narrowed to the span when wider (never widened) and centred in it, and a letter looks the same
    wherever it stands in the line. Columns outside ``upright`` are cut there, which is where the canvas ends.
    """
    room = lettering.size // 2
    _, height = upright.size
    glyph = pillow.image.new("1", (2 * lettering.size + 2 * room, height), _NONE)
    draw = pillow.draw.Draw(glyph)
    draw.fontmode = "1"
    draw.text((room, lettering.top + _baseline(lettering.size)), char, fill=_INK, font=lettering.font, anchor="ls")
    box = glyph.getbbox()
    if box is None:
        return
    left, _, right, _ = box
    ink = glyph.crop((left, 0, right, height))
    width, slot = right - left, max(1, span.length)
    if width > slot:
        ink, width = ink.resize((slot, height)), slot
    x = span.start + (slot - width) // 2
    upright.paste(ink, (x - lettering.origin, 0), ink)


def _text_mask(
    pillow: _Pillow, area: FieldArea, text: str, canvas: Canvas, *, spans: Sequence[Span] | None = None
) -> Bitmap | None:
    """Letter the part of ``text`` that lands on ``canvas``, turn it into place and return its glyph pixels.

    The picture is a mode "1" image drawn without anti-aliasing, so each pixel is a glyph's dot or
    not, and its packed rows are the bitmap's (most significant bit first, padded to whole bytes).
    Only that part is allocated, and only the letters that can reach it are drawn: Pillow builds a
    picture of the whole string at a byte per pixel first, which took 1.9 s and 459 MB for one
    field of 255 characters at 24 times the size, nearly all of it past the canvas.

    With ``spans`` (one per character, :func:`~datamax_dpl.domain.preview.letter_ink_spans`) each
    letter is drawn on its own inside the ink the printer gives it (:func:`_letter_in_span`), not at
    the lettering font's own advance: lettered as one string, a font whose widths differ from the
    printer's runs short of the field, or past it, and the end of a font 9 line read as cut off where
    the paper printed it whole.

    Returns:
        The glyph pixels, the size of the field's ``mask_box``; None when the field misses the canvas.
    """
    part = area.on_canvas(width=canvas.width, height=canvas.height)
    if part is None:
        return None
    size = max(_MIN_FONT_PIXELS, area.height)
    above = area.height - part.up.start - part.up.length
    upright = pillow.image.new("1", (part.along.length, part.up.length), _NONE)
    draw = pillow.draw.Draw(upright)
    draw.fontmode = "1"
    font = _lettering_font(pillow.font, size)
    if spans is None:
        letters = (part.along.start + part.along.length) // max(1, size // _NARROWEST_LETTER_SHARE) + 1
        draw.text((-part.along.start, _baseline(size) - above), text[:letters], fill=_INK, font=font, anchor="ls")
    else:
        end = part.along.start + part.along.length
        lettering = _Lettering(font=font, size=size, top=-above, origin=part.along.start)
        for char, span in zip(text, spans, strict=True):
            if span.start < end and span.start + span.length > part.along.start and not char.isspace():
                _letter_in_span(pillow, upright, char, span, lettering)
    # Pillow turns counter-clockwise by positive angles; the manual's rotations turn clockwise.
    turned = upright.rotate(-area.rotation.value, expand=True)
    width, height = turned.size
    data, stride = turned.tobytes(), (width + 7) // 8
    return Bitmap.from_packed_rows([data[row * stride : (row + 1) * stride] for row in range(height)], width=width)


def _masks(pillow: _Pillow, label: Label, profile: PrinterProfile, options: RenderOptions | None) -> dict[int, Bitmap]:
    """The lettering of each text field and barcode, by element index."""
    canvas = canvas_for(label, profile, options=options)
    masks: dict[int, Bitmap] = {}
    for index, element in enumerate(label.elements):
        if not isinstance(element, (Text, ScalableText, Barcode)):
            continue
        # A text field is lettered as the printer prints it ('?' for a character its font cannot
        # print, a capital in a capitals-only font), never as typed: the preview stands in for paper.
        text = element.data if isinstance(element, Barcode) else printed_text(element, canvas)
        mask = _text_mask(pillow, mask_area(element, canvas), text, canvas, spans=_letter_spans(element, canvas))
        if mask is not None:
            masks[index] = mask
    return masks


def render_preview_png(
    label: Label,
    profile: PrinterProfile,
    *,
    sent_as: SentAs | None = None,
    options: RenderOptions | None = None,
    max_length_mm: float = DEFAULT_PREVIEW_MAX_LENGTH_MM,
) -> bytes:
    """Return the preview PNG: lettered with Pillow when available, cell blocks otherwise.

    Args:
        label: The label to draw.
        profile: The model whose print width and resolution the picture uses.
        sent_as: ``RenderResult.sent_as``, how the job sends its fields: halved images and merged
            groups are drawn the way they print.
        options: The ones the job is rendered with, so that each field sits at the row and column
            that job writes; None renders for ``profile``.
        max_length_mm: The longest side the picture may have, checked before anything is drawn.

    Returns:
        The PNG file's bytes.

    Raises:
        PreviewTooLongError: A side of the picture would be longer than ``max_length_mm``, or the
            job would send more fields than the model's format takes (``sent_as`` counts a merged
            group once).
        UnknownModelError: ``options.model`` names no profile.
        ValueError: ``options`` are for another model than ``profile``.

    Examples:
        >>> from datamax_dpl import Label, Text, get_profile
        >>> png = render_preview_png(Label(elements=[Text(x=1, y=1, text="HELLO")]), get_profile("dmx-600"))
        >>> png[1:4]
        b'PNG'
    """
    check_preview_length(canvas_for(label, profile, options=options), max_length_mm=max_length_mm)
    check_preview_fields(label, profile, sent_as=sent_as)
    pillow = _pillow()
    masks = None if pillow is None else _masks(pillow, label, profile, options)
    return encode_png(preview_bitmap(label, profile, sent_as=sent_as, options=options, masks=masks))


def preview_findings(label: Label) -> list[Finding]:
    """What the preview PNG of ``label`` cannot show: text approximated as cells without Pillow.

    Args:
        label: The label the preview draws.

    Returns:
        One finding per approximated field; none when Pillow is available.

    Example:
        >>> from datamax_dpl.domain.label import Label, Line
        >>> preview_findings(Label(elements=[Line(x=0, y=0, x2=5, y2=0, thickness=1)]))
        []
    """
    if pillow_available() or not any(isinstance(e, (Text, ScalableText)) for e in label.elements):
        return []
    message = (
        "Pillow (the preview extra, Python 3.11+) is not installed, so a field crossing text combines "
        "with its whole character cells, not with the letters' dots the printer prints"
    )
    fallback = "text drawn as grey character cells"
    return [
        Finding(code=FindingCode.PREVIEW_TEXT_APPROXIMATED, severity=Severity.INFO, message=message, fallback=fallback)
    ]


__all__ = ["pillow_available", "preview_findings", "render_preview_png"]
