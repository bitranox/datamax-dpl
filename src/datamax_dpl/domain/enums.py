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


__all__ = [
    "SPEED_IPS",
    "Attribute",
    "DeployTarget",
    "FallbackPolicy",
    "FillPattern",
    "LineDirection",
    "MediaSensor",
    "OutputFormat",
    "Rotation",
    "Severity",
    "SpeedCode",
    "Unit",
]
