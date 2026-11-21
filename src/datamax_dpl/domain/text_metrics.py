"""How wide a text field prints: the advance of each character in printer dots.

Fonts 0-8 are fixed bitmaps. Their character cells (height, width and the gap after each
character) come from the font tables in ``docs/dpl-reference.md`` section 6, at 203, 300 and
289.56 dpi, so their widths are exact.

Font 9 is the resident CG Triumvirate Bold, a proportional face: the manual's table "Font 9
Bitmapped Resident fonts specifications" names it for both size numberings (printed page 174, PDF
page 184), and its note on font 9 says "each character will take up a different amount of space
when printed. For example, the letter W will be larger than the letter I" (printed page 119, PDF
page 129). The manual publishes no widths. The widths here were measured on paper on an Allegro 2
(203 dpi, firmware CA02 03.04, 2026-11-21) for the capitals, the digits, ``,`` ``-`` ``.`` ``/``
and the space, at 12, 18, 24 and 30 pt
(``docs/measurements/2026-11-21-allegro-2-CA02-03.04-font-9-widths``), one table per printed size
(:data:`FONT_9_PRINTED_ADVANCES`): the widths do not scale from one size to the next. A printed
size without a table takes the nearest measured size's advances scaled by the em, and so does
another resolution; both are unverified. Every other character keeps the advance width of Adobe's
Helvetica-Bold (the Core 14 AFM file, version 001.007, as matplotlib ships it), since CG Triumvirate
is Agfa's Helvetica design: a stand-in, unverified on paper (:data:`FONT_9_STAND_IN_ADVANCES`;
:data:`FONT_9_MEASURED` names the measured bytes). The point size is the em (``points * dpi / 72``
dots).

How the printer rounds, fitted to those lines: up to 12 pt (:data:`WHOLE_DOT_MAX_POINTS`) each
character advances by its own width rounded to whole dots; from 18 pt up the characters advance by
their exact widths and only the position each reaches is rounded. 14 pt, in between, is
unmeasured. One 12 pt advance rounds against its width: ``/`` advances 11 dots where its width
gives 10; only a 203 dpi model printing 12 pt takes that, other sizes and resolutions scale the
width (unverified). A line's ink ends before its last advance by that glyph's right side bearing
(:data:`FONT_9_RIGHT_BEARINGS`, from the same AFM file): an M ends 68 thousandths of the em before
its advance, on paper as in the AFM at 30 pt (12 pt line ends fall about 1.4 dots short, which a
12 pt M ending a dot earlier would explain; unresolved). The Allegro 2 starts a font 9 line 2 dots right of its record
at 30 pt (:class:`~.profiles.Font9PrintShift`, measured apart): the 30 pt lines were read with it
taken out, and :func:`units_left_of_x` writes the record that much further left.

Text alignment (:class:`~.enums.TextAlign`) moves a field's reference point back by these widths:
DPL places a field at its lower-left corner and has no justification of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import accumulate, pairwise
from types import MappingProxyType
from typing import TYPE_CHECKING

from .charset import encode_for_bitmapped_font
from .encode_common import position_units
from .enums import Rotation, TextAlign
from .frozen_mapping import FrozenMapping
from .label import FONT_9, Text
from .units import Axis

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .profiles import PrinterProfile
    from .render_context import RenderContext

_DPI_300 = 300
_DPI_289 = 289
_POINTS_PER_INCH = 72
#: The AFM widths and bearings here are thousandths of the em, as an AFM file states them.
EM_UNITS = 1000
#: Font 9 point size a field with neither ``points`` nor ``font_id`` is drawn at; validation
#: refuses such a field, so this only keeps the arithmetic total.
_DEFAULT_POINTS = 10
#: Share of the em one character of a font 9 field takes when its font is unknown (a ``font_id``
#: names a downloaded or cartridge font, whose widths nothing here knows).
_UNKNOWN_FONT_ADVANCE = 0.6


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

# Generated from phvb8a.afm: each code page 850 byte font 9 prints (:data:`~.charset.FONT_CODECS`),
# decoded to its character, named by the Adobe Glyph List, and that glyph's WX. Helvetica-Bold has a
# glyph for every one of them.
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
    153: 778, 154: 722, 155: 611, 156: 556, 157: 778, 158: 584, 159: 556, 160: 556,
    161: 278, 162: 611, 163: 611, 164: 611, 165: 722, 166: 370, 167: 365, 168: 611,
    169: 737, 171: 834, 172: 834, 173: 333, 181: 722, 182: 722, 183: 722, 184: 737,
    189: 556, 190: 556, 198: 556, 199: 722, 208: 611, 209: 722, 210: 667, 211: 667,
    212: 667, 213: 278, 214: 278, 215: 278, 216: 278, 222: 278, 224: 778, 225: 611,
    226: 778, 227: 778, 228: 611, 229: 778, 230: 611, 231: 611, 232: 667, 233: 722,
    234: 722, 235: 722, 236: 556, 237: 667, 241: 584, 243: 834, 246: 584, 247: 333,
    248: 400, 249: 333, 250: 278,
}
# fmt: on

# Measured on paper on an Allegro 2 (docs/measurements/2026-11-21-allegro-2-CA02-03.04-font-9-widths):
# each byte's advance in dots at 203 dpi, before the printer rounds it (module docstring), per
# printed size, as scripts/fit_fill.py.txt writes it: unrounded but for the last step, to a
# millionth of a dot. 12 pt: widths that round to the advance each 12 pt line gave, the middle of that
# range for a character measured at 12 pt only, and the 30 pt width scaled for one measured at both;
# "/" keeps that scaled width here, and the 11 dots its 12 pt line gave are kept apart
# (_BITMAP_ADVANCES_203_12). 30 pt: the line-end lines for M, the digits, "," "." "/" space and
# S T K B O X (read to 0.1 mm over 9 to 25 characters), the fill-and-V round for the rest. 18 and
# 24 pt: the fill-and-V round (an advance to 2 dots / N over N = 10 to 49 characters); the label of
# U V W X Y at 24 pt was printed and read twice, and takes the mean of its two readings. The ten
# digits share one advance and the period the comma's at 12 and 30 pt, as in the design; the fill
# round printed each and found the same at every size. Lines of the 30 pt line-end round were read
# with the print shift (profiles.Font9PrintShift: 2 dots at 30 pt) taken out; the fill round reads
# it out too.
# fmt: off
_PRINTED_ADVANCES_203: dict[int, dict[int, float]] = {
    12: {
        32: 9.921984, 44: 9.921984, 45: 10.0, 46: 9.921984, 47: 9.921984, 48: 18.27179, 49: 18.27179, 50: 18.27179,
        51: 18.27179, 52: 18.27179, 53: 18.27179, 54: 18.27179, 55: 18.27179, 56: 18.27179, 57: 18.27179, 65: 23.0,
        66: 22.639028, 67: 24.0, 68: 24.0, 69: 22.0, 70: 20.0, 71: 26.0, 72: 25.0, 73: 9.0, 74: 17.0, 75: 21.893096,
        76: 19.0, 77: 28.166741, 78: 25.0, 79: 25.767528, 80: 22.0, 81: 26.0, 82: 24.0, 83: 21.894023,
        84: 20.252834, 85: 25.0, 86: 22.0, 87: 31.0, 88: 21.894023, 89: 23.0, 90: 20.0,
    },
    18: {
        32: 14.974161, 44: 14.974161, 45: 14.996301, 46: 14.974161, 47: 15.951939, 48: 27.909885, 49: 27.909885,
        50: 27.909885, 51: 27.909885, 52: 27.909885, 53: 27.909885, 54: 27.909885, 55: 27.909885, 56: 27.909885,
        57: 27.909885, 65: 32.938803, 66: 33.991434, 67: 36.102069, 68: 36.102069, 69: 33.091863, 70: 30.083511,
        71: 39.04925, 72: 37.04925, 73: 13.915046, 74: 25.99374, 75: 32.891863, 76: 28.949446, 77: 41.98915,
        78: 37.04925, 79: 38.931603, 80: 32.691862, 81: 39.04925, 82: 36.990958, 83: 33.091863, 84: 29.901693,
        85: 37.519838, 86: 33.091863, 87: 46.559804, 88: 33.091863, 89: 34.201961, 90: 30.083511,
    },
    24: {
        32: 19.963121, 44: 19.963121, 45: 20.023727, 46: 19.963121, 47: 20.811606, 48: 36.932389, 49: 36.932389,
        50: 36.932389, 51: 36.932389, 52: 36.932389, 53: 36.932389, 54: 36.932389, 55: 36.932389, 56: 36.932389,
        57: 36.932389, 65: 44.913071, 66: 44.913071, 67: 48.060231, 68: 48.060231, 69: 44.0522, 70: 40.798938,
        71: 52.06525, 72: 49.137154, 73: 18.02175, 74: 33.935947, 75: 44.0522, 76: 37.928412, 77: 56.798455,
        78: 49.291, 79: 51.898583, 80: 44.0522, 81: 52.06525, 82: 48.060231, 83: 44.0522, 84: 41.048938,
        85: 49.137154, 86: 44.0522, 87: 63.4783, 88: 44.0522, 89: 45.055929, 90: 40.798938,
    },
    30: {
        32: 24.804961, 44: 24.804961, 45: 24.86915, 46: 24.804961, 47: 24.804961, 48: 45.679476, 49: 45.679476,
        50: 45.679476, 51: 45.679476, 52: 45.679476, 53: 45.679476, 54: 45.679476, 55: 45.679476, 56: 45.679476,
        57: 45.679476, 65: 54.702614, 66: 56.597569, 67: 59.772875, 68: 59.772875, 69: 54.702614, 70: 50.644062,
        71: 64.636528, 72: 60.972875, 73: 22.776027, 74: 42.781917, 75: 54.73274, 76: 47.671442, 77: 70.416852,
        78: 60.972875, 79: 64.41882, 80: 54.702614, 81: 64.636528, 82: 59.572875, 83: 54.735056, 84: 50.632084,
        85: 60.572875, 86: 54.702614, 87: 77.466094, 88: 54.735056, 89: 56.884432, 90: 50.644062,
    },
}
# fmt: on

# Generated from phvb8a.afm: WX minus the right edge of the glyph's bounding box, for every byte in
# _HELVETICA_BOLD. The space has no ink; it ends a line at its advance, as a cell of fonts 0-8 does.
# fmt: off
_HELVETICA_BOLD_RIGHT_BEARINGS: dict[int, int] = {
    32: 0, 33: 89, 34: 98, 35: 18, 36: 33, 37: 28, 38: 21, 39: 70, 40: 19, 41: 35, 42: 27, 43: 40,
    44: 64, 45: 27, 46: 64, 47: -33, 48: 32, 49: 178, 50: 45, 51: 40, 52: 30, 53: 40, 54: 36,
    55: 28, 56: 32, 57: 34, 58: 91, 59: 91, 60: 38, 61: 40, 62: 38, 63: 55, 64: 119, 65: 20, 66: 53,
    67: 38, 68: 37, 69: 46, 70: 24, 71: 65, 72: 71, 73: 64, 74: 72, 75: 0, 76: 28, 77: 68, 78: 68,
    79: 44, 80: 40, 81: 41, 82: 45, 83: 38, 84: 13, 85: 71, 86: 19, 87: 15, 88: 14, 89: 14, 90: 25,
    91: 24, 92: -33, 93: 63, 94: 62, 95: 0, 96: 108, 97: 29, 98: 33, 99: 32, 100: 60, 101: 28,
    102: 15, 103: 58, 104: 65, 105: 69, 106: 69, 107: -6, 108: 69, 109: 63, 110: 65, 111: 33,
    112: 33, 113: 59, 114: 16, 115: 37, 116: 24, 117: 66, 118: 13, 119: 9, 120: 15, 121: 17,
    122: 20, 123: 24, 124: 84, 125: 48, 126: 61, 128: 38, 129: 66, 130: 28, 131: 29, 132: 29,
    133: 29, 134: 29, 135: 32, 136: 28, 137: 28, 138: 28, 139: -22, 140: -38, 141: 69, 142: 20,
    143: 20, 144: 46, 145: 31, 146: 46, 147: 33, 148: 33, 149: 33, 150: 66, 151: 66, 152: 17,
    153: 44, 154: 71, 155: 22, 156: 15, 157: 34, 158: 39, 159: 40, 160: 29, 161: -51, 162: 33,
    163: 66, 164: 65, 165: 68, 166: 23, 167: 5, 168: 60, 169: -11, 171: 40, 172: 68, 173: 89,
    181: 20, 182: 20, 183: 20, 184: -12, 189: 32, 190: -9, 198: 29, 199: 20, 208: 33, 209: 37,
    210: 46, 211: 46, 212: 46, 213: 69, 214: -51, 215: -38, 216: -22, 222: 64, 224: 44, 225: 32,
    226: 44, 227: 44, 228: 33, 229: 44, 230: 66, 231: 33, 232: 40, 233: 71, 234: 71, 235: 71,
    236: 17, 237: 14, 241: 40, 243: 35, 246: 40, 247: 88, 248: 57, 249: 6, 250: 58,
}
# fmt: on

# Generated from phvb8a.afm with the right bearings above: the left edge of the glyph's bounding box
# (llx), how far right of its origin a glyph's ink starts (negative: left of it). The space has no
# ink and starts a line at its origin.
# fmt: off
_HELVETICA_BOLD_LEFT_BEARINGS: dict[int, int] = {
    32: 0, 33: 90, 34: 98, 35: 18, 36: 30, 37: 28, 38: 54, 39: 70, 40: 35, 41: 19, 42: 27, 43: 40,
    44: 64, 45: 27, 46: 64, 47: -33, 48: 32, 49: 69, 50: 26, 51: 27, 52: 27, 53: 27, 54: 31, 55: 25,
    56: 32, 57: 30, 58: 92, 59: 92, 60: 38, 61: 40, 62: 38, 63: 60, 64: 118, 65: 20, 66: 76, 67: 44,
    68: 76, 69: 76, 70: 76, 71: 44, 72: 71, 73: 64, 74: 22, 75: 87, 76: 76, 77: 69, 78: 69, 79: 44,
    80: 76, 81: 44, 82: 76, 83: 39, 84: 14, 85: 72, 86: 19, 87: 16, 88: 14, 89: 15, 90: 25, 91: 63,
    92: -33, 93: 24, 94: 62, 95: 0, 96: -23, 97: 29, 98: 61, 99: 34, 100: 34, 101: 23, 102: 10,
    103: 40, 104: 65, 105: 69, 106: 3, 107: 69, 108: 69, 109: 64, 110: 65, 111: 34, 112: 62,
    113: 34, 114: 64, 115: 30, 116: 10, 117: 66, 118: 13, 119: 10, 120: 15, 121: 10, 122: 20,
    123: 48, 124: 84, 125: 24, 126: 61, 128: 44, 129: 66, 130: 23, 131: 29, 132: 29, 133: 29,
    134: 29, 135: 34, 136: 23, 137: 23, 138: 23, 139: -21, 140: -37, 141: -50, 142: 20, 143: 20,
    144: 76, 145: 29, 146: 5, 147: 34, 148: 34, 149: 34, 150: 66, 151: 66, 152: 10, 153: 44,
    154: 72, 155: 22, 156: 28, 157: 33, 158: 40, 159: -10, 160: 29, 161: 69, 162: 34, 163: 66,
    164: 65, 165: 69, 166: 22, 167: 6, 168: 55, 169: -11, 171: 26, 172: 26, 173: 90, 181: 20,
    182: 20, 183: 20, 184: -11, 189: 34, 190: -9, 198: 29, 199: 20, 208: 34, 209: -5, 210: 76,
    211: 76, 212: 76, 213: 69, 214: 64, 215: -37, 216: -21, 222: -50, 224: 44, 225: 69, 226: 44,
    227: 44, 228: 34, 229: 44, 230: 66, 231: 62, 232: 76, 233: 72, 234: 72, 235: 72, 236: 10,
    237: 15, 241: 40, 243: 16, 246: 40, 247: 6, 248: 57, 249: 6, 250: 58,
}
# fmt: on

#: Resolution the font 9 advances in :data:`FONT_9_PRINTED_ADVANCES` were measured at.
MEASURED_DPI = 203
#: The one advance a 12 pt line measured that no width gives: "/" advances 11 dots at 12 pt, where
#: the width it shares with 30 pt rounds to 10. It was measured at that size and resolution only, so
#: only a 203 dpi model printing 12 pt takes it; any other size or resolution scales the width.
_BITMAP_ADVANCES_203_12: Mapping[int, int] = FrozenMapping({47: 11})
#: The paper-measured font 9 advance of each byte in :data:`FONT_9_MEASURED`, in dots at
#: :data:`MEASURED_DPI` before the printer rounds it, by printed point size (12, 18, 24 and 30).
FONT_9_PRINTED_ADVANCES: Mapping[int, Mapping[int, float]] = FrozenMapping(
    {points: FrozenMapping(advances) for points, advances in _PRINTED_ADVANCES_203.items()}
)
#: The bytes whose font 9 advance was measured on paper, at every size in
#: :data:`FONT_9_PRINTED_ADVANCES`; every other byte takes the unverified stand-in.
FONT_9_MEASURED: frozenset[int] = frozenset(_PRINTED_ADVANCES_203[12])
#: Advance width of each byte font 9 prints in Helvetica-Bold, in thousandths of the em
#: (:data:`EM_UNITS`): the unverified stand-in a byte outside :data:`FONT_9_MEASURED` advances by.
FONT_9_STAND_IN_ADVANCES: Mapping[int, int] = FrozenMapping(_HELVETICA_BOLD)
#: How far a glyph's ink ends before its advance, in thousandths of the em (negative: past it).
FONT_9_RIGHT_BEARINGS: Mapping[int, int] = FrozenMapping(_HELVETICA_BOLD_RIGHT_BEARINGS)
#: How far right of its origin a glyph's ink starts, in thousandths of the em (negative: left of it).
#: From the same AFM file as the right bearings; unlike those, not checked on paper.
FONT_9_LEFT_BEARINGS: Mapping[int, int] = FrozenMapping(_HELVETICA_BOLD_LEFT_BEARINGS)
#: Largest font 9 point size whose characters each advance by whole dots; larger sizes round the
#: position each character reaches instead (module docstring). 12 pt advances by whole dots on
#: paper and 18, 24 and 30 pt do not (at 18 pt U, W, P and Y each lie clearly between two), so the
#: paper puts this bound anywhere from 12 to 17; 14 pt, the one size in between, is unmeasured. 12
#: is the smallest bound the record allows. No test pins the rounding from 13 to 17 pt: the whole
#: suite passes with this set to 13, 14 or 17.
WHOLE_DOT_MAX_POINTS = 12


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


def font9_measured_points(points: int) -> int:
    """Return the measured font 9 size whose advances a font 9 line printed at ``points`` takes.

    That size itself when :data:`FONT_9_PRINTED_ADVANCES` has it, else the nearest measured size,
    the smaller of two equally near (unverified on paper: the widths do not scale exactly).

    Args:
        points: The printed point size.

    Returns:
        A key of :data:`FONT_9_PRINTED_ADVANCES`.

    Example:
        >>> [font9_measured_points(points) for points in (24, 14, 15, 48)]
        [24, 12, 12, 30]
    """
    return min(FONT_9_PRINTED_ADVANCES, key=lambda size: (abs(size - points), size))


def _font9_exact_advances(data: bytes, points: int, *, em: float, dpi: float) -> list[float]:
    # The measured size's advances, scaled by the em: exactly the table at a measured size on a
    # 203 dpi model, the nearest size's advances stretched to the printed em anywhere else.
    measured = font9_measured_points(points)
    scale = em / (measured * MEASURED_DPI / _POINTS_PER_INCH)
    table = FONT_9_PRINTED_ADVANCES[measured]
    bitmap: Mapping[int, int] = _BITMAP_ADVANCES_203_12 if (round(dpi), points) == (MEASURED_DPI, 12) else {}
    return [
        bitmap.get(byte, table[byte] * scale) if byte in table else FONT_9_STAND_IN_ADVANCES[byte] * em / EM_UNITS
        for byte in data
    ]


def char_advances(element: Text, profile: PrinterProfile) -> tuple[int, ...]:
    """Return how many dots each character of ``element`` advances the next one, as ``profile`` prints it.

    A character a font cannot print counts as the ``?`` the encoder sends for it. Fonts 0-8 advance
    by their cell and its gap, times ``width_multiplier``; font 9 by :data:`FONT_9_PRINTED_ADVANCES`
    at the printed point size (:func:`font9_measured_points`) or the stand-in, rounded as the module
    docstring says. A font 9 field with a ``font_id`` keeps the rough estimate of 0.6 em per
    character.

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
    points = font9_points(element.points or _DEFAULT_POINTS, profile)
    em = font9_em_dots(points, profile)
    if element.font_id is not None:
        return (round(em * _UNKNOWN_FONT_ADVANCE),) * len(element.text)
    data = encode_for_bitmapped_font(element.text, font=FONT_9).data
    exact = _font9_exact_advances(data, points, em=em, dpi=profile.dpi)
    if points <= WHOLE_DOT_MAX_POINTS:
        return tuple(round(advance) for advance in exact)
    reached = [0, *(round(position) for position in accumulate(exact))]
    return tuple(end - start for start, end in pairwise(reached))


def text_extent(element: Text, profile: PrinterProfile) -> int:
    """Return how many dots the printed line of ``element`` spans, from its first character to its last.

    The sum of the advances, less what follows the last character's ink: the gap after a cell of
    fonts 0-8, the right side bearing (:data:`FONT_9_RIGHT_BEARINGS`) of a font 9 glyph, rounded
    to whole dots.

    Example:
        >>> from datamax_dpl.domain.profiles import get_profile
        >>> text_extent(Text(x=0, y=0, text="ABC", font=2), get_profile("allegro-2"))
        34
        >>> text_extent(Text(x=0, y=0, text="MM", font=9, points=12), get_profile("allegro-2"))
        54
    """
    total = sum(char_advances(element, profile))
    if not element.text:
        return total
    if element.font != FONT_9:
        return total - CELLS[element.font].at(profile.dpi).gap * element.width_multiplier
    if element.font_id is not None:
        return total
    last = encode_for_bitmapped_font(element.text[-1], font=FONT_9).data[0]
    em = font9_em_dots(element.points or _DEFAULT_POINTS, profile)
    return total - round(FONT_9_RIGHT_BEARINGS[last] * em / EM_UNITS)


def font9_print_shift_dots(element: Text, profile: PrinterProfile) -> int:
    """Return how many dots right of its record's column ``profile`` prints ``element``: 0 but for font 9.

    The model's measured :class:`~.profiles.Font9PrintShift` at the printed point size, for an
    upright field in the resident font 9. A turned field or one in a downloaded font (``font_id``)
    was never measured and keeps 0, as does every model without a measured shift.

    Example:
        >>> from datamax_dpl.domain.profiles import get_profile
        >>> line = Text(x=0, y=0, text="5 STK", font=9, points=30)
        >>> font9_print_shift_dots(line, get_profile("allegro-2")), font9_print_shift_dots(line, get_profile("dmx-600"))
        (2, 0)
    """
    shift = profile.font9_print_shift
    if shift is None or element.font != FONT_9 or element.font_id is not None or element.rotation is not Rotation.DEG_0:
        return 0
    return shift.at(font9_points(element.points or _DEFAULT_POINTS, profile))


def units_left_of_x(element: Text, ctx: RenderContext) -> int:
    """Return how many DPL units left of ``x``'s column a field's record is written: 0 for most left-aligned text.

    Right-aligned text moves back by its whole :func:`text_extent`, which ends at the last glyph's
    ink. Centred text moves back by half of the ink's span: in font 9 that span starts at the first
    glyph's left side bearing (:data:`FONT_9_LEFT_BEARINGS`), so half of that bearing is added to
    half the extent; fonts 0-8 are centred by their cells. Font 9 also moves back by the dots the
    model prints it right of its record (:func:`font9_print_shift_dots`), so its glyphs land where
    ``x`` says. The sum is converted at the job's dots per unit and rounded to the nearest unit once.
    The line's ink ends (or is centred, or starts) within about one unit of the dot ``x`` prints at:
    half a unit from rounding, plus the rounding of units to whole dots (up to 2 dots measured on a
    DMX 430 in inch format, whose 1/100 in unit is 2.9 dots; in metric format its 1/10 mm unit is
    1.14 dots). The encoder, the preview and the overlap and edge checks all place a field by this
    one number (:func:`text_column`).

    Args:
        element: The text field, after its placeholders are filled.
        ctx: The context the job is encoded with.

    Returns:
        The units to subtract from the column ``x`` is written at.
    """
    share = 0.0
    if element.align is TextAlign.RIGHT:
        share = text_extent(element, ctx.profile)
    elif element.align is TextAlign.CENTER:
        share = (text_extent(element, ctx.profile) + font9_left_bearing_dots(element, ctx.profile)) / 2
    return round((share + font9_print_shift_dots(element, ctx.profile)) / ctx.dots_per_unit)


def font9_left_bearing_dots(element: Text, profile: PrinterProfile) -> float:
    """Return how many dots right of its origin the first glyph of a font 9 line starts its ink: 0 otherwise.

    The first glyph's left side bearing (:data:`FONT_9_LEFT_BEARINGS`) at the printed size, unrounded,
    so a centred line adds it to its extent before the one rounding to units. Fonts 0-8, a
    downloaded font (``font_id``) and an empty line keep 0, as does a leading space, whose line starts
    at its origin just as a trailing space ends one at its advance.

    Example:
        >>> from datamax_dpl.domain.profiles import get_profile
        >>> round(font9_left_bearing_dots(Text(x=0, y=0, text="MMM", font=9, points=30), get_profile("allegro-2")), 2)
        5.84
        >>> font9_left_bearing_dots(Text(x=0, y=0, text="MMM", font=2), get_profile("allegro-2"))
        0.0
    """
    if element.font != FONT_9 or element.font_id is not None or not element.text:
        return 0.0
    first = encode_for_bitmapped_font(element.text[0], font=FONT_9).data[0]
    em = font9_em_dots(element.points or _DEFAULT_POINTS, profile)
    return FONT_9_LEFT_BEARINGS[first] * em / EM_UNITS


def label_edge_column(ctx: RenderContext) -> int:
    """Return the column the label's left edge lies at, in the job's DPL units.

    0 without an offset across; a positive offset moves the label right of the printhead's first
    column, a negative one left of it, where no record can be written.
    """
    return ctx.dpl_units(ctx.shifted(0.0, axis=Axis.ACROSS), axis=Axis.ACROSS)


def text_column(element: Text, ctx: RenderContext) -> int:
    """Return the column a text field's record is written at, recording nothing.

    ``x``'s column (:func:`~.encode_common.position_units`) moved back by
    :func:`units_left_of_x`, kept on the label and on the printhead: a line that would start
    left of either starts at the later of the two, where the encoder writes it after refusing it.

    Example:
        >>> from datamax_dpl.domain.label import Label
        >>> from datamax_dpl.domain.render import job_context
        >>> from datamax_dpl.domain.render_context import RenderOptions
        >>> line = Text(x=10, y=0, text="ABC", font=2, align=TextAlign.RIGHT)
        >>> text_column(line, job_context(Label(elements=[line]), RenderOptions(model="allegro-2")))
        23
    """
    column = position_units(element.x, axis=Axis.ACROSS, ctx=ctx) - units_left_of_x(element, ctx)
    return max(column, label_edge_column(ctx), 0)


__all__ = [
    "CELLS",
    "EM_UNITS",
    "FONT_9_LEFT_BEARINGS",
    "FONT_9_MEASURED",
    "FONT_9_PRINTED_ADVANCES",
    "FONT_9_RIGHT_BEARINGS",
    "FONT_9_STAND_IN_ADVANCES",
    "MEASURED_DPI",
    "WHOLE_DOT_MAX_POINTS",
    "CharCell",
    "FontCells",
    "char_advances",
    "font9_em_dots",
    "font9_left_bearing_dots",
    "font9_measured_points",
    "font9_points",
    "font9_print_shift_dots",
    "label_edge_column",
    "text_column",
    "text_extent",
    "units_left_of_x",
]
