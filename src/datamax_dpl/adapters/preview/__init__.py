"""Preview PNG with real lettering, when Pillow (the ``preview`` extra) is installed.

The domain preview draws text as grey character cells, which shows placement but not content. With
Pillow available, each text field and each barcode's data is written into its area in Pillow's
scalable default font, so a reader (or an LLM looking at the PNG) sees what the label says. The
lettering is Pillow's font, not the printer's: shapes and widths differ from a real print.

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
from ...domain.preview import canvas_for, preview_bitmap, text_box

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...domain.label import Label
    from ...domain.profiles import PrinterProfile

_MIN_FONT_PIXELS = 6
_BARCODE_TEXT_SHARE = 4
_WHITE = 255
_BLACK = 0


class _Image(Protocol):
    def putdata(self, data: Sequence[int]) -> None: ...
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


def _letter(pillow: _Pillow, image: _Image, box: tuple[int, int, int, int], text: str) -> None:
    left, top, width, height = box
    draw = pillow.draw.Draw(image)
    draw.rectangle((left, top, left + width - 1, top + height - 1), fill=_WHITE)
    font = pillow.font.load_default(size=max(_MIN_FONT_PIXELS, height))
    draw.text((left, top), text, fill=_BLACK, font=font)


def render_preview_png(label: Label, profile: PrinterProfile) -> bytes:
    """Return the preview PNG: lettered with Pillow when available, cell blocks otherwise."""
    bitmap = preview_bitmap(label, profile)
    pillow = _pillow()
    if pillow is None:
        return encode_png(bitmap)
    image = pillow.image.new("L", (bitmap.width, bitmap.height), _WHITE)
    image.putdata([_BLACK if bitmap.get(x, y) else _WHITE for y in range(bitmap.height) for x in range(bitmap.width)])
    canvas = canvas_for(label, profile)
    for element in label.elements:
        if isinstance(element, (Text, ScalableText)):
            _letter(pillow, image, text_box(element, canvas), element.text)
        elif isinstance(element, Barcode):
            left, top, width, height = text_box(element, canvas)
            _letter(pillow, image, (left, top, width, max(1, height // _BARCODE_TEXT_SHARE)), element.data)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


__all__ = ["pillow_available", "render_preview_png"]
