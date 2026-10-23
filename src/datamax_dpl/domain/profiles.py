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

from .enums import SpeedCode


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


Firmware = tuple[int, int]


class Source(str, Enum):
    """Where a profile value comes from."""

    MANUAL = "manual"
    MEASURED = "measured"


@dataclass(frozen=True)
class Support:
    """Whether a model has a feature, and under which condition.

    Attributes:
        available: The model has the feature at all.
        min_firmware: Lowest firmware ``(major, minor)`` that has it, or None for every firmware.
        option: Name of a hardware option the printer must have installed (``datamatrix``).
        source: Whether the manual says so or a test print measured it.
    """

    available: bool
    min_firmware: Firmware | None = None
    option: str | None = None
    source: Source = Source.MANUAL

    def describe(self) -> str:
        """Short text for tables: ``yes``, ``no``, ``fw 2.01``, ``option datamatrix``.

        Example:
            >>> Support(available=True, min_firmware=(2, 1)).describe()
            'fw 2.01'
        """
        if not self.available:
            return "no"
        if self.min_firmware is not None:
            return f"fw {self.min_firmware[0]}.{self.min_firmware[1]:02d}"
        if self.option is not None:
            return f"option {self.option}"
        return "yes"


YES = Support(available=True)
NO = Support(available=False)


def from_firmware(major: int, minor: int) -> Support:
    """Support that starts at firmware ``major.minor``."""
    return Support(available=True, min_firmware=(major, minor))


DATAMATRIX_OPTION = Support(available=True, option="datamatrix")


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

    def support(self, feature: Feature) -> Support:
        """Return how this model supports ``feature`` (missing entries mean no)."""
        return self.features.get(feature, NO)


# Feature columns of docs/dpl-reference.md section 2, one set per model family.
_ALL_FEATURES_YES: dict[Feature, Support] = dict.fromkeys(Feature, YES) | {
    Feature.CUTTER_PULSE: NO,
    Feature.PRESENT_DISTANCE: NO,
    Feature.DATAMATRIX: DATAMATRIX_OPTION,
}

_FAMILY_FEATURES: dict[str, dict[Feature, Support]] = {
    "allegro": _ALL_FEATURES_YES
    | {
        Feature.ATTRIBUTE: NO,
        Feature.MIRROR: NO,
        Feature.FORMAT_METRIC: NO,
        Feature.FIELD_TERMINATOR: NO,
        Feature.BACKUP_SPEED: NO,
        Feature.CIRCLE_POLYGON: NO,
        Feature.SCALABLE_FONTS: NO,
        Feature.EXTENDED_BARCODES: NO,
        Feature.PDF417: from_firmware(3, 2),
    },
    "prodigy": dict.fromkeys(Feature, NO)
    | {
        Feature.CUTTER_PULSE: YES,
        Feature.DEFAULT_BAR_WIDTHS: YES,
        Feature.PDF417: from_firmware(3, 2),
        Feature.DATAMATRIX: DATAMATRIX_OPTION,
    },
    "prodigy-plus": _ALL_FEATURES_YES
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
        Feature.PDF417: from_firmware(3, 2),
        Feature.DEFAULT_BAR_WIDTHS: NO,
    },
    "dmx-400": _ALL_FEATURES_YES
    | {Feature.MIRROR: NO, Feature.CIRCLE_POLYGON: from_firmware(2, 1), Feature.SCALABLE_FONTS: NO},
    "dmx-430": _ALL_FEATURES_YES | {Feature.MIRROR: NO, Feature.SCALABLE_FONTS: NO},
    "dmx-600": _ALL_FEATURES_YES | {Feature.MIRROR: from_firmware(2, 1), Feature.SCALABLE_FONTS: from_firmware(2, 1)},
    "dmx-800": _ALL_FEATURES_YES,
    "max-pe": _ALL_FEATURES_YES,
    "pe": _ALL_FEATURES_YES | {Feature.PRESENT_DISTANCE: YES},
    "ovation": _ALL_FEATURES_YES | {Feature.PRESENT_DISTANCE: YES, Feature.SCALABLE_FONTS: NO},
    "titan": _ALL_FEATURES_YES,
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
    family: str
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
    _Row("allegro", "Allegro", "allegro", 203, 410, 4.10, 200, 5000, 20, ("C C", "C C", "C C"), (), _OLD_NO_5PT),
    _Row(
        "allegro-2", "Allegro 2", "allegro", 203, 410, 4.10, 200, 5000, 20, ("C-E C", "C-E C", "C-E C"), (), _OLD_NO_5PT
    ),
    _Row("prodigy", "Prodigy", "prodigy", 203, 410, 4.10, 100, 3000, 20, ("A-G E", "A-G G", None), ()),
    _Row("prodigy-152", "Prodigy 152", "prodigy", 152, 410, 4.15, 100, 3000, 20, ("A-G E", "A-G G", None), ()),
    _Row(
        "prodigy-plus",
        "Prodigy Plus",
        "prodigy-plus",
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
        "prodigy-plus",
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
    _Row("dmx-400", "DMX 400", "dmx-400", 203, 410, 4.10, 199, 5000, 20, ("C-G G", "C-I K", "C-I E"), ()),
    _Row("dmx-430", "DMX 430", "dmx-430", 289.56, 398, 3.98, 399, 10000, 20, ("C-K G", "C-K K", "C-I E"), _A_FULL),
    _Row("dmx-600", "DMX 600", "dmx-600", 300, 640, 6.40, 399, 10000, 20, ("C-O K", "C-S O", "C-I E"), A_SERIES_POINTS),
    _Row("dmx-800", "DMX 800", "dmx-800", 300, 860, 8.53, 399, 10000, 20, ("C-I G", "C-K K", "C-I E"), A_SERIES_POINTS),
    _Row(
        "prodigy-max-203",
        "Prodigy Max 203",
        "max-pe",
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
        "max-pe",
        300,
        410,
        4.16,
        319,
        7000,
        30,
        ("C-O K", "C-O O", "C-I E"),
        _A_NO_SMALL,
    ),
    _Row("pe42", "PE42", "pe", 203, 410, 4.10, 319, 7000, 30, ("C-S K", "C-S O", "C-I E"), _A_NO_SMALL),
    _Row("pe43", "PE43", "pe", 300, 410, 4.16, 319, 7000, 30, ("C-O K", "C-O M", "C-I E"), _A_NO_SMALL),
    _Row("ovation", "Ovation!", "ovation", 203, 410, 4.10, 319, 7000, 30, ("A-C C", "A-C C", "A-C C"), _A_FULL),
    _Row("ovation-300", "Ovation! 300", "ovation", 300, 410, 4.16, 319, 7000, 30, ("A-C C", "A-C C", "A-C C"), _A_FULL),
    _Row("ovation-2", "Ovation 2", "ovation", 203, 425, 4.256, 319, 7000, 30, ("A-C C", "A-C C", "A-C C"), _A_FULL),
    _Row("titan-6200", "Titan 6200", "titan", 203, 661, 6.62, 399, 10000, 30, ("C-K G", "C-K K", "C-G C"), _A_FULL),
)


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
    )


MODEL_PROFILES: dict[str, PrinterProfile] = {row.id: _build(row) for row in _ROWS}


def _common_support(feature: Feature) -> Support:
    """Support every model has unconditionally, else none (firmware/option gates count as no)."""
    every = all(p.support(feature) == YES for p in MODEL_PROFILES.values())
    return YES if every else NO


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
    )


PROFILES: dict[str, PrinterProfile] = {"generic": _generic(), "permissive": _permissive(), **MODEL_PROFILES}


class UnknownModelError(KeyError):
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
    """Parse ``"2.01"`` into ``(2, 1)``; an empty string means unknown.

    Raises:
        ValueError: The text is not ``<major>.<minor>``.

    Example:
        >>> parse_firmware("3.2")
        (3, 2)
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
    return int(match["major"]), int(match["minor"])


_FIRMWARE = re.compile(r"(?P<major>[0-9]{1,3})\.(?P<minor>[0-9]{1,3})")


__all__ = [
    "A_SERIES_POINTS",
    "MODEL_PROFILES",
    "OLD_SERIES_POINTS",
    "PROFILES",
    "Feature",
    "Firmware",
    "PrinterProfile",
    "Source",
    "SpeedRange",
    "Support",
    "UnknownModelError",
    "get_profile",
    "list_profiles",
    "parse_firmware",
]
