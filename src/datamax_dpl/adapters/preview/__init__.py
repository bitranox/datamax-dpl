"""Preview PNG with real lettering, when Pillow (the ``preview`` extra) is installed.

The domain preview draws text as grey character cells, which shows placement but not content. With
Pillow available, each text field and each barcode's data is written into its area in Pillow's
scalable default font, so a reader (or an LLM looking at the PNG) sees what the label says. The
lettering is Pillow's font, not the printer's: shapes and widths differ from a real print. A turned
field is lettered upright in a picture of its own and that picture is turned clockwise (exactly, a
quarter turn being a transpose) into the field's turned box, by the rule in ``domain.preview``.

Pillow is reached through :mod:`importlib` and the small Protocols below rather than a plain
import: the extra exists only on Python 3.11 and later, and a static ``import PIL`` would make the
type check fail on 3.10, where it is not installed.
"""

from __future__ import annotations

import importlib
import io
from typing import TYPE_CHECKING, Protocol, cast

from ...domain.label import Barcode, ScalableText, Text
from ...domain.png import encode_png
from ...domain.preview import FieldArea, canvas_for, field_area, preview_bitmap

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...domain.label import Label
    from ...domain.preview import SentAs
    from ...domain.profiles import PrinterProfile
    from ...domain.render_context import RenderOptions

_MIN_FONT_PIXELS = 6
_BARCODE_TEXT_SHARE = 4
_WHITE = 255
_BLACK = 0


class _Image(Protocol):
    def putdata(self, data: Sequence[int]) -> None: ...
    def rotate(self, angle: float, *, expand: bool) -> _Image: ...
    def paste(self, im: _Image, box: tuple[int, int]) -> None: ...
    def save(self, fp: io.BytesIO, format: str) -> None: ...  # noqa: A002 - Pillow's own keyword


class _ImageModule(Protocol):
    def new(self, mode: str, size: tuple[int, int], color: int) -> _Image: ...


class _Draw(Protocol):
    def rectangle(self, xy: tuple[int, int, int, int], fill: int) -> None: ...
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


def _letter(pillow: _Pillow, image: _Image, area: FieldArea, *, text: str, band: int) -> None:
    """Write ``text`` over the top ``band`` rows of the upright field, then turn it into place.

    The band is whitened first, so the grey cells or hatching under the lettering do not show.
    """
    upright = pillow.image.new("L", (max(1, area.length), band), _WHITE)
    font = pillow.font.load_default(size=max(_MIN_FONT_PIXELS, band))
    pillow.draw.Draw(upright).text((0, 0), text, fill=_BLACK, font=font)
    # Pillow turns counter-clockwise by positive angles; the manual's rotations turn clockwise.
    turned = upright.rotate(-area.rotation.value, expand=True)
    left, top, _, _ = area.part(up=(area.height - band, band))
    image.paste(turned, (left, top))


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
    bitmap = preview_bitmap(label, profile, sent_as=sent_as, options=options)
    pillow = _pillow()
    if pillow is None:
        return encode_png(bitmap)
    image = pillow.image.new("L", (bitmap.width, bitmap.height), _WHITE)
    image.putdata([_BLACK if bitmap.get(x, y) else _WHITE for y in range(bitmap.height) for x in range(bitmap.width)])
    canvas = canvas_for(label, profile, options=options)
    for element in label.elements:
        if isinstance(element, (Text, ScalableText)):
            area = field_area(element, canvas)
            _letter(pillow, image, area, text=element.text, band=max(1, area.height))
        elif isinstance(element, Barcode):
            area = field_area(element, canvas)
            _letter(pillow, image, area, text=element.data, band=max(1, area.height // _BARCODE_TEXT_SHARE))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


__all__ = ["pillow_available", "render_preview_png"]
