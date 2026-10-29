"""Type-safe domain enums: configuration display, label geometry, DPL codes and policies.

Every fixed vocabulary the label model and the encoder share lives here, so a value that reaches
the printer is always one of a closed set. The DPL letters and digits each member stands for are
listed in ``docs/dpl-reference.md``.
"""

from __future__ import annotations

from enum import Enum, IntEnum


class OutputFormat(str, Enum):
    """Output format options for configuration display.

    Example:
        >>> OutputFormat.HUMAN.value
        'human'
        >>> OutputFormat.JSON == "json"
        True
    """

    HUMAN = "human"
    JSON = "json"


class DeployTarget(str, Enum):
    """Configuration deployment target layers.

    Example:
        >>> DeployTarget.USER.value
        'user'
    """

    APP = "app"
    HOST = "host"
    USER = "user"


class Unit(str, Enum):
    """Unit every length in a label is written in.

    Example:
        >>> Unit("mm") is Unit.MM
        True
    """

    MM = "mm"
    INCH = "in"
    DOTS = "dots"


class Rotation(IntEnum):
    """Field rotation in degrees, as written in JSON; :attr:`dpl_digit` is the DPL rotation digit.

    Example:
        >>> Rotation(90).dpl_digit
        2
        >>> Rotation.from_dpl_digit(4)
        <Rotation.DEG_270: 270>
    """

    DEG_0 = 0
    DEG_90 = 90
    DEG_180 = 180
    DEG_270 = 270

    @property
    def dpl_digit(self) -> int:
        """The rotation digit (1-4) that starts a DPL record."""
        return self.value // 90 + 1

    @classmethod
    def from_dpl_digit(cls, digit: int) -> Rotation:
        """Return the rotation a DPL record's first digit (1-4) stands for."""
        return cls((digit - 1) * 90)


class FillPattern(IntEnum):
    """Fill of a circle, polygon or filled box; the value is the DPL pattern number.

    Example:
        >>> FillPattern.SOLID.value
        1
    """

    NONE = 0
    SOLID = 1
    SHADE_6 = 2
    SHADE_12 = 3
    SHADE_25 = 4
    SHADE_38 = 5
    SHADE_50 = 6
    DIAMONDS = 7
    CIRCLES = 8
    RIGHT_DIAGONAL = 9
    LEFT_DIAGONAL = 10
    GRID = 11


class Attribute(IntEnum):
    """How a field combines with what is already printed (format command ``An``).

    Example:
        >>> Attribute.INVERSE.value
        5
    """

    XOR = 1
    TRANSPARENT = 2
    OPAQUE = 3
    INVERSE = 5


class SpeedCode(str, Enum):
    """DPL speed letter; the member name gives the speed in inches per second.

    JSON and the CLI use the letter (the value). :data:`SPEED_IPS` maps each member to its speed.

    Example:
        >>> SpeedCode("C") is SpeedCode.IPS_2_0
        True
        >>> SPEED_IPS[SpeedCode.IPS_10_0]
        10.0
    """

    IPS_1_0 = "A"
    IPS_1_5 = "B"
    IPS_2_0 = "C"
    IPS_2_5 = "D"
    IPS_3_0 = "E"
    IPS_3_5 = "F"
    IPS_4_0 = "G"
    IPS_4_5 = "H"
    IPS_5_0 = "I"
    IPS_5_5 = "J"
    IPS_6_0 = "K"
    IPS_6_5 = "L"
    IPS_7_0 = "M"
    IPS_7_5 = "N"
    IPS_8_0 = "O"
    IPS_8_5 = "P"
    IPS_9_0 = "Q"
    IPS_9_5 = "R"
    IPS_10_0 = "S"


#: Inches per second for each speed letter (A = 1.0, then 0.5 per letter).
SPEED_IPS: dict[SpeedCode, float] = {code: 1.0 + 0.5 * index for index, code in enumerate(SpeedCode)}


class MediaSensor(str, Enum):
    """How the printer finds the start of the next label.

    Example:
        >>> MediaSensor.GAP.value
        'gap'
    """

    GAP = "gap"
    REFLECTIVE = "reflective"
    CONTINUOUS = "continuous"


class FallbackPolicy(str, Enum):
    """What happens when a label asks for a feature the chosen model does not have.

    Attributes:
        DEGRADE: Apply the documented fallback and report it as a warning.
        STRICT: Refuse the label; every deviation is an error.

    Example:
        >>> FallbackPolicy("strict") is FallbackPolicy.STRICT
        True
    """

    DEGRADE = "degrade"
    STRICT = "strict"


class Severity(str, Enum):
    """Weight of a finding: ``error`` stops rendering, the others are reported.

    Example:
        >>> Severity.ERROR.value
        'error'
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LineDirection(str, Enum):
    """Orientation of an axis-aligned line.

    Example:
        >>> LineDirection.HORIZONTAL.value
        'horizontal'
    """

    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class FindingCode(str, Enum):
    """Stable identifier of a finding, the value scripts and LLMs match on.

    The values are part of the JSON output and of ``docs/capabilities.md``, so a member is never
    renamed. Build a message with ``code.value``: under the ``str, Enum`` form an f-string of the
    member reads ``FindingCode.X`` on Python 3.11 and later.

    Example:
        >>> FindingCode.TOO_MANY_FIELDS.value
        'too_many_fields'
        >>> FindingCode("circle_rasterized") is FindingCode.CIRCLE_RASTERIZED
        True
    """

    ATTRIBUTE_UNSUPPORTED = "attribute_unsupported"
    BARCODE_HEIGHT_OUT_OF_RANGE = "barcode_height_out_of_range"
    BATCH_CUT_UNSUPPORTED = "batch_cut_unsupported"
    BATCH_CUT_UNVERIFIED = "batch_cut_unverified"
    CHARACTERS_UNMAPPABLE = "characters_unmappable"
    CHECK_DIGIT_ADDED = "check_digit_added"
    CONTINUOUS_LENGTH_MISSING = "continuous_length_missing"
    CUT_BY_NOT_BELOW_QUANTITY = "cut_by_not_below_quantity"
    CUT_BY_UNSUPPORTED = "cut_by_unsupported"
    CUT_NOW_UNSUPPORTED = "cut_now_unsupported"
    CUTTER_ASSUMED = "cutter_assumed"
    DATAMATRIX_OPTION_UNCONFIRMED = "datamatrix_option_unconfirmed"
    DEFAULT_BAR_WIDTHS_UNSUPPORTED = "default_bar_widths_unsupported"
    DISTANCE_TOO_LARGE = "distance_too_large"
    FIELD_TOO_LONG = "field_too_long"
    FONT_SIZE_SUBSTITUTED = "font_size_substituted"
    HEAT_OUT_OF_RANGE = "heat_out_of_range"
    IMAGE_MUST_BE_STORED = "image_must_be_stored"
    INCH_UNITS = "inch_units"
    LABEL_WIDER_THAN_PRINT_WIDTH = "label_wider_than_print_width"
    MIRROR_UNSUPPORTED = "mirror_unsupported"
    NO_CUTTER = "no_cutter"
    NO_HUMAN_READABLE = "no_human_readable"
    OFFSET_PAST_LABEL_EDGE = "offset_past_label_edge"
    OUTSIDE_PRINT_WIDTH = "outside_print_width"
    PAUSE_EACH_LABEL_UNSUPPORTED = "pause_each_label_unsupported"
    PAUSE_PERSISTS = "pause_persists"
    PLACEHOLDERS_UNFILLED = "placeholders_unfilled"
    PRESENT_DISTANCE_UNSUPPORTED = "present_distance_unsupported"
    SCALABLE_FONT_UNSUPPORTED = "scalable_font_unsupported"
    SENSOR_SELECT_UNSUPPORTED = "sensor_select_unsupported"
    SOLID_FILL_AS_EDGES = "solid_fill_as_edges"
    SPEED_OUT_OF_RANGE = "speed_out_of_range"
    SPEED_UNSUPPORTED = "speed_unsupported"
    STOP_POSITION_TOO_LARGE = "stop_position_too_large"
    STOP_POSITION_UNSUPPORTED = "stop_position_unsupported"
    SYMBOL_SET_UNSUPPORTED = "symbol_set_unsupported"
    SYMBOLOGY_UNSUPPORTED = "symbology_unsupported"
    TEXT_UPPERCASED = "text_uppercased"
    TOO_MANY_FIELDS = "too_many_fields"
    LINE_RASTERIZED = "line_rasterized"
    BOX_RASTERIZED = "box_rasterized"
    CIRCLE_RASTERIZED = "circle_rasterized"
    POLYGON_RASTERIZED = "polygon_rasterized"
    BARCODE_EMPTY = "barcode_empty"
    BARCODE_BAD_CHARACTER = "barcode_bad_character"
    BARCODE_BAD_LENGTH = "barcode_bad_length"
    BARCODE_BAD_CHECK_DIGIT = "barcode_bad_check_digit"
    BARCODE_BAD_PRICE_MARKER = "barcode_bad_price_marker"
    BARCODE_BAD_ESCAPE = "barcode_bad_escape"
    BARCODE_BAD_START_STOP = "barcode_bad_start_stop"
    BARCODE_MISSING_PREFIX = "barcode_missing_prefix"
    BARCODE_TOO_LONG = "barcode_too_long"
    BARCODE_RECORD_TERMINATOR = "barcode_record_terminator"


class BarcodeProblemCode(str, Enum):
    """Why the printer would refuse or misprint barcode data.

    Each member has its own finding code, ``barcode_`` plus the value, in :attr:`finding_code`.

    Example:
        >>> BarcodeProblemCode.BAD_LENGTH.value
        'bad_length'
        >>> BarcodeProblemCode.BAD_LENGTH.finding_code is FindingCode.BARCODE_BAD_LENGTH
        True
    """

    EMPTY = "empty"
    BAD_CHARACTER = "bad_character"
    BAD_LENGTH = "bad_length"
    BAD_CHECK_DIGIT = "bad_check_digit"
    BAD_PRICE_MARKER = "bad_price_marker"
    BAD_ESCAPE = "bad_escape"
    BAD_START_STOP = "bad_start_stop"
    MISSING_PREFIX = "missing_prefix"
    TOO_LONG = "too_long"
    RECORD_TERMINATOR = "record_terminator"

    @property
    def finding_code(self) -> FindingCode:
        """The finding code a render reports for this problem."""
        return _BARCODE_FINDING_CODES[self]


# A table rather than FindingCode(f"barcode_{...}"), so a member added to one enum and not the
# other fails at import instead of at the first render that meets it.
_BARCODE_FINDING_CODES: dict[BarcodeProblemCode, FindingCode] = {
    BarcodeProblemCode.EMPTY: FindingCode.BARCODE_EMPTY,
    BarcodeProblemCode.BAD_CHARACTER: FindingCode.BARCODE_BAD_CHARACTER,
    BarcodeProblemCode.BAD_LENGTH: FindingCode.BARCODE_BAD_LENGTH,
    BarcodeProblemCode.BAD_CHECK_DIGIT: FindingCode.BARCODE_BAD_CHECK_DIGIT,
    BarcodeProblemCode.BAD_PRICE_MARKER: FindingCode.BARCODE_BAD_PRICE_MARKER,
    BarcodeProblemCode.BAD_ESCAPE: FindingCode.BARCODE_BAD_ESCAPE,
    BarcodeProblemCode.BAD_START_STOP: FindingCode.BARCODE_BAD_START_STOP,
    BarcodeProblemCode.MISSING_PREFIX: FindingCode.BARCODE_MISSING_PREFIX,
    BarcodeProblemCode.TOO_LONG: FindingCode.BARCODE_TOO_LONG,
    BarcodeProblemCode.RECORD_TERMINATOR: FindingCode.BARCODE_RECORD_TERMINATOR,
}
if set(_BARCODE_FINDING_CODES) != set(BarcodeProblemCode):  # pragma: no cover - import-time guard
    raise RuntimeError("every BarcodeProblemCode needs a FindingCode")


class CommandKind(str, Enum):
    """What one command in a DPL stream does, as ``explain`` reports it.

    Example:
        >>> CommandKind.RECORD.value
        'record'
    """

    IMMEDIATE = "immediate"
    SYSTEM = "system"
    DOWNLOAD = "download"
    FORMAT = "format"
    RECORD = "record"
    COUNTER = "counter"
    END = "end"
    UNKNOWN = "unknown"


class StatusProblem(str, Enum):
    """A printer status condition that needs a person.

    Example:
        >>> StatusProblem.PAPER_OUT.value
        'paper_out'
    """

    PAPER_OUT = "paper_out"
    RIBBON_OUT = "ribbon_out"
    PAUSED = "paused"


class ModelFamily(str, Enum):
    """Printer family; models of one family share a feature set.

    Example:
        >>> ModelFamily("prodigy-plus") is ModelFamily.PRODIGY_PLUS
        True
    """

    ALLEGRO = "allegro"
    PRODIGY = "prodigy"
    PRODIGY_PLUS = "prodigy-plus"
    DMX_400 = "dmx-400"
    DMX_430 = "dmx-430"
    DMX_600 = "dmx-600"
    DMX_800 = "dmx-800"
    MAX_PE = "max-pe"
    PE = "pe"
    OVATION = "ovation"
    TITAN = "titan"


class CutterPresence(str, Enum):
    """Whether a printer has a cutter installed, as the configuration states it.

    Example:
        >>> CutterPresence("unknown") is CutterPresence.UNKNOWN
        True
    """

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class HardwareOption(str, Enum):
    """An installed hardware option a feature can depend on.

    Example:
        >>> HardwareOption("datamatrix") is HardwareOption.DATAMATRIX
        True
    """

    DATAMATRIX = "datamatrix"


class MediaType(str, Enum):
    """Image format of a camera capture.

    Example:
        >>> MediaType.PNG.value
        'image/png'
    """

    PNG = "image/png"
    JPEG = "image/jpeg"


__all__ = [
    "SPEED_IPS",
    "Attribute",
    "BarcodeProblemCode",
    "CommandKind",
    "CutterPresence",
    "DeployTarget",
    "FallbackPolicy",
    "FillPattern",
    "FindingCode",
    "HardwareOption",
    "LineDirection",
    "MediaSensor",
    "MediaType",
    "ModelFamily",
    "OutputFormat",
    "Rotation",
    "Severity",
    "SpeedCode",
    "StatusProblem",
    "Unit",
]
