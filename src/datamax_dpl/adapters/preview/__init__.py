"""Preview PNG with real lettering, when Pillow (the ``preview`` extra) is installed.

The domain preview draws text as grey character cells, which shows placement but not content. With
Pillow available, each text field and each barcode's data is lettered in Pillow's scalable default
font, so a reader (or an LLM looking at the PNG) sees what the label says. The lettering goes to
the domain as a mask of its glyph pixels (``preview_bitmap``'s ``masks``) and is combined with the
other fields there, so a line crossing text gaps only under the strokes, as on paper. The lettering
is Pillow's font, not the printer's: shapes and widths differ from a real print. A turned field is
lettered upright in a picture of its own and that picture is turned clockwise (exactly, a quarter
turn being a transpose) into the field's turned box, by the rule in ``domain.preview``.

Without Pillow text stays grey cells that combine as whole cells; :func:`preview_findings` says so.

Pillow is reached through :mod:`importlib` and the small Protocols below rather than a plain
import: the extra exists only on Python 3.11 and later, and a static ``import PIL`` would make the
type check fail on 3.10, where it is not installed.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Protocol, cast

from ...domain.enums import FindingCode, Severity
from ...domain.findings import Finding
from ...domain.label import Barcode, ScalableText, Text
from ...domain.png import encode_png
from ...domain.preview import FieldArea, canvas_for, mask_area, preview_bitmap
from ...domain.raster import Bitmap

if TYPE_CHECKING:
    from ...domain.label import Label
    from ...domain.preview import SentAs
    from ...domain.profiles import PrinterProfile
    from ...domain.render_context import RenderOptions

_MIN_FONT_PIXELS = 6
#: In a mode "1" image a set bit (255) is a glyph pixel; the background and a turn's corners stay 0.
_INK = 255
_NONE = 0


class _Image(Protocol):
    @property
    def size(self) -> tuple[int, int]: ...
    def rotate(self, angle: float, *, expand: bool) -> _Image: ...
    def tobytes(self) -> bytes: ...


class _ImageModule(Protocol):
    def new(self, mode: str, size: tuple[int, int], color: int) -> _Image: ...


class _Draw(Protocol):
    fontmode: str

    def text(self, xy: tuple[int, int], text: str, fill: int, font: object) -> None: ...


class _DrawModule(Protocol):
    def Draw(self, image: _Image) -> _Draw: ...  # noqa: N802 - Pillow's own name


class _FontModule(Protocol):
    def load_default(self, size: float) -> object: ...


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


def pillow_available() -> bool:
    """True when the ``preview`` extra is installed."""
    return _pillow() is not None


def _text_mask(pillow: _Pillow, area: FieldArea, text: str) -> Bitmap:
    """Letter ``text`` upright over ``area``, turn it into place and return its glyph pixels.

    The picture is a mode "1" image drawn without anti-aliasing, so each pixel is a glyph's dot or
    not, and its packed rows are the bitmap's (most significant bit first, padded to whole bytes).
    """
    upright = pillow.image.new("1", (area.length, area.height), _NONE)
    draw = pillow.draw.Draw(upright)
    draw.fontmode = "1"
    draw.text((0, 0), text, fill=_INK, font=pillow.font.load_default(size=max(_MIN_FONT_PIXELS, area.height)))
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
        if isinstance(element, (Text, ScalableText)):
            masks[index] = _text_mask(pillow, mask_area(element, canvas), element.text)
        elif isinstance(element, Barcode):
            masks[index] = _text_mask(pillow, mask_area(element, canvas), element.data)
    return masks


def render_preview_png(
    label: Label, profile: PrinterProfile, *, sent_as: SentAs | None = None, options: RenderOptions | None = None
) -> bytes:
    """Return the preview PNG: lettered with Pillow when available, cell blocks otherwise.

    ``sent_as`` (``RenderResult.sent_as``) says how the job sends its fields: halved images and
    merged groups are drawn the way they print. ``options`` are the ones the job is rendered with,
    so that each field sits at the row and column that job writes; None renders for ``profile``.

    Raises:
        ValueError: ``options`` are for another model than ``profile``.
    """
    pillow = _pillow()
    masks = None if pillow is None else _masks(pillow, label, profile, options)
    return encode_png(preview_bitmap(label, profile, sent_as=sent_as, options=options, masks=masks))


def preview_findings(label: Label) -> list[Finding]:
    """What the preview PNG of ``label`` cannot show: text approximated as cells without Pillow.

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
