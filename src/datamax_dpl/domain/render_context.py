"""Render options and the per-render context every encoder step shares.

The context answers three questions for the encoders: does this model (at this firmware, with
these options) have a feature, how is a length written in this job's DPL unit, and where do
findings go.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from .enums import Attribute, FallbackPolicy, FindingCode, HardwareOption, Unit
from .image_records import download_command
from .units import (
    MAX_OFFSET_MM,
    MAX_SCALE,
    MIN_SCALE,
    MM_PER_INCH,
    NOMINAL,
    Axis,
    Correction,
    DplUnit,
    exact_dots,
    to_dots,
    to_dpl,
)

if TYPE_CHECKING:
    from .findings import FindingCollector
    from .profiles import Feature, Firmware, PrinterProfile
    from .raster import Bitmap

#: Largest value of a four-digit DPL distance field.
MAX_FIELD_VALUE = 9999
#: First character of the name of every image the library draws (raster fallbacks, QR Codes).
DRAWN_IMAGE_PREFIX = "D"
#: Hex digits of the content hash after the prefix: an image name holds at most 8 characters.
_NAME_HASH_DIGITS = 7


def drawn_image_name(bitmap: Bitmap, *, salt: int = 0) -> str:
    """Return the printer name of a drawn image: ``D`` and 7 hex digits of a hash of its content.

    The printer keeps a downloaded image in its memory module across jobs and answers BELL when a
    job loads a name it already holds (manual, Appendix D), which does not say the new image
    replaces the old. A name that follows the content keeps one image's name off another's bits:
    the same bitmap gets the same name in every job and process (sha256, unlike the per-process
    salted ``hash()``), and a different bitmap a different one, except with a chance of about
    1 in 2**28 per pair (within one job :meth:`RenderContext.download_drawn_image` rules that out).

    Args:
        bitmap: The image; its width and height count, since the packed rows pad to whole bytes.
        salt: 0 for the name; a higher value gives the next candidate when two different images
            of one job hash alike.

    Example:
        >>> from datamax_dpl.domain.raster import Bitmap
        >>> drawn_image_name(Bitmap.from_rows(["#."]))
        'DBBE51D5'
    """
    header = f"{bitmap.width}x{bitmap.height}:{salt}:".encode("ascii")
    digest = hashlib.sha256(header + b"".join(bitmap.rows())).hexdigest().upper()
    return DRAWN_IMAGE_PREFIX + digest[:_NAME_HASH_DIGITS]


class RenderOptions(BaseModel):
    """How to render: for which model, at which firmware, with which policy.

    Attributes:
        model: Profile id (``datamax-dpl models`` lists them).
        firmware: Printer firmware ``"<major>.<minor>"``; empty means the oldest the manual covers.
        policy: ``degrade`` applies fallbacks, ``strict`` refuses the label instead.
        cutter_installed: Whether the printer has its cutter installed and enabled; None if unknown.
        options: Installed hardware options, e.g. ``{HardwareOption.DATAMATRIX}``.
        scale_across: Printed length divided by nominal length across the web, measured for
            one printer (``datamax-dpl calibrate``); 1.0 prints nominal lengths. None takes the
            model's measured default (``PrinterProfile.calibration``), nominal for most models.
        scale_along: The same along the feed, where it depends on the platen roller.
        offset_across: Millimetres every field is moved to the right, measured for one printer
            (``datamax-dpl calibrate``); negative moves left. None takes the model's default.
        offset_along: Millimetres every field is moved toward the label top; negative moves down.
        stop_position_mm: Tear-off stop position in mm, sent as configured (no scale or offset
            applies); None takes the model's default, which for most models leaves the printer's
            own setting. A label's own ``stop_position`` wins, and a job that cuts never uses it.
        cutter_stop_position_mm: Stop position in mm for a job that cuts, sent as configured;
            None sends no stop position for such a job and leaves the printer's own setting.

    Example:
        >>> RenderOptions(model="dmx-600").policy
        <FallbackPolicy.DEGRADE: 'degrade'>
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = "generic"
    firmware: str = ""
    policy: FallbackPolicy = FallbackPolicy.DEGRADE
    cutter_installed: bool | None = None
    options: frozenset[HardwareOption] = Field(default_factory=frozenset[HardwareOption])
    # A real printer is a few percent off; a factor outside this range is a typo or an
    # inverted measurement (nominal / printed), which would double the error instead.
    scale_across: float | None = Field(default=None, ge=MIN_SCALE, le=MAX_SCALE)
    scale_along: float | None = Field(default=None, ge=MIN_SCALE, le=MAX_SCALE)
    offset_across: float | None = Field(default=None, ge=-MAX_OFFSET_MM, le=MAX_OFFSET_MM)
    offset_along: float | None = Field(default=None, ge=-MAX_OFFSET_MM, le=MAX_OFFSET_MM)
    stop_position_mm: float | None = Field(default=None, gt=0)
    cutter_stop_position_mm: float | None = Field(default=None, gt=0)


@dataclass(frozen=True)
class ImageDownload:
    """One image sent to the printer before the format.

    The name is kept beside the command so a result can list the downloads without parsing them
    back out of the bytes.

    Attributes:
        name: The image name the format's image record refers to.
        command: The complete ``<STX>I`` download command.
    """

    name: str
    command: bytes


@dataclass
class RenderContext:
    """Mutable state of one render.

    Attributes:
        profile: The model profile.
        firmware: Parsed firmware, or None when unknown.
        options: Installed hardware options.
        unit: Unit the label's lengths are written in.
        dpl_unit: DPL distance unit of this job.
        findings: Where every step records what it noticed.
        cutter_installed: Whether the printer has a working cutter; None when unknown.
        symbol_set: Symbol set of the label's scalable text, or None for the printer default.
        default_attribute: Attribute the format is in when no element overrides it.
        downloads: Images to download before ``<STX>L``, in the order they were queued.
        system_suffix: System commands sent after the format (cut now).
        across: How this printer prints lengths across the web.
        along: How this printer prints lengths along the feed.
        offset_across: Millimetres every field moves to the right on this printer.
        offset_along: Millimetres every field moves toward the label top on this printer.
        model_offset_axes: Axes whose offset is the model's measured default rather than one the
            caller set; a field that offset pushes past the label edge is clamped, not refused.
        stop_position_mm: Tear-off stop position in mm on this printer, or None for its own setting.
        cutter_stop_position_mm: Stop position in mm when cutting, or None for its own setting.
    """

    profile: PrinterProfile
    firmware: Firmware | None
    options: frozenset[HardwareOption]
    unit: Unit
    dpl_unit: DplUnit
    findings: FindingCollector
    cutter_installed: bool | None = None
    symbol_set: str | None = None
    default_attribute: Attribute = Attribute.XOR
    downloads: list[ImageDownload] = field(default_factory=list[ImageDownload])
    system_suffix: list[bytes] = field(default_factory=list[bytes])
    across: Correction = NOMINAL
    along: Correction = NOMINAL
    offset_across: float = 0.0
    offset_along: float = 0.0
    model_offset_axes: frozenset[Axis] = frozenset[Axis]()
    stop_position_mm: float | None = None
    cutter_stop_position_mm: float | None = None

    def correction(self, axis: Axis) -> Correction:
        """Return how this printer prints lengths on ``axis``."""
        return self.across if axis is Axis.ACROSS else self.along

    def offset_mm(self, axis: Axis) -> float:
        """Return how far this printer's fields move on ``axis``, in mm."""
        return self.offset_across if axis is Axis.ACROSS else self.offset_along

    def shifted(self, value: float, *, axis: Axis) -> float:
        """Return the label position ``value`` moved by this printer's offset, in the label's unit."""
        offset = self.offset_mm(axis)
        return value + (offset / MM_PER_INCH if self.unit is Unit.INCH else offset)

    def supports(self, feature: Feature) -> bool:
        """Return True when the model has ``feature`` at this firmware with these options."""
        support = self.profile.support(feature)
        if not support.available:
            return False
        if support.min_firmware is not None and (self.firmware is None or self.firmware < support.min_firmware):
            return False
        return support.option is None or support.option in self.options

    def requirement(self, feature: Feature) -> str:
        """Describe what ``feature`` needs on this model, for messages (``needs firmware 2.01``)."""
        support = self.profile.support(feature)
        if not support.available:
            return f"{self.profile.name} does not have it"
        if support.min_firmware is not None:
            major, minor = support.min_firmware
            return f"{self.profile.name} needs firmware {major}.{minor:02d} or later (pass --firmware)"
        option = support.option.value if support.option is not None else ""
        return f"{self.profile.name} needs the '{option}' option installed"

    def distance(self, value: float, *, axis: Axis, element: int | None, what: str) -> int:
        """Convert a label length on ``axis`` to this job's DPL unit, refusing values past four digits."""
        converted = to_dpl(
            value, self.unit, target=self.dpl_unit, dpi=self.profile.dpi, correction=self.correction(axis)
        ).value
        if converted > MAX_FIELD_VALUE:
            self.findings.refuse(
                FindingCode.DISTANCE_TOO_LARGE,
                f"{what} is {converted} {self.dpl_unit.value} units; DPL fields hold at most {MAX_FIELD_VALUE}",
                element=element,
            )
            return MAX_FIELD_VALUE
        return converted

    def inch_hundredths(self, value: float, *, axis: Axis) -> int:
        """Convert a label length on ``axis`` to hundredths of an inch (system commands use inch mode)."""
        return to_dpl(
            value, self.unit, target=DplUnit.INCH_HUNDREDTHS, dpi=self.profile.dpi, correction=self.correction(axis)
        ).value

    def nominal_inch_hundredths(self, mm: float) -> int:
        """Convert a millimetre distance set outside the label to hundredths of an inch, uncorrected.

        For distances found by trial on the printer (a stop position), where what is configured
        is what must be sent: neither the label's unit nor this printer's scale applies.
        """
        return to_dpl(mm, Unit.MM, target=DplUnit.INCH_HUNDREDTHS, dpi=self.profile.dpi).value

    def dots(self, value: float, *, axis: Axis) -> int:
        """Convert a label length on ``axis`` to whole printer dots, at least one (a size)."""
        return to_dots(value, self.unit, dpi=self.profile.dpi, correction=self.correction(axis))

    def offset_dots(self, value: float, *, axis: Axis) -> int:
        """Convert a label length on ``axis`` to the nearest whole number of dots, zero allowed (a position)."""
        return round(exact_dots(value, self.unit, dpi=self.profile.dpi, correction=self.correction(axis)))

    def download_image(self, name: str, bitmap: Bitmap) -> None:
        """Queue ``bitmap`` for download under ``name`` before the format.

        Raises:
            ValueError: When the name is not 1-8 letters, digits or underscores, or the image is
                too wide.
        """
        self.downloads.append(ImageDownload(name=name, command=download_command(name, bitmap)))

    def download_drawn_image(self, bitmap: Bitmap) -> str:
        """Queue an image the library drew under its content name, once per job, and return the name.

        A bitmap already queued is not queued again. Two different bitmaps of one job that hash
        to the same name are told apart by the next salt, so a job never names two images alike.
        """
        salt = 0
        while True:
            name = drawn_image_name(bitmap, salt=salt)
            command = download_command(name, bitmap)
            held = next((download.command for download in self.downloads if download.name == name), None)
            if held is None:
                self.downloads.append(ImageDownload(name=name, command=command))
                return name
            if held == command:
                return name
            salt += 1


__all__ = [
    "DRAWN_IMAGE_PREFIX",
    "MAX_FIELD_VALUE",
    "ImageDownload",
    "RenderContext",
    "RenderOptions",
    "drawn_image_name",
]
