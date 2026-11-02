"""Printer model profiles: what each Datamax model can do, as the DPL manual documents it.

A :class:`PrinterProfile` carries the limits (resolution, printable width, field counts, speed
and heat ranges, font 9 sizes) and a :class:`Feature` table for one model. The compatibility
layer checks a label against a profile and applies fallbacks. Every value is taken from
``docs/dpl-reference.md`` sections 1-3; that file names the manual's contradictions and the
reading chosen.

Two synthetic profiles exist besides the models: ``generic`` (what every model can do, the
safest output when the model is unknown) and ``permissive`` (no model limits, syntax checks only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple, TypeVar

from .enums import HardwareOption, ImageModule, ModelFamily, SpeedCode
from .label import DotSize
from .units import hundredths_to_mm


class Feature(str, Enum):
    """A DPL capability that not every model has.

    Example:
        >>> Feature.CUT_BY.value
        'cut_by'
    """

    CUT_BY = "cut_by"
    CUT_NOW = "cut_now"
    CUTTER_PULSE = "cutter_pulse"
    STOP_POSITION = "stop_position"
    PRESENT_DISTANCE = "present_distance"
    PAUSE_EACH_LABEL = "pause_each_label"
    SENSOR_SELECT = "sensor_select"
    ATTRIBUTE = "attribute"
    MIRROR = "mirror"
    FORMAT_METRIC = "format_metric"
    FIELD_TERMINATOR = "field_terminator"
    BACKUP_SPEED = "backup_speed"
    CIRCLE_POLYGON = "circle_polygon"
    SCALABLE_FONTS = "scalable_fonts"
    EXTENDED_BARCODES = "extended_barcodes"
    PDF417 = "pdf417"
    DATAMATRIX = "datamatrix"
    DEFAULT_BAR_WIDTHS = "default_bar_widths"
    STORED_FORMATS = "stored_formats"
    TIME_DATE = "time_date"
    CLEAR_MODULE = "clear_module"
    REMOVE_IMAGE = "remove_image"


class Firmware(NamedTuple):
    """A firmware version; versions compare as ``(major, minor)``, so 2.10 is newer than 2.09.

    Example:
        >>> Firmware(major=2, minor=10) > Firmware(major=2, minor=9)
        True
    """

    major: int
    minor: int


#: The dot size a format prints at without a ``D`` command: ``D22`` on most models, ``D11`` on some.
_D22 = DotSize(width=2, height=2)
_D11 = DotSize(width=1, height=1)


class Source(str, Enum):
    """Where a profile value comes from."""

    MANUAL = "manual"
    MEASURED = "measured"


@dataclass(frozen=True)
class Support:
    """Whether a model has a feature, and under which condition.

    Attributes:
        available: The model has the feature at all.
        min_firmware: Lowest firmware that has it, or None for every firmware.
        option: Hardware option the printer must have installed, or None.
        source: Whether the manual says so or a test print measured it.
    """

    available: bool
    min_firmware: Firmware | None = None
    option: HardwareOption | None = None
    source: Source = Source.MANUAL

    def describe(self) -> str:
        """Short text for tables: ``yes``, ``no``, ``fw 2.01``, ``option datamatrix``.

        Example:
            >>> Support(available=True, min_firmware=Firmware(major=2, minor=1)).describe()
            'fw 2.01'
        """
        if not self.available:
            return "no"
        if self.min_firmware is not None:
            return f"fw {self.min_firmware.major}.{self.min_firmware.minor:02d}"
        if self.option is not None:
            return f"option {self.option.value}"
        return "yes"


YES = Support(available=True)
NO = Support(available=False)


def from_firmware(major: int, minor: int) -> Support:
    """Support that starts at firmware ``major.minor``."""
    return Support(available=True, min_firmware=Firmware(major=major, minor=minor))


DATAMATRIX_OPTION = Support(available=True, option=HardwareOption.DATAMATRIX)


@dataclass(frozen=True)
class SpeedRange:
    """Speed letters a model accepts for one speed setting, and its default."""

    low: SpeedCode
    high: SpeedCode
    default: SpeedCode

    def contains(self, code: SpeedCode) -> bool:
        """True when ``code`` lies within the range.

        Example:
            >>> SpeedRange(SpeedCode("C"), SpeedCode("G"), SpeedCode("G")).contains(SpeedCode("K"))
            False
        """
        order = list(SpeedCode)
        return order.index(self.low) <= order.index(code) <= order.index(self.high)

    def clamp(self, code: SpeedCode) -> SpeedCode:
        """Return ``code`` limited to the range."""
        order = list(SpeedCode)
        index = min(max(order.index(code), order.index(self.low)), order.index(self.high))
        return order[index]


def _speeds(text: str) -> SpeedRange:
    """Parse ``"C-O K"`` (range, default) into a :class:`SpeedRange`."""
    span, default = text.split()
    low, _, high = span.partition("-")
    return SpeedRange(SpeedCode(low), SpeedCode(high or low), SpeedCode(default))


#: Font 9 point sizes of the A-series size codes (``A04`` ... ``A72``).
A_SERIES_POINTS: tuple[int, ...] = (4, 5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48, 72)
#: Font 9 point sizes of the old numbering ``000`` ... ``010``, in code order.
OLD_SERIES_POINTS: tuple[int, ...] = (5, 6, 8, 10, 12, 14, 18, 24, 30, 36, 48)


@dataclass(frozen=True)
class CalibrationDefaults:
    """How a model prints, as measured on one printer of it, used where the caller sets nothing.

    The neutral defaults print nominal lengths at nominal positions and leave the stop position to
    the printer. A model ships other values only when they were measured on paper; the printer's
    own calibration (``datamax-dpl calibrate``) always wins over them.

    Attributes:
        scale_across: Printed length divided by nominal length across the web.
        scale_along: The same along the feed.
        offset_across: Millimetres every field moves to the right.
        offset_along: Millimetres every field moves toward the label top.
        stop_position_mm: Tear-off stop position in mm, or None to keep the printer's setting.
        provenance: Where the values were measured; empty for the neutral defaults.
        image_memory_bytes: Bytes of image data one job may download, or None for no budget
            (see :mod:`~datamax_dpl.domain.image_budget`).
        image_memory_provenance: Where the image memory budget comes from; it was read on another
            day than the calibration, so it carries its own.

    Example:
        >>> CalibrationDefaults().scale_along
        1.0
        >>> CalibrationDefaults().image_memory_bytes is None
        True
    """

    scale_across: float = 1.0
    scale_along: float = 1.0
    offset_across: float = 0.0
    offset_along: float = 0.0
    stop_position_mm: float | None = None
    provenance: str = ""
    image_memory_bytes: int | None = None
    image_memory_provenance: str = ""


@dataclass(frozen=True)
class UndescribedBit:
    """A ``<STX>V`` bit whose option no datamax-dpl setting describes.

    Attributes:
        bit: The bit's position in the value.
        option: The option the manual names for it.
    """

    bit: int
    option: str


@dataclass(frozen=True)
class SoftwareSwitchBits:
    """Which bit of the ``<STX>V`` value sets which printer option on one model (manual p.42).

    ``<STX>V`` overrides every bit at once until the next power-up, so a value can only be built
    where datamax-dpl knows what each bit means: a bit no setting describes (a ribbon option)
    would be switched off blindly.

    Attributes:
        cutter: Bit of the cutter option.
        internal_batch: Bit of internal batch mode, or None where the model has no such bit.
        label_present: Bit of the label-present sensor, or None where the model has no such bit.
        undescribed: Bits whose option no datamax-dpl setting describes; any makes the layout
            unbuildable.

    Example:
        >>> SoftwareSwitchBits(cutter=0, internal_batch=1).buildable
        True
    """

    cutter: int
    internal_batch: int | None = None
    label_present: int | None = None
    undescribed: tuple[UndescribedBit, ...] = ()

    @property
    def buildable(self) -> bool:
        """True when every bit of the layout maps to something datamax-dpl sets."""
        return not self.undescribed

    def describe(self) -> str:
        """Short text for tables: the bits in order, or why no value is ever sent.

        Example:
            >>> SoftwareSwitchBits(cutter=0, label_present=2).describe()
            '0 cutter, 2 label present'
        """
        if not self.buildable:
            return "never sent: " + ", ".join(f"bit {bit.bit} {bit.option}" for bit in self.undescribed)
        bits = ((self.cutter, "cutter"), (self.internal_batch, "internal batch"), (self.label_present, "label present"))
        named = sorted((bit, option) for bit, option in bits if bit is not None)
        return ", ".join(f"{bit} {option}" for bit, option in named)


@dataclass(frozen=True)
class PrinterProfile:
    """Everything the encoder needs to know about one printer model.

    Attributes:
        id: Stable profile name used in config, JSON and ``--model``.
        name: Model name as the manual writes it.
        dpi: Print resolution in dots per inch.
        max_column: Largest column value in hundredths of an inch.
        print_width_in: Printable width in inches.
        max_fields: Largest number of records in one label format.
        max_field_chars: Largest number of data characters in one record.
        heat_max: Largest heat value (``H00``-``Hnn``).
        print_speed: Print speed range.
        slew_speed: Feed (slew) speed range.
        backup_speed: Backfeed speed range, or None when the model has no backfeed speed.
        a_series_points: Font 9 point sizes this model has as ``Annn`` codes (empty: none).
        old_series_points: Font 9 point sizes this model has in the old ``000``-``010`` numbering.
        features: Support for each :class:`Feature`.
        synthetic: True for ``generic`` and ``permissive``, which are not real models.
        family: The family whose feature set the model has; None for ``generic`` and
            ``permissive``, which belong to none.
        default_dot_size: The dot size the printer uses when a format sends no ``D`` command:
            2x2 on every model except the DMX 600/800 (manual, ``D``).
        inch_unit_dots: Whole dots one 1/100 in prints as, on a model known to round it to whole
            dots; None where the exact 1/100 in is assumed. Measured 2 on the Allegro 2 only.
        calibration: Scale, offset and stop position measured for this model, applied where the
            render options leave them unset.
        image_module: The memory module image downloads go to, the model's built-in
            RAM (manual, Appendix L); None for a model with no built-in module, where the render
            options must name the plug-in module fitted.
        ram_modules: The model's built-in RAM modules (manual, Appendix L), the only
            modules a job clears with ``<STX>q``: a plug-in module may be flash, which every
            clear wears.
        max_format_rows: Highest dot row along the feed a format may reach at dot height 1,
            counted as the encoder commands it (calibration scale and offset included), past
            which the printer may cut it; a label dot_size of height h multiplies it by h. None
            where no limit is known.
        start_of_print_mm: The manual's default start of print (``<STX>O``) in mm, which a job
            sets so it does not inherit another program's; None where the default depends on a
            DIP switch (Prodigy Max) or the models differ (``generic``), and the job sends none.
        max_label_length_mm: The manual's default maximum label length (``<STX>M``) in mm; None
            where no single default applies.
        feed_rate: Speed the feed button feeds at (``<STX>S``); the model's slew default, since
            the manual gives no feed rate default. None where no single default applies.
        software_switches: Bit layout of ``<STX>V`` on this model, or None where the manual's
            table has no column for it.
    """

    id: str
    name: str
    dpi: float
    max_column: int
    print_width_in: float
    max_fields: int
    max_field_chars: int
    heat_max: int
    print_speed: SpeedRange
    slew_speed: SpeedRange
    backup_speed: SpeedRange | None
    a_series_points: tuple[int, ...]
    old_series_points: tuple[int, ...]
    features: dict[Feature, Support] = field(default_factory=dict[Feature, Support])
    synthetic: bool = False
    family: ModelFamily | None = None
    default_dot_size: DotSize = _D22
    inch_unit_dots: int | None = None
    calibration: CalibrationDefaults = CalibrationDefaults()
    image_module: ImageModule | None = ImageModule.A
    ram_modules: frozenset[ImageModule] = frozenset({ImageModule.A})
    max_format_rows: int | None = None
    start_of_print_mm: float | None = None
    max_label_length_mm: float | None = None
    feed_rate: SpeedCode | None = None
    software_switches: SoftwareSwitchBits | None = None

    def support(self, feature: Feature) -> Support:
        """Return how this model supports ``feature`` (missing entries mean no)."""
        return self.features.get(feature, NO)


# Feature columns of docs/dpl-reference.md section 2, one set per model family. Removing one
# image by name (<STX>R<name>) is "only available on the standard Prodigy" (manual p.36), which
# instead has no clear module (<STX>q, p.35); the Prodigy 152 is its 152 dpi variant (Appendix K),
# not a Prodigy Plus, so it shares both. <STX>O, M, S and V carry no "not available" note, so every
# model is sent them and no Feature stands for them.
_ALL_FEATURES_YES: dict[Feature, Support] = dict.fromkeys(Feature, YES) | {
    Feature.CUTTER_PULSE: NO,
    Feature.PRESENT_DISTANCE: NO,
    Feature.DATAMATRIX: DATAMATRIX_OPTION,
    Feature.REMOVE_IMAGE: NO,
}

_FAMILY_FEATURES: dict[ModelFamily, dict[Feature, Support]] = {
    ModelFamily.ALLEGRO: _ALL_FEATURES_YES
    | {
        Feature.ATTRIBUTE: NO,
        Feature.MIRROR: NO,
        Feature.FORMAT_METRIC: NO,
        Feature.FIELD_TERMINATOR: NO,
        Feature.BACKUP_SPEED: NO,
        Feature.CIRCLE_POLYGON: NO,
        Feature.SCALABLE_FONTS: NO,
        Feature.EXTENDED_BARCODES: NO,
        Feature.PDF417: from_firmware(3, 20),
    },
    ModelFamily.PRODIGY: dict.fromkeys(Feature, NO)
    | {
        Feature.CUTTER_PULSE: YES,
        Feature.DEFAULT_BAR_WIDTHS: YES,
        Feature.PDF417: from_firmware(3, 20),
        Feature.DATAMATRIX: DATAMATRIX_OPTION,
        Feature.REMOVE_IMAGE: YES,
    },
    ModelFamily.PRODIGY_PLUS: _ALL_FEATURES_YES
    | {
        Feature.CUT_NOW: from_firmware(1, 18),
        Feature.CUTTER_PULSE: YES,
        Feature.PAUSE_EACH_LABEL: NO,
        Feature.ATTRIBUTE: NO,
        Feature.MIRROR: NO,
        Feature.FORMAT_METRIC: NO,
        Feature.CIRCLE_POLYGON: NO,
        Feature.SCALABLE_FONTS: NO,
        Feature.EXTENDED_BARCODES: NO,
        Feature.PDF417: from_firmware(3, 20),
        Feature.DEFAULT_BAR_WIDTHS: NO,
    },
    ModelFamily.DMX_400: _ALL_FEATURES_YES
    | {Feature.MIRROR: NO, Feature.CIRCLE_POLYGON: from_firmware(2, 1), Feature.SCALABLE_FONTS: NO},
    ModelFamily.DMX_430: _ALL_FEATURES_YES | {Feature.MIRROR: NO, Feature.SCALABLE_FONTS: NO},
    ModelFamily.DMX_600: _ALL_FEATURES_YES
    | {Feature.MIRROR: from_firmware(2, 1), Feature.SCALABLE_FONTS: from_firmware(2, 1)},
    ModelFamily.DMX_800: _ALL_FEATURES_YES,
    ModelFamily.MAX_PE: _ALL_FEATURES_YES,
    ModelFamily.PE: _ALL_FEATURES_YES | {Feature.PRESENT_DISTANCE: YES},
    ModelFamily.OVATION: _ALL_FEATURES_YES | {Feature.PRESENT_DISTANCE: YES, Feature.SCALABLE_FONTS: NO},
    ModelFamily.TITAN: _ALL_FEATURES_YES,
}

# A72 exists on the DMX 600/800 only; PE42/PE43 and the Prodigy Max lack A04 and A05.
_A72 = 72
_SMALLEST_KEPT_ON_PE = 6
_A_FULL = tuple(p for p in A_SERIES_POINTS if p != _A72)
_A_NO_SMALL = tuple(p for p in _A_FULL if p >= _SMALLEST_KEPT_ON_PE)
_OLD_NO_5PT = OLD_SERIES_POINTS[1:]


@dataclass(frozen=True)
class _Row:
    id: str
    name: str
    family: ModelFamily
    dpi: float
    max_column: int
    width: float
    fields: int
    chars: int
    heat: int
    speeds: tuple[str, str, str | None]
    a_points: tuple[int, ...]
    old_points: tuple[int, ...] = OLD_SERIES_POINTS


_ROWS: tuple[_Row, ...] = (
    _Row(
        "allegro", "Allegro", ModelFamily.ALLEGRO, 203, 410, 4.10, 200, 5000, 20, ("C C", "C C", "C C"), (), _OLD_NO_5PT
    ),
    _Row(
        "allegro-2",
        "Allegro 2",
        ModelFamily.ALLEGRO,
        203,
        410,
        4.10,
        200,
        5000,
        20,
        ("C-E C", "C-E C", "C-E C"),
        (),
        _OLD_NO_5PT,
    ),
    _Row("prodigy", "Prodigy", ModelFamily.PRODIGY, 203, 410, 4.10, 100, 3000, 20, ("A-G E", "A-G G", None), ()),
    _Row(
        "prodigy-152", "Prodigy 152", ModelFamily.PRODIGY, 152, 410, 4.15, 100, 3000, 20, ("A-G E", "A-G G", None), ()
    ),
    _Row(
        "prodigy-plus",
        "Prodigy Plus",
        ModelFamily.PRODIGY_PLUS,
        203,
        410,
        4.10,
        200,
        5000,
        20,
        ("C-O K", "C-O K", "C-I E"),
        (),
        _OLD_NO_5PT,
    ),
    _Row(
        "prodigy-plus-152",
        "Prodigy Plus 152",
        ModelFamily.PRODIGY_PLUS,
        152,
        410,
        4.21,
        200,
        5000,
        20,
        ("C-O K", "C-O K", "C-I E"),
        (),
        _OLD_NO_5PT,
    ),
    _Row("dmx-400", "DMX 400", ModelFamily.DMX_400, 203, 410, 4.10, 199, 5000, 20, ("C-G G", "C-I K", "C-I E"), ()),
    _Row(
        "dmx-430",
        "DMX 430",
        ModelFamily.DMX_430,
        289.56,
        398,
        3.98,
        399,
        10000,
        20,
        ("C-K G", "C-K K", "C-I E"),
        _A_FULL,
    ),
    _Row(
        "dmx-600",
        "DMX 600",
        ModelFamily.DMX_600,
        300,
        640,
        6.40,
        399,
        10000,
        20,
        ("C-O K", "C-S O", "C-I E"),
        A_SERIES_POINTS,
    ),
    _Row(
        "dmx-800",
        "DMX 800",
        ModelFamily.DMX_800,
        300,
        860,
        8.53,
        399,
        10000,
        20,
        ("C-I G", "C-K K", "C-I E"),
        A_SERIES_POINTS,
    ),
    _Row(
        "prodigy-max-203",
        "Prodigy Max 203",
        ModelFamily.MAX_PE,
        203,
        410,
        4.10,
        319,
        7000,
        30,
        ("C-S K", "C-S O", "C-I I"),
        _A_NO_SMALL,
    ),
    _Row(
        "prodigy-max-300",
        "Prodigy Max 300",
        ModelFamily.MAX_PE,
        300,
        410,
        4.16,
        319,
        7000,
        30,
        ("C-O K", "C-O O", "C-I E"),
        _A_NO_SMALL,
    ),
    _Row("pe42", "PE42", ModelFamily.PE, 203, 410, 4.10, 319, 7000, 30, ("C-S K", "C-S O", "C-I E"), _A_NO_SMALL),
    _Row("pe43", "PE43", ModelFamily.PE, 300, 410, 4.16, 319, 7000, 30, ("C-O K", "C-O M", "C-I E"), _A_NO_SMALL),
    _Row(
        "ovation", "Ovation!", ModelFamily.OVATION, 203, 410, 4.10, 319, 7000, 30, ("A-C C", "A-C C", "A-C C"), _A_FULL
    ),
    _Row(
        "ovation-300",
        "Ovation! 300",
        ModelFamily.OVATION,
        300,
        410,
        4.16,
        319,
        7000,
        30,
        ("A-C C", "A-C C", "A-C C"),
        _A_FULL,
    ),
    _Row(
        "ovation-2",
        "Ovation 2",
        ModelFamily.OVATION,
        203,
        425,
        4.256,
        319,
        7000,
        30,
        ("A-C C", "A-C C", "A-C C"),
        _A_FULL,
    ),
    _Row(
        "titan-6200",
        "Titan 6200",
        ModelFamily.TITAN,
        203,
        661,
        6.62,
        399,
        10000,
        30,
        ("C-K G", "C-K K", "C-G C"),
        _A_FULL,
    ),
)


#: The manual's ``D`` command: "Default setting: D22 for all printers except the DMX 600/800".
_FAMILIES_DEFAULTING_TO_D11 = frozenset({ModelFamily.DMX_600, ModelFamily.DMX_800})

#: Models measured to print one 1/100 in as whole dots: a 787/100 in line printed as 1574 dot
#: rows on an Allegro 2 (2 per unit, not 2.03). Other models keep the exact unit until measured;
#: a per-printer scale factor absorbs what remains.
_INCH_UNIT_DOTS: dict[str, int] = {"allegro-2": 2}

#: Calibration measured on paper per model. One printer stands for its model here: the platen
#: roller and the sensor position differ between printers, so ``datamax-dpl calibrate`` still
#: measures each one, and these values only replace the nominal ones where nothing was measured.
_CALIBRATION: dict[str, CalibrationDefaults] = {
    "allegro-2": CalibrationDefaults(
        scale_along=0.976,
        offset_along=-0.25,
        stop_position_mm=66.8,
        provenance="measured on one Allegro 2, firmware CA02 03.04, 2026-10-29 and 2026-10-30",
        # The free bytes <STX>W reported for module A, less a margin for what the printer adds per
        # stored image and for graphics stored later: the module was not empty when it was read.
        image_memory_bytes=48631 - 4096,
        image_memory_provenance=(
            "48631 free bytes of module A read on one Allegro 2 holding 11 stored graphics, "
            "2026-10-30, less a 4096-byte margin"
        ),
    ),
}


#: The module of each model's built-in RAM, from the manual's Appendix L. On the DMX 600/800
#: modules A and B are plug-ins and D is non-volatile NVRAM, so images go to E, its RAM, and vanish
#: at power-off as they do on the Allegro 2. The Prodigy and Prodigy Plus have no built-in module
#: (A and B are plug-ins): None, so a job downloading images there needs the module named.
#: Only A has been seen on a printer (an Allegro 2).
_IMAGE_MODULE: dict[str, ImageModule | None] = {
    "allegro": ImageModule.A,
    "allegro-2": ImageModule.A,
    "dmx-400": ImageModule.A,
    "dmx-430": ImageModule.A,
    "prodigy-max-203": ImageModule.A,
    "prodigy-max-300": ImageModule.A,
    "pe42": ImageModule.A,
    "pe43": ImageModule.A,
    "ovation": ImageModule.A,
    "ovation-300": ImageModule.A,
    "ovation-2": ImageModule.A,
    "titan-6200": ImageModule.A,
    "dmx-600": ImageModule.E,
    "dmx-800": ImageModule.E,
    "prodigy": None,
    "prodigy-152": None,
    "prodigy-plus": None,
    "prodigy-plus-152": None,
}


#: The built-in RAM modules of each model, from the manual's Appendix L (p.189): "Internal RAM" A
#: on the Allegro and DMX 400/430, RAM E on the DMX 600/800, RAM A on the Prodigy Max, PE42/PE43,
#: Ovation, Ovation 2 and 6200. Every other module is a plug-in (B everywhere; A on the Prodigy,
#: Prodigy Plus and DMX 600/800, where A is not listed as RAM), the internal non-volatile NVRAM D,
#: or C, which stands for whichever module was made the default (manual, ``<STX>X``). A plug-in
#: module may be flash, which each ``<STX>q`` wears (manual p.35), and NVRAM or C are not known to
#: be RAM, so a job clears only the modules listed here. The Prodigy and Prodigy Plus have none.
_RAM_MODULES: dict[str, frozenset[ImageModule]] = {
    "allegro": frozenset({ImageModule.A}),
    "allegro-2": frozenset({ImageModule.A}),
    "dmx-400": frozenset({ImageModule.A}),
    "dmx-430": frozenset({ImageModule.A}),
    "prodigy-max-203": frozenset({ImageModule.A}),
    "prodigy-max-300": frozenset({ImageModule.A}),
    "pe42": frozenset({ImageModule.A}),
    "pe43": frozenset({ImageModule.A}),
    "ovation": frozenset({ImageModule.A}),
    "ovation-300": frozenset({ImageModule.A}),
    "ovation-2": frozenset({ImageModule.A}),
    "titan-6200": frozenset({ImageModule.A}),
    "dmx-600": frozenset({ImageModule.E}),
    "dmx-800": frozenset({ImageModule.E}),
    "prodigy": frozenset(),
    "prodigy-152": frozenset(),
    "prodigy-plus": frozenset(),
    "prodigy-plus-152": frozenset(),
}


#: The highest dot row along the feed a format may reach, at dot height 1, counted as the encoder
#: commands it. For the Allegro 2 it is the lowest row any test format was cut at (one Allegro 2,
#: firmware CA02 03.04, 2026-10-30; docs/dpl-reference.md lists the formats). Where the printer
#: cuts depends on the format's content in a way that is not understood: a tall element running up
#: from the bottom was cut at rows 1648 to 1652, a format with nothing tall printed to row 1675.
#: So 1648 is conservative, and max_format_rows raises it. No other model has one: their manuals
#: give none and none was measured.
_MAX_FORMAT_ROWS: dict[str, int] = {"allegro-2": 1648}


#: The default start of print, ``<STX>O``, from the manual's table (p.32), in hundredths of an
#: inch: Prodigy 0250; "Prodigy Plus, 6200" 0110; "All others" 0220. The table's "Prodigy Max" is
#: "110/250, DIP switch selectable" (footnote 1), so which a printer uses is not known: None sends
#: none and keeps the printer's own. Footnote 2, "Menu selectable, if equipped", marks the 6200 and
#: "All others", the Allegro 2 among them: an operator may have set another value on the front
#: panel, and a job sending O0220 overrides it, which is why sending O by default waits for a
#: comparison print on a real printer. The Prodigy 152 and Prodigy Plus 152 are the 152 dpi
#: variants of the Prodigy and Prodigy Plus (Appendix K), so they take those rows; Ovation! 300
#: takes "All others".
_START_OF_PRINT: dict[str, int | None] = {
    "allegro": 220,
    "allegro-2": 220,
    "prodigy": 250,
    "prodigy-152": 250,
    "prodigy-plus": 110,
    "prodigy-plus-152": 110,
    "dmx-400": 220,
    "dmx-430": 220,
    "dmx-600": 220,
    "dmx-800": 220,
    "prodigy-max-203": None,
    "prodigy-max-300": None,
    "pe42": 220,
    "pe43": 220,
    "ovation": 220,
    "ovation-300": 220,
    "ovation-2": 220,
    "titan-6200": 110,
}

#: The default maximum label length, ``<STX>M``, from the manual's table (p.30), in hundredths of
#: an inch: 12 in for "Prodigy Plus, Prodigy, 400, 430, Allegro, Allegro 2, Ovation, and Ovation
#: 2", 16 in for "Prodigy Max, PE42, PE43, 600, 800 and 6200". The 152 dpi variants take their
#: model's row, and Ovation! 300 the Ovation's.
_TWELVE_INCHES = 1200
_SIXTEEN_INCHES = 1600
_MAX_LABEL_LENGTH: dict[str, int] = {
    "allegro": _TWELVE_INCHES,
    "allegro-2": _TWELVE_INCHES,
    "prodigy": _TWELVE_INCHES,
    "prodigy-152": _TWELVE_INCHES,
    "prodigy-plus": _TWELVE_INCHES,
    "prodigy-plus-152": _TWELVE_INCHES,
    "dmx-400": _TWELVE_INCHES,
    "dmx-430": _TWELVE_INCHES,
    "dmx-600": _SIXTEEN_INCHES,
    "dmx-800": _SIXTEEN_INCHES,
    "prodigy-max-203": _SIXTEEN_INCHES,
    "prodigy-max-300": _SIXTEEN_INCHES,
    "pe42": _SIXTEEN_INCHES,
    "pe43": _SIXTEEN_INCHES,
    "ovation": _TWELVE_INCHES,
    "ovation-300": _TWELVE_INCHES,
    "ovation-2": _TWELVE_INCHES,
    "titan-6200": _SIXTEEN_INCHES,
}

#: The ``<STX>V`` bit layouts of the manual's bit assignment table (p.42), one per column.
_SWITCHES_400_600_800_6200 = SoftwareSwitchBits(cutter=0, internal_batch=1, label_present=2)
_SWITCHES_PRODIGY_PLUS = SoftwareSwitchBits(
    cutter=0, label_present=2, undescribed=(UndescribedBit(bit=1, option="insert ribbon sense"),)
)
_SWITCHES_PRODIGY = SoftwareSwitchBits(
    cutter=0, internal_batch=3, label_present=2, undescribed=(UndescribedBit(bit=1, option="invert ribbon saver"),)
)
_SWITCHES_ALLEGRO = SoftwareSwitchBits(cutter=0, internal_batch=1)
_SWITCHES_MAX_OVATION_PE = SoftwareSwitchBits(cutter=0, label_present=2)

#: The ``<STX>V`` layout per model. The table has no DMX 430 column, so the 430 has None rather
#: than the 400's layout: a value built from a guessed bit must never be sent. The 152 dpi
#: variants take their model's column, Ovation! 300 the Ovation's.
_SOFTWARE_SWITCHES: dict[str, SoftwareSwitchBits | None] = {
    "allegro": _SWITCHES_ALLEGRO,
    "allegro-2": _SWITCHES_ALLEGRO,
    "prodigy": _SWITCHES_PRODIGY,
    "prodigy-152": _SWITCHES_PRODIGY,
    "prodigy-plus": _SWITCHES_PRODIGY_PLUS,
    "prodigy-plus-152": _SWITCHES_PRODIGY_PLUS,
    "dmx-400": _SWITCHES_400_600_800_6200,
    # Manual p.42: the STX V bit table has no DMX 430 column; no bit is guessed for it.
    "dmx-430": None,
    "dmx-600": _SWITCHES_400_600_800_6200,
    "dmx-800": _SWITCHES_400_600_800_6200,
    "prodigy-max-203": _SWITCHES_MAX_OVATION_PE,
    "prodigy-max-300": _SWITCHES_MAX_OVATION_PE,
    "pe42": _SWITCHES_MAX_OVATION_PE,
    "pe43": _SWITCHES_MAX_OVATION_PE,
    "ovation": _SWITCHES_MAX_OVATION_PE,
    "ovation-300": _SWITCHES_MAX_OVATION_PE,
    "ovation-2": _SWITCHES_MAX_OVATION_PE,
    "titan-6200": _SWITCHES_400_600_800_6200,
}


def _feed_rate(slew: SpeedRange) -> SpeedCode | None:
    """Return the slew default as the feed rate, or None where it lies outside the slew range.

    Appendix M (p.194) lists the DMX 400's slew range as C-I with default K, outside that range:
    the table contradicts itself, so no feed rate is sent rather than a value one column rules
    out, and the job names ``feed_rate`` as the key to set.
    """
    return slew.default if slew.contains(slew.default) else None


def _start_of_print_mm(model: str) -> float | None:
    hundredths = _START_OF_PRINT[model]
    return None if hundredths is None else hundredths_to_mm(hundredths)


def _build(row: _Row) -> PrinterProfile:
    print_speed, slew_speed, backup_speed = row.speeds
    return PrinterProfile(
        id=row.id,
        name=row.name,
        dpi=row.dpi,
        max_column=row.max_column,
        print_width_in=row.width,
        max_fields=row.fields,
        max_field_chars=row.chars,
        heat_max=row.heat,
        print_speed=_speeds(print_speed),
        slew_speed=_speeds(slew_speed),
        backup_speed=_speeds(backup_speed) if backup_speed else None,
        a_series_points=row.a_points,
        old_series_points=row.old_points,
        features=dict(_FAMILY_FEATURES[row.family]),
        family=row.family,
        default_dot_size=_D11 if row.family in _FAMILIES_DEFAULTING_TO_D11 else _D22,
        inch_unit_dots=_INCH_UNIT_DOTS.get(row.id),
        calibration=_CALIBRATION.get(row.id, CalibrationDefaults()),
        image_module=_IMAGE_MODULE[row.id],
        ram_modules=_RAM_MODULES[row.id],
        max_format_rows=_MAX_FORMAT_ROWS.get(row.id),
        start_of_print_mm=_start_of_print_mm(row.id),
        max_label_length_mm=hundredths_to_mm(_MAX_LABEL_LENGTH[row.id]),
        # The manual gives no feed rate default; the feed button feeding at the slew speed the
        # printer uses anyway is the reading that changes nothing a user would notice.
        feed_rate=_feed_rate(_speeds(slew_speed)),
        software_switches=_SOFTWARE_SWITCHES[row.id],
    )


MODEL_PROFILES: dict[str, PrinterProfile] = {row.id: _build(row) for row in _ROWS}


_T = TypeVar("_T")


def _common_support(feature: Feature) -> Support:
    """Support every model has unconditionally, else none (firmware/option gates count as no)."""
    every = all(p.support(feature) == YES for p in MODEL_PROFILES.values())
    return YES if every else NO


def _common_value(values: list[_T]) -> _T | None:
    """Return the value every model has, or None when two models differ."""
    return values[0] if values and all(value == values[0] for value in values) else None


def _intersect_speeds(ranges: list[SpeedRange]) -> SpeedRange:
    order = list(SpeedCode)
    low = max((r.low for r in ranges), key=order.index)
    high = min((r.high for r in ranges), key=order.index)
    return SpeedRange(low, high, low)


def _generic() -> PrinterProfile:
    models = list(MODEL_PROFILES.values())
    return PrinterProfile(
        id="generic",
        name="Any model (common subset)",
        dpi=203,
        max_column=min(p.max_column for p in models),
        print_width_in=min(p.print_width_in for p in models),
        max_fields=min(p.max_fields for p in models),
        max_field_chars=min(p.max_field_chars for p in models),
        heat_max=min(p.heat_max for p in models),
        print_speed=_intersect_speeds([p.print_speed for p in models]),
        slew_speed=_intersect_speeds([p.slew_speed for p in models]),
        backup_speed=None,
        a_series_points=(),
        old_series_points=_OLD_NO_5PT,
        features={feature: _common_support(feature) for feature in Feature},
        synthetic=True,
        # Not the common subset: the Prodigy family has no built-in module, so a strict subset
        # would refuse every image. A is the built-in module of most models.
        image_module=ImageModule.A,
        # Generic has no clear module anyway; the modules every model has as RAM are none.
        ram_modules=frozenset(m for m in ImageModule if all(m in p.ram_modules for p in models)),
        # A setting goes out only where every model shares the default: a job for an unknown
        # model must not move another model's start of print or feed rate off its own default.
        start_of_print_mm=_common_value([p.start_of_print_mm for p in models]),
        max_label_length_mm=_common_value([p.max_label_length_mm for p in models]),
        feed_rate=_common_value([p.feed_rate for p in models]),
        software_switches=_common_value([p.software_switches for p in models]),
    )


def _permissive() -> PrinterProfile:
    full = SpeedRange(SpeedCode.IPS_1_0, SpeedCode.IPS_10_0, SpeedCode.IPS_2_0)
    return PrinterProfile(
        id="permissive",
        name="No model checks",
        dpi=203,
        max_column=9999,
        print_width_in=99.99,
        max_fields=9999,
        max_field_chars=10000,
        heat_max=30,
        print_speed=full,
        slew_speed=full,
        backup_speed=full,
        a_series_points=A_SERIES_POINTS,
        old_series_points=OLD_SERIES_POINTS,
        features=dict.fromkeys(Feature, YES),
        synthetic=True,
        image_module=ImageModule.A,
    )


PROFILES: dict[str, PrinterProfile] = {"generic": _generic(), "permissive": _permissive(), **MODEL_PROFILES}


class UnknownModelError(LookupError):
    """Raised for a model id that has no profile."""


def get_profile(model: str) -> PrinterProfile:
    """Return the profile for ``model`` (case-insensitive, spaces or underscores allowed).

    Raises:
        UnknownModelError: No profile has that id.

    Example:
        >>> get_profile("DMX 600").id
        'dmx-600'
        >>> get_profile("generic").features[Feature.CIRCLE_POLYGON].available
        False
    """
    key = model.strip().lower().replace(" ", "-").replace("_", "-")
    try:
        return PROFILES[key]
    except KeyError:
        known = ", ".join(PROFILES)
        msg = f"unknown printer model {model!r}; known: {known}"
        raise UnknownModelError(msg) from None


def list_profiles() -> list[PrinterProfile]:
    """Return every profile, synthetic ones first."""
    return list(PROFILES.values())


def parse_firmware(text: str) -> Firmware | None:
    """Parse ``"2.01"`` into major 2, minor 1; an empty string means unknown.

    Raises:
        ValueError: The text is not ``<major>.<minor>``.

    Example:
        >>> parse_firmware("3.2")
        Firmware(major=3, minor=20)
        >>> parse_firmware("") is None
        True
    """
    stripped = text.strip()
    if not stripped:
        return None
    match = _FIRMWARE.fullmatch(stripped)
    if match is None:
        msg = f"firmware must look like 2.01, got {text!r}"
        raise ValueError(msg)
    # Versions are decimals: "3.2" is 3.20, so a one-digit minor is padded on the right.
    return Firmware(major=int(match["major"]), minor=int(match["minor"].ljust(_MINOR_DIGITS, "0")))


_MINOR_DIGITS = 2
_FIRMWARE = re.compile(r"(?P<major>[0-9]{1,3})\.(?P<minor>[0-9]{1,3})")


__all__ = [
    "A_SERIES_POINTS",
    "MODEL_PROFILES",
    "OLD_SERIES_POINTS",
    "PROFILES",
    "CalibrationDefaults",
    "Feature",
    "Firmware",
    "PrinterProfile",
    "SoftwareSwitchBits",
    "Source",
    "SpeedRange",
    "Support",
    "UndescribedBit",
    "UnknownModelError",
    "get_profile",
    "list_profiles",
    "parse_firmware",
]
