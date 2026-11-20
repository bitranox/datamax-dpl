"""How wide a text field prints: the advance of each character in printer dots.

Fonts 0-8 are fixed bitmaps. Their character cells (height, width and the gap after each
character) come from the font tables in ``docs/dpl-reference.md`` section 6, at 203, 300 and
289.56 dpi, so their widths are exact.

Font 9 is the resident CG Triumvirate Bold, a proportional face: the manual's table "Font 9
Bitmapped Resident fonts specifications" names it for both size numberings (printed page 174, PDF
page 184), and its note on font 9 says "each character will take up a different amount of space
when printed. For example, the letter W will be larger than the letter I" (printed page 119, PDF
page 129). The manual publishes no widths. :data:`FONT_9_ADVANCES` holds the advance widths of
Adobe's Helvetica-Bold instead (the Core 14 AFM file, version 001.007, as matplotlib ships it),
since CG Triumvirate is Agfa's Helvetica design. That is a stand-in, unverified until paper: the
widths of the printer's own bitmaps may differ, and the point size is taken as the em
(``points * dpi / 72`` dots). The table is the one place to change when a paper calibration
measures the real widths. Before it, every font 9 character was estimated at 0.6 of the em, which
an Allegro 2 proved too narrow: "NORTHWAY" at 8 pt and "ABCDEF00123" at 30 pt printed whole where
the preview cut their last letter (2026-11-20).

Text alignment (:class:`~.enums.TextAlign`) moves a field's reference point back by these widths:
DPL places a field at its lower-left corner and has no justification of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from .charset import FONT_BYTES, encode_for_bitmapped_font
from .enums import TextAlign
from .frozen_mapping import FrozenMapping
from .label import FONT_9, Text

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .profiles import PrinterProfile
    from .render_context import RenderContext

_DPI_300 = 300
_DPI_289 = 289
_POINTS_PER_INCH = 72
#: Advance widths in :data:`FONT_9_ADVANCES` are thousandths of the em, as an AFM file states them.
EM_UNITS = 1000
#: Font 9 point size a field with neither ``points`` nor ``font_id`` is drawn at; validation
#: refuses such a field, so this only keeps the arithmetic total.
_DEFAULT_POINTS = 10
#: Share of the em one character of a font 9 field takes when its font is unknown (a ``font_id``
#: names a downloaded or cartridge font, whose widths nothing here knows).
_UNKNOWN_FONT_ADVANCE = 0.6
#: Advance of a character Helvetica-Bold has no glyph for (a box-drawing or Greek character of code
#: page 437), in thousandths of the em: a digit's width, the font's most common advance.
FONT_9_FALLBACK_ADVANCE = 556


@dataclass(frozen=True)
class CharCell:
    """One character of a bitmapped font in dots: its height, its width and the gap after it."""

    height: int
    width: int
    gap: int


@dataclass(frozen=True)
class FontCells:
    """A bitmapped font's character cell at each resolution the font tables list."""

    dpi_203: CharCell
    dpi_300: CharCell
    dpi_289: CharCell

    def at(self, dpi: float) -> CharCell:
        """The cell at ``dpi``: 300 dpi and up, 289.56 dpi (the DMX 430), else 203 dpi."""
        if dpi >= _DPI_300:
            return self.dpi_300
        if dpi >= _DPI_289:
            return self.dpi_289
        return self.dpi_203


#: Character cell (height, width, gap) in dots at 203 / 300 / 289.56 dpi, for fonts 0-8.
CELLS: Mapping[int, FontCells] = MappingProxyType(
    {
        0: FontCells(CharCell(7, 5, 1), CharCell(10, 7, 1), CharCell(10, 7, 1)),
        1: FontCells(CharCell(13, 7, 2), CharCell(19, 10, 3), CharCell(18, 10, 3)),
        2: FontCells(CharCell(18, 10, 2), CharCell(27, 15, 3), CharCell(26, 14, 3)),
        3: FontCells(CharCell(27, 14, 2), CharCell(40, 21, 3), CharCell(39, 20, 3)),
        4: FontCells(CharCell(36, 18, 3), CharCell(53, 27, 4), CharCell(52, 26, 4)),
        5: FontCells(CharCell(52, 18, 3), CharCell(77, 27, 4), CharCell(75, 26, 4)),
        6: FontCells(CharCell(64, 32, 4), CharCell(95, 47, 6), CharCell(92, 46, 6)),
        7: FontCells(CharCell(32, 15, 5), CharCell(47, 22, 7), CharCell(46, 22, 7)),
        8: FontCells(CharCell(28, 15, 5), CharCell(41, 22, 7), CharCell(40, 22, 7)),
    }
)

# Generated from phvb8a.afm: each code page 437 byte font 9 prints, decoded to its character, named
# by the Adobe Glyph List, and that glyph's WX. The bytes Helvetica-Bold has no glyph for are left
# out and take FONT_9_FALLBACK_ADVANCE: 158, 169, 181-184, 189, 190, 198, 199, 208-216, 222, 224,
# 226-229, 231-237, 243, 247 and 249.
# fmt: off
_HELVETICA_BOLD: dict[int, int] = {
    32: 278, 33: 333, 34: 474, 35: 556, 36: 556, 37: 889, 38: 722, 39: 238,
    40: 333, 41: 333, 42: 389, 43: 584, 44: 278, 45: 333, 46: 278, 47: 278,
    48: 556, 49: 556, 50: 556, 51: 556, 52: 556, 53: 556, 54: 556, 55: 556,
    56: 556, 57: 556, 58: 333, 59: 333, 60: 584, 61: 584, 62: 584, 63: 611,
    64: 975, 65: 722, 66: 722, 67: 722, 68: 722, 69: 667, 70: 611, 71: 778,
    72: 722, 73: 278, 74: 556, 75: 722, 76: 611, 77: 833, 78: 722, 79: 778,
    80: 667, 81: 778, 82: 722, 83: 667, 84: 611, 85: 722, 86: 667, 87: 944,
    88: 667, 89: 667, 90: 611, 91: 333, 92: 278, 93: 333, 94: 584, 95: 556,
    96: 333, 97: 556, 98: 611, 99: 556, 100: 611, 101: 556, 102: 333, 103: 611,
    104: 611, 105: 278, 106: 278, 107: 556, 108: 278, 109: 889, 110: 611, 111: 611,
    112: 611, 113: 611, 114: 389, 115: 556, 116: 333, 117: 611, 118: 556, 119: 778,
    120: 556, 121: 556, 122: 500, 123: 389, 124: 280, 125: 389, 126: 584, 128: 722,
    129: 611, 130: 556, 131: 556, 132: 556, 133: 556, 134: 556, 135: 556, 136: 556,
    137: 556, 138: 556, 139: 278, 140: 278, 141: 278, 142: 722, 143: 722, 144: 667,
    145: 889, 146: 1000, 147: 611, 148: 611, 149: 611, 150: 611, 151: 611, 152: 556,
    153: 778, 154: 722, 155: 556, 156: 556, 157: 556, 159: 556, 160: 556, 161: 278,
    162: 611, 163: 611, 164: 611, 165: 722, 166: 370, 167: 365, 168: 611, 171: 834,
    172: 834, 173: 333, 225: 611, 230: 611, 241: 584, 246: 584, 248: 400, 250: 278,
}
# fmt: on

#: Advance width of each byte font 9 prints, in thousandths of the em (:data:`EM_UNITS`): the
#: Helvetica-Bold stand-in for CG Triumvirate Bold described in the module docstring.
FONT_9_ADVANCES: Mapping[int, int] = FrozenMapping(
    {byte: _HELVETICA_BOLD.get(byte, FONT_9_FALLBACK_ADVANCE) for byte in sorted(FONT_BYTES[FONT_9])}
)


def font9_points(points: int, profile: PrinterProfile) -> int:
    """Return the font 9 point size ``profile`` prints for ``points``: that size, or the nearest it has.

    The encoder sends the same size and reports the substitution (``font_size_substituted``).

    Args:
        points: The size the field asks for.
        profile: The model that prints it.

    Returns:
        ``points`` when the model has it, else its nearest size, the smaller of two equally near;
        ``points`` itself on a model with no font 9 size at all.

    Example:
        >>> from datamax_dpl.domain.profiles import get_profile
        >>> font9_points(8, get_profile("allegro-2")), font9_points(4, get_profile("allegro-2"))
        (8, 6)
    """
    sizes = sorted(set(profile.a_series_points) | set(profile.old_series_points))
    if points in sizes or not sizes:
        return points
    return min(sizes, key=lambda size: (abs(size - points), size))


def font9_em_dots(points: int, profile: PrinterProfile) -> float:
    """Return the em of font 9 at ``points`` on ``profile`` in dots: the printed size's points at its dpi."""
    return font9_points(points, profile) * profile.dpi / _POINTS_PER_INCH


def char_advances(element: Text, profile: PrinterProfile) -> tuple[int, ...]:
    """Return how many dots each character of ``element`` advances the next one, as ``profile`` prints it.

    A character a font cannot print counts as the ``?`` the encoder sends for it. Fonts 0-8 advance
    by their cell and its gap, times ``width_multiplier``; font 9 by :data:`FONT_9_ADVANCES` at the
    printed point size, each rounded to whole dots as a bitmap glyph is. A font 9 field with a
    ``font_id`` keeps the rough estimate of 0.6 em per character.

    Args:
        element: The text field.
        profile: The model that prints it.

    Returns:
        One advance per character, in dots.

    Example:
        >>> from datamax_dpl.domain.profiles import get_profile
        >>> char_advances(Text(x=0, y=0, text="Wi", font=9, points=8), get_profile("allegro-2"))
        (21, 6)
    """
    if element.font != FONT_9:
        cell = CELLS[element.font].at(profile.dpi)
        return ((cell.width + cell.gap) * element.width_multiplier,) * len(element.text)
    em = font9_em_dots(element.points or _DEFAULT_POINTS, profile)
    if element.font_id is not None:
        return (round(em * _UNKNOWN_FONT_ADVANCE),) * len(element.text)
    data = encode_for_bitmapped_font(element.text, font=FONT_9).data
    return tuple(round(FONT_9_ADVANCES[byte] * em / EM_UNITS) for byte in data)


def text_extent(element: Text, profile: PrinterProfile) -> int:
    """Return how many dots the printed line of ``element`` spans, from its first character to its last.

    The sum of the advances, less the gap after the last character of fonts 0-8, which prints no
    dot. A font 9 advance holds its glyph's own side bearings, which the stand-in table cannot
    separate.

    Example:
        >>> from datamax_dpl.domain.profiles import get_profile
        >>> text_extent(Text(x=0, y=0, text="ABC", font=2), get_profile("allegro-2"))
        34
    """
    total = sum(char_advances(element, profile))
    if element.font == FONT_9 or not element.text:
        return total
    return total - CELLS[element.font].at(profile.dpi).gap * element.width_multiplier


def align_shift_units(element: Text, ctx: RenderContext) -> int:
    """Return how many DPL units a field's record column lies left of ``x``: 0 for left-aligned text.

    Right-aligned text moves back by its whole :func:`text_extent`, centred text by half of it,
    each converted at the job's dots per unit and rounded to the nearest unit, so the line ends
    (or is centred) within half a unit of the dot ``x`` prints at. The encoder, the preview and
    the overlap and edge checks all place an aligned field by this one number.

    Args:
        element: The text field, after its placeholders are filled.
        ctx: The context the job is encoded with.

    Returns:
        The units to subtract from the column ``x`` is written at.
    """
    if element.align is TextAlign.LEFT:
        return 0
    extent = text_extent(element, ctx.profile)
    share = extent if element.align is TextAlign.RIGHT else extent / 2
    return round(share / ctx.dots_per_unit)


__all__ = [
    "CELLS",
    "EM_UNITS",
    "FONT_9_ADVANCES",
    "FONT_9_FALLBACK_ADVANCE",
    "CharCell",
    "FontCells",
    "align_shift_units",
    "char_advances",
    "font9_em_dots",
    "font9_points",
    "text_extent",
]
