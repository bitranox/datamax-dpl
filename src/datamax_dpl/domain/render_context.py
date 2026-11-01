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

from .enums import (
    Attribute,
    FallbackPolicy,
    FindingCode,
    HardwareOption,
    ImageModule,
    MediaSensor,
    PrinterSetting,
    Severity,
    SpeedCode,
    Unit,
)
from .held_images import HeldImages
from .image_budget import HALVING_FACTOR, halve, halved_magnification, image_data_bytes
from .image_records import CR, MAX_WIDTH, download_command
from .label import StopPosition
from .profiles import Feature
from .raster import Bitmap
from .records import MAX_IMAGE_MAGNIFICATION
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
    hundredths_to_mm,
    to_dots,
    to_dpl,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .findings import FindingCollector
    from .label import Image
    from .profiles import Firmware, PrinterProfile

#: Largest value of a four-digit DPL distance field.
MAX_FIELD_VALUE = 9999
#: Smallest start of print ``<STX>O`` keeps, in mm: "Settings below 50 are adjusted back to
#: default" (manual p.32), and the job's system commands go out in inch mode, so 50/100 in.
MIN_START_OF_PRINT_MM = hundredths_to_mm(50)
#: Smallest maximum label length ``<STX>M`` takes, in mm: the manual says it "should be at least
#: 5 in (127mm)" (p.30), since a value near the label's own length makes the printer declare a
#: paper fault before it finds the next edge.
MIN_MAX_LABEL_LENGTH_MM = hundredths_to_mm(500)
#: Largest distance the four digits of ``<STX>O`` and ``<STX>M`` hold in inch mode, 9999/100 in.
MAX_SYSTEM_DISTANCE_MM = hundredths_to_mm(MAX_FIELD_VALUE)
#: First character of the printer name of every image a job downloads: a label ``image`` with a
#: bitmap, a raster fallback, a QR Code, a merged transparency group.
DRAWN_IMAGE_PREFIX = "D"
#: The 62 symbols a content name is written in, in digit order: ``0`` is 0, ``A`` is 10, ``a`` is 36,
#: ``z`` is 61 (the ASCII order). Lower case doubles what 7 characters hold over upper case and
#: digits alone; one Allegro 2 (firmware CA02 03.04, 2026-11-01) kept a lower-case name as sent and
#: matched it by exact case, which no other model was checked for.
NAME_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
#: Characters of the content hash after the prefix: an image name holds at most 8 characters.
_NAME_HASH_CHARS = 7
#: Leading digest bytes the name is taken from: 64 bits, so reducing them to the 62**7 values seven
#: characters hold skews no name by more than one part in five million.
_NAME_DIGEST_BYTES = 8


def drawn_image_name(bitmap: Bitmap, *, salt: int = 0) -> str:
    """Return the printer name of a downloaded image: ``D`` and 7 base-62 digits of a hash of its content.

    Every image a job downloads goes under this name, a label ``image`` with a bitmap as well as
    one the library draws. The printer keeps a downloaded image in its memory module across jobs
    and prints nothing for a format that downloads a name it already holds (one Allegro 2, 3 of 3
    rounds), so a name that follows the content means a held name is the same picture: the same
    bitmap gets the same name in every job and process (sha256, unlike the per-process salted
    ``hash()``). Within one job :meth:`RenderContext.download_drawn_image` and
    :meth:`RenderContext.download_image` salt past a name already taken, so two different images
    never share one there; across separate jobs two different bitmaps share a name only on a hash
    collision, about 1 in 62**7 (3.5e12, about 2**42) per pair.

    The 7 digits are the leading 8 bytes of the sha256, read as one big-endian number, written in
    base 62 with :data:`NAME_ALPHABET`: its last 7 digits, most significant first. Names are
    compared with their letter case, never folded: ``Dab`` and ``DAB`` are two names.

    Args:
        bitmap: The image; its width and height count, since the packed rows pad to whole bytes.
        salt: 0 for the name; a higher value gives the next candidate when two different images
            of one job hash alike.

    Example:
        >>> from datamax_dpl.domain.raster import Bitmap
        >>> drawn_image_name(Bitmap.from_rows(["#."]))
        'DCoRmuuz'
    """
    header = f"{bitmap.width}x{bitmap.height}:{salt}:".encode("ascii")
    digest = hashlib.sha256(header + b"".join(bitmap.rows())).digest()
    value = int.from_bytes(digest[:_NAME_DIGEST_BYTES], "big")
    digits: list[str] = []
    for _ in range(_NAME_HASH_CHARS):
        value, digit = divmod(value, len(NAME_ALPHABET))
        digits.append(NAME_ALPHABET[digit])
    return DRAWN_IMAGE_PREFIX + "".join(reversed(digits))


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
            applies); ``"printer"`` sends none and keeps the printer's own setting; None takes the
            model's default, which for most models leaves the printer's own setting. A label's own
            ``stop_position`` wins, and a job that cuts never uses it.
        cutter_stop_position_mm: Stop position in mm for a job that cuts, sent as configured;
            None sends no stop position for such a job and leaves the printer's own setting.
        image_memory_bytes: Bytes of image data one job may download (see
            :mod:`~datamax_dpl.domain.image_budget`); None takes the model's budget, which most
            models do not have.
        image_module: Memory module image downloads are stored in; None takes the model's
            built-in module (``PrinterProfile.image_module``). A model without one refuses a job
            that downloads an image (``image_module_unknown``) until this names the module fitted.
        max_format_rows: Dot rows along the feed one format may reach at dot height 1, counted as
            the encoder commands them; None takes the model's limit
            (``PrinterProfile.max_format_rows``), which most models do not have. A field past it
            is refused (``format_past_max_length``) under either policy.
        send_settings: Persistent printer settings a job sends before its format; None sends
            every one. A setting left out keeps whatever the printer holds.
        media: Media sensor a job sets when the label names none; the label's own ``media``
            wins. None leaves the sensor as the label says, or as the printer holds it.
        start_of_print_mm: Start of print (``<STX>O``) in mm, sent as configured; 12.7 mm to
            2539.746 mm (the printer takes less as its default, more does not fit the command).
            None takes the model's default.
        max_label_length_mm: How far the printer searches for a label edge (``<STX>M``) in mm,
            sent as configured; 127 mm to 2539.746 mm (less faults on labels near that length,
            more does not fit the command). None takes the model's default.
        feed_rate: Speed the feed button feeds at (``<STX>S``); None takes the model's default.
        label_present: Whether the label-present sensor is on, for the ``<STX>V`` value of a
            model with that bit; None when unknown, which sends no ``<STX>V`` there.
        clear_graphics_before_label: Clear the memory module the label's images go to before
            each label downloads them, so a name the printer already holds cannot drop the format.
            ``<STX>q`` clears EVERY file in that module (images, formats, fonts), not only images,
            so an image the label places without a bitmap is refused (``stored_image_cleared``);
            the plain Prodigy, which has no ``<STX>q``, removes just the names the label downloads.
            Where no clear runs before the downloads, a name the printer holds is not downloaded
            again (see :class:`~datamax_dpl.domain.held_images.HeldImages`).
        clear_graphics_after_label: Clear that module again after each label, so the next job
            starts from an empty module whatever program sends it; it too removes every file.
            Neither clear goes to a module that is not one of the model's built-in RAM modules
            (``PrinterProfile.ram_modules``): a plug-in module may be flash, which every clear wears.
        held_image_severity: Severity of ``image_name_held`` and ``image_names_unread``, the
            findings of a label whose downloads no clear precedes; ``error`` refuses such a label
            under either policy.

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
    stop_position_mm: StopPosition | None = None
    cutter_stop_position_mm: float | None = Field(default=None, gt=0)
    image_memory_bytes: int | None = Field(default=None, gt=0)
    image_module: ImageModule | None = None
    max_format_rows: int | None = Field(default=None, gt=0)
    send_settings: frozenset[PrinterSetting] | None = None
    media: MediaSensor | None = None
    start_of_print_mm: float | None = Field(default=None, ge=MIN_START_OF_PRINT_MM, le=MAX_SYSTEM_DISTANCE_MM)
    max_label_length_mm: float | None = Field(default=None, ge=MIN_MAX_LABEL_LENGTH_MM, le=MAX_SYSTEM_DISTANCE_MM)
    feed_rate: SpeedCode | None = None
    label_present: bool | None = None
    clear_graphics_before_label: bool = True
    clear_graphics_after_label: bool = True
    held_image_severity: Severity = Severity.WARNING


@dataclass(frozen=True)
class PrinterState:
    """The persistent printer settings one label's job sets, each option resolved over the model.

    Every field is required, so a state is either resolved from the options and the model
    (:func:`resolved_printer_state`) or written out in full; none is half-filled by defaults.

    Attributes:
        send: Settings the job sends; every one unless the options name fewer.
        configured: Settings whose value the options set, rather than the model's default or
            the label's own media, so a finding can say where a value came from.
        media: Media sensor to set: the label's own, else the option's; None sets none.
        media_from_label: True when ``media`` is the label's own: a model that cannot choose it
            deviates from what the label asks for, while a configured sensor is only reported.
        start_of_print_mm: Start of print in mm, or None to send none.
        max_label_length_mm: Maximum label length in mm, or None to send none.
        feed_rate: Feed button speed, or None to send none.
        label_present: Whether the label-present sensor is on; None when unknown.
        clear_graphics_before_label: Clear the image module before the label's downloads; with
            ``<STX>q`` that removes every file in it (images, formats, fonts).
        clear_graphics_after_label: Clear the image module after the label, the same way.
    """

    send: frozenset[PrinterSetting]
    configured: frozenset[PrinterSetting]
    media: MediaSensor | None
    media_from_label: bool
    start_of_print_mm: float | None
    max_label_length_mm: float | None
    feed_rate: SpeedCode | None
    label_present: bool | None
    clear_graphics_before_label: bool
    clear_graphics_after_label: bool


def resolved_printer_state(
    options: RenderOptions, profile: PrinterProfile, *, label_media: MediaSensor | None
) -> PrinterState:
    """Return the printer settings a label's job sets: each option wins over the model's default.

    Args:
        options: The job's render options.
        profile: The model profile, whose manual defaults fill what the options leave unset.
        label_media: The label's own media sensor, which wins over the option's.

    Returns:
        The resolved settings.

    Example:
        >>> from datamax_dpl.domain.profiles import get_profile
        >>> resolved_printer_state(RenderOptions(), get_profile("allegro-2"), label_media=None).start_of_print_mm
        55.88
        >>> options = RenderOptions(start_of_print_mm=40)
        >>> resolved_printer_state(options, get_profile("allegro-2"), label_media=None).start_of_print_mm
        40.0
    """
    given = {
        PrinterSetting.MEDIA: label_media is None and options.media is not None,
        PrinterSetting.START_OF_PRINT: options.start_of_print_mm is not None,
        PrinterSetting.MAX_LABEL_LENGTH: options.max_label_length_mm is not None,
        PrinterSetting.FEED_RATE: options.feed_rate is not None,
    }
    return PrinterState(
        send=frozenset(PrinterSetting) if options.send_settings is None else options.send_settings,
        configured=frozenset(setting for setting, set_by_options in given.items() if set_by_options),
        media=options.media if label_media is None else label_media,
        media_from_label=label_media is not None,
        start_of_print_mm=profile.start_of_print_mm if options.start_of_print_mm is None else options.start_of_print_mm,
        max_label_length_mm=(
            profile.max_label_length_mm if options.max_label_length_mm is None else options.max_label_length_mm
        ),
        feed_rate=profile.feed_rate if options.feed_rate is None else options.feed_rate,
        label_present=options.label_present,
        clear_graphics_before_label=options.clear_graphics_before_label,
        clear_graphics_after_label=options.clear_graphics_after_label,
    )


def resolved_image_module(options: RenderOptions, profile: PrinterProfile) -> str | None:
    """Return the letter of the memory module a job's image downloads go to.

    The configured module wins over the model's built-in one. Every context built for a job reads
    the module from here, so a probe context cannot name a different module than the render does.

    Args:
        options: The job's render options; ``image_module`` is the configured module, if any.
        profile: The model profile; ``image_module`` is its built-in module, None when it has none.

    Returns:
        The module letter, or None when neither names one (the job then refuses downloads).

    Example:
        >>> from datamax_dpl.domain.profiles import get_profile
        >>> resolved_image_module(RenderOptions(model="dmx-600"), get_profile("dmx-600"))
        'E'
        >>> resolved_image_module(RenderOptions(image_module=ImageModule.B), get_profile("prodigy"))
        'B'
        >>> resolved_image_module(RenderOptions(), get_profile("prodigy")) is None
        True
    """
    return profile.image_module if options.image_module is None else options.image_module.value


@dataclass(frozen=True)
class ImageDownload:
    """One image sent to the printer before the format.

    The name is kept beside the command so a result can list the downloads without parsing them
    back out of the bytes.

    Attributes:
        name: The printer name the format's image record refers to, the image's content name.
        command: The complete ``<STX>I`` download command.
        element: The first element that queued it, for findings about the download.
        label_name: The ``name`` of the label ``image`` element that queued it, for findings;
            None for an image the library drew.
    """

    name: str
    command: bytes
    element: int
    label_name: str | None = None

    @property
    def described(self) -> str:
        """Name the download in a finding: ``image LOGO (sent as D1a2B3c4)``, or ``image D1a2B3c4`` when drawn.

        Example:
            >>> ImageDownload(name="D1a2B3c4", command=b"", element=0, label_name="LOGO").described
            'image LOGO (sent as D1a2B3c4)'
        """
        if self.label_name is None:
            return f"image {self.name}"
        return f"image {self.label_name} (sent as {self.name})"

    @property
    def data_bytes(self) -> int:
        """Bytes of dot data the printer stores for this image, read from the command's records.

        Counted from the encoded records rather than from the bitmap they came from, so the
        figure is the one the bytes on the wire carry (see :func:`image_data_bytes`).
        """
        return image_data_bytes(self.command.split(CR, 1)[1])


@dataclass(frozen=True)
class PlacedImage:
    """An image queued for download and how its placing record prints it.

    Attributes:
        name: The image name the placing record refers to.
        magnification: Printer dots per image dot, across and along alike; doubled for a halved
            image.
        size: Width and height in dots of the image as downloaded, or None for an image placed
            from the printer's memory.
    """

    name: str
    magnification: int
    size: tuple[int, int] | None = None

    @property
    def dots(self) -> tuple[int, int]:
        """The magnification as the placing record's (across, along) pair."""
        return self.magnification, self.magnification


@dataclass(frozen=True)
class ImageEdge:
    """Where a placing record starts an image, for the check of its right edge against the print width.

    Every placement takes one, so no image placed with a known size escapes the check.

    Attributes:
        column: DPL column the record writes, where the image's first dot prints; None when the
            field is already refused for starting past the last column (``outside_print_width``),
            so its right edge gets no second finding.
        what: What the element is, naming the image in a refusal after the element's position:
            ``image LOGO`` reads ``element 3 (image LOGO)``.
        code: The finding an image ending past the print width gets; a QR Code keeps its own.
        margin_dots: Dots right of the image that must fit too: a QR Code's quiet zone.

    Example:
        >>> ImageEdge(column=None, what="image LOGO").code
        <FindingCode.IMAGE_PAST_PRINT_WIDTH: 'image_past_print_width'>
    """

    column: int | None
    what: str
    code: FindingCode = FindingCode.IMAGE_PAST_PRINT_WIDTH
    margin_dots: int = 0


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
        image_module: Letter of the memory module images are downloaded to, or None when the
            model has none and the options name none: a download then refuses the job.
        printer_state: The persistent printer settings this label's job sets, from
            :func:`resolved_printer_state`; required, so no context carries an unresolved one.
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
        reserved_image_names: Names of the label's stored-only ``image`` elements that no
            element of the label downloads: they name images the printer stores, so no download
            of the job takes one, whichever element comes first.
        bitmap_rows: The bitmap of each name a label ``image`` downloads (the first such
            element's), so a stored-only reference to that name is placed as that download,
            halved where the job halves it, whichever element comes first.
        halve_images: Download every image that may be halved at half resolution, placed at
            double magnification (the job is past its image memory budget).
        halved_elements: Elements whose image was halved, in the order they were encoded.
        emulated_groups: Groups of overlapping elements drawn into one image, because the label
            asks for transparency and the model has no attribute command.
        held_images: The image names the printer is known to hold in ``image_module``; a label
            no clear precedes leaves out the download of each one (see :attr:`sent_downloads`).
        held_image_severity: Severity of the findings about those names.
    """

    profile: PrinterProfile
    firmware: Firmware | None
    options: frozenset[HardwareOption]
    unit: Unit
    dpl_unit: DplUnit
    findings: FindingCollector
    image_module: str | None
    printer_state: PrinterState
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
    reserved_image_names: frozenset[str] = frozenset[str]()
    bitmap_rows: Mapping[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])
    halve_images: bool = False
    halved_elements: list[int] = field(default_factory=list[int])
    emulated_groups: list[tuple[int, ...]] = field(default_factory=list[tuple[int, ...]])
    held_images: HeldImages = field(default_factory=HeldImages)
    held_image_severity: Severity = Severity.WARNING
    module_refused: bool = field(default=False, init=False)

    def correction(self, axis: Axis) -> Correction:
        """Return how this printer prints lengths on ``axis``."""
        return self.across if axis is Axis.ACROSS else self.along

    def offset_mm(self, axis: Axis) -> float:
        """Return how far this printer's fields move on ``axis``, in mm."""
        return self.offset_across if axis is Axis.ACROSS else self.offset_along

    def shifted(self, value: float, *, axis: Axis) -> float:
        """Return the label position ``value`` moved by this printer's offset, in the label's unit.

        The offset is a millimetre calibration whatever unit the label uses, so a label in dots
        moves by those millimetres at this model's resolution, not by that many dots.
        """
        offset = self.offset_mm(axis)
        if self.unit is Unit.MM:
            return value + offset
        inches = offset / MM_PER_INCH
        return value + (inches * self.profile.dpi if self.unit is Unit.DOTS else inches)

    def supports(self, feature: Feature) -> bool:
        """Return True when the model has ``feature`` at this firmware with these options."""
        support = self.profile.support(feature)
        if not support.available:
            return False
        if support.min_firmware is not None and (self.firmware is None or self.firmware < support.min_firmware):
            return False
        return support.option is None or support.option in self.options

    @property
    def clears_module(self) -> bool:
        """True when a clear may go to the image module as ``<STX>q``: the model has it, the module is its RAM.

        A plug-in module may be flash, which every clear wears (manual p.35, Appendix L), so its
        clear is skipped whatever the clear keys say.
        """
        return self.supports(Feature.CLEAR_MODULE) and self.image_module in self.profile.ram_modules

    @property
    def module_cleared_before(self) -> bool:
        """True when ``<STX>q`` empties the image module before the label: nothing stored there survives."""
        return self.printer_state.clear_graphics_before_label and self.clears_module

    @property
    def clear_runs_before(self) -> bool:
        """True when a clear runs before the label's downloads: ``<STX>q``, or the Prodigy's per-name remove.

        Only without one can a name the printer holds meet a download of it again.
        """
        before = self.printer_state.clear_graphics_before_label
        return before and (self.clears_module or self.supports(Feature.REMOVE_IMAGE))

    @property
    def sent_downloads(self) -> list[ImageDownload]:
        """The downloads the job sends: all, but each name the printer holds where no clear runs before them."""
        if self.clear_runs_before:
            return list(self.downloads)
        return [download for download in self.downloads if not self.held_images.holds(download.name)]

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

    def dpl_units(self, value: float, *, axis: Axis) -> int:
        """Convert a label length on ``axis`` to this job's DPL unit, with no check and no finding."""
        return to_dpl(
            value, self.unit, target=self.dpl_unit, dpi=self.profile.dpi, correction=self.correction(axis)
        ).value

    @property
    def dots_per_unit(self) -> float:
        """Printhead dots one DPL distance unit of this job addresses.

        The measured whole dots of a model that rounds 1/100 in to them (2 on the Allegro 2), else
        the nominal ratio of the resolution to the unit.
        """
        unit_dots = self.across.unit_dots
        if unit_dots is not None and self.dpl_unit is DplUnit.INCH_HUNDREDTHS:
            return float(unit_dots)
        return self.profile.dpi / self.dpl_unit.per_inch

    def length_of_dots(self, dots: int) -> float:
        """Return ``dots`` printer dots as a nominal length in the label's unit."""
        if self.unit is Unit.DOTS:
            return float(dots)
        inches = dots / self.profile.dpi
        return inches * MM_PER_INCH if self.unit is Unit.MM else inches

    def printed_dots(self, units: int) -> int:
        """Return how many printhead dots ``units`` DPL distance units of this job print as."""
        return round(units * self.dots_per_unit)

    def distance(self, value: float, *, axis: Axis, element: int | None, what: str) -> int:
        """Convert a label length on ``axis`` to this job's DPL unit, refusing values past four digits."""
        converted = self.dpl_units(value, axis=axis)
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

    @staticmethod
    def _described(edge: ImageEdge, *, element: int) -> str:
        """Name an image in a refusal by its element and what it is: ``element 3 (image LOGO)``."""
        return f"element {element} ({edge.what})"

    def _fits_an_image_row(self, described: str, bitmap: Bitmap, *, element: int) -> bool:
        """Refuse an image wider than one row record holds, on the element that made it.

        Args:
            described: Names the image in the message, ``element 3 (image LOGO)``; a drawn image
                adds the name the job would queue it under, ``element 3 (polygon) drawn as image
                D1a2B3c4``.
            bitmap: The image as it would be sent.
            element: Position of the element in the label.
        """
        if bitmap.width <= MAX_WIDTH:
            return True
        self.findings.refuse(
            FindingCode.IMAGE_TOO_WIDE,
            f"{described} is {bitmap.width} dots wide; one image row holds at most {MAX_WIDTH} dots",
            element=element,
        )
        return False

    def _module(self, *, element: int) -> str | None:
        """Return the module letter a download goes to; with none, refuse the job once and return None.

        The refusal names the first element that downloads; a later download is dropped silently,
        since one finding already says why the job cannot be printed.
        """
        if self.image_module is None and not self.module_refused:
            self.module_refused = True
            self.findings.refuse(
                FindingCode.IMAGE_MODULE_UNKNOWN,
                f"{self.profile.name} has no built-in memory module for images; "
                "set image_module (A-E) to the module fitted",
                element=element,
            )
        return self.image_module

    def _sent_form(self, bitmap: Bitmap, *, what: str, element: int, magnification: int) -> tuple[Bitmap, int]:
        """Return the bitmap this job sends and the magnification it is placed at.

        Unchanged unless the job halves its images; then halved and placed at double the
        magnification, or kept and refused (``image_memory_exceeded``) when the doubled
        magnification passes the largest a record writes.
        """
        if not self.halve_images:
            return bitmap, magnification
        doubled = halved_magnification(magnification)
        if doubled is None:
            self.findings.refuse(
                FindingCode.IMAGE_MEMORY_EXCEEDED,
                f"{what} is placed at magnification {magnification}; halving it would need "
                f"{magnification * HALVING_FACTOR}, past {MAX_IMAGE_MAGNIFICATION}, the largest image magnification",
                element=element,
            )
            return bitmap, magnification
        self.halved_elements.append(element)
        return halve(bitmap), doubled

    def _check_print_width(self, placed: PlacedImage, *, edge: ImageEdge, element: int) -> None:
        """Refuse an image whose right edge, magnified, ends past the printhead's last dot.

        Counted in printhead dots from the column the placing record writes (corrected like every
        column), since the printer addresses the head by dot, whatever the calibration says the
        dots measure on paper. The size is the image as sent, so a halved image counts at its
        doubled magnification. An image of unknown size (placed from the printer's memory) and
        one whose field is already refused for its column are not checked.
        """
        if placed.size is None or edge.column is None:
            return
        end = self.printed_dots(edge.column) + placed.size[0] * placed.magnification + edge.margin_dots
        limit = round(self.profile.print_width_in * self.profile.dpi)
        if end > limit:
            self.findings.refuse(
                edge.code,
                f"{self._described(edge, element=element)} ends at dot {end}; "
                f"it passes the {limit}-dot print width of {self.profile.name}",
                element=element,
            )

    def _checked(self, placed: PlacedImage, *, edge: ImageEdge, element: int) -> PlacedImage:
        """Return ``placed`` after checking its right edge against the print width."""
        self._check_print_width(placed, edge=edge, element=element)
        return placed

    def download_image(
        self, name: str, bitmap: Bitmap, *, element: int, edge: ImageEdge, magnification: int = 1
    ) -> PlacedImage | None:
        """Queue a label's ``bitmap`` for download before the format and say how to place it.

        The bitmap goes under its content name (:func:`drawn_image_name`), halved or not, never
        under ``name``: the same picture then has the same printer name in every job, so a name
        the printer holds is that very picture, and two elements of one ``name`` with different
        bitmaps can never clash. A second element with the same bitmap places the first one's
        download. ``name`` stays the label's own, in findings.

        Args:
            name: The image name, the label's own.
            bitmap: The image.
            element: Position of the element in the label, for findings.
            edge: Where the placing record starts it; an image ending past the print width is
                refused (``image_past_print_width``) but still queued and placed.
            magnification: Printer dots per image dot the element asks for.

        Returns:
            The printer name and the magnification to place it at (doubled when the job halves
            its images), or None when the image is too wide for one row record
            (``image_too_wide``) or the job has no memory module to download to
            (``image_module_unknown``): it is then refused, not queued, and the caller places
            nothing for the element.
        """
        sent, placed_at = self._sent_form(bitmap, what=f"image {name}", element=element, magnification=magnification)
        return self._download_label_image(name, sent, element=element, edge=edge, magnification=placed_at)

    def _download_label_image(
        self, name: str, sent: Bitmap, *, element: int, edge: ImageEdge, magnification: int
    ) -> PlacedImage | None:
        """Queue ``sent``, a label image as the job sends it, under its content name and place it."""
        if not self._fits_an_image_row(self._described(edge, element=element), sent, element=element):
            return None
        module = self._module(element=element)
        if module is None:
            return None
        printer_name = self._queue_drawn(self._drawn_name(sent), sent, module=module, element=element, label_name=name)
        placed = PlacedImage(name=printer_name, magnification=magnification, size=(sent.width, sent.height))
        return self._checked(placed, edge=edge, element=element)

    def image_rows(self, image: Image) -> Sequence[str] | None:
        """Return the bitmap ``image`` prints: its own, else the one this job downloads under its name.

        A stored-only reference to a name another element of the job downloads prints that
        download, wherever it stands in the label (:meth:`place_stored_image`).

        Returns:
            The rows, top row first, or None for an image only the printer holds, whose dots the
            library cannot know.
        """
        return image.bitmap if image.bitmap is not None else self.bitmap_rows.get(image.name)

    def place_stored_image(self, name: str, *, element: int, edge: ImageEdge, magnification: int) -> PlacedImage | None:
        """Say how to place an image the label names without a bitmap.

        Normally the printer's own image under ``name``. Where another element of the label
        downloads ``name``, this reference prints that download: the first such element's bitmap
        under its content name, halved and at double magnification where the job halves its
        images, whichever element comes first. Only such a reference has a known size, so only it
        is checked against the print width (``edge``).

        Returns:
            How to place it, or None when that bitmap is too wide for one row record (refused as
            ``image_too_wide``) or the job has no memory module to download it to (refused as
            ``image_module_unknown``); nothing is placed.
        """
        rows = self.bitmap_rows.get(name)
        if rows is None:
            return PlacedImage(name=name, magnification=magnification)
        bitmap = Bitmap.from_rows(rows)
        sent, placed_at = self._sent_form(bitmap, what=f"image {name}", element=element, magnification=magnification)
        return self._download_label_image(name, sent, element=element, edge=edge, magnification=placed_at)

    def download_drawn_image(
        self, bitmap: Bitmap, *, element: int, edge: ImageEdge, magnification: int = 1, halvable: bool = True
    ) -> PlacedImage | None:
        """Queue an image the library drew under its content name, once per job, and say how to place it.

        A bitmap already queued is not queued again. When the content name is taken in this job,
        by a different drawn bitmap (a hash collision) or by a user ``image`` of that name (a
        reserved name, whether or not it is downloaded), the next salt is tried, so a job never
        sends or places two different images under one name, whatever the element order. A
        halved bitmap is named by its own content.

        Args:
            bitmap: The drawn image.
            element: Position of the element in the label, for findings.
            edge: Where the placing record starts it; an image ending past the print width is
                refused (``edge.code``) but still queued and placed.
            magnification: Printer dots per image dot.
            halvable: False for an image halving would distort (a QR Code's modules): it is sent
                as it is even when the job halves its images.

        Returns:
            The name and the magnification to place it at, or None when the image is too wide
            for one row record (``image_too_wide``) or the job has no memory module to download
            to (``image_module_unknown``): it is then refused, not queued, and the caller places
            nothing for the element.
        """
        sent, placed_at = bitmap, magnification
        if halvable:
            sent, placed_at = self._sent_form(bitmap, what="drawn image", element=element, magnification=magnification)
        # Named before the check, so a refusal gives the name the job would queue it under.
        name = self._drawn_name(sent)
        described = f"{self._described(edge, element=element)} drawn as image {name}"
        if not self._fits_an_image_row(described, sent, element=element):
            return None
        module = self._module(element=element)
        if module is None:
            return None
        placed = PlacedImage(
            name=self._queue_drawn(name, sent, module=module, element=element),
            magnification=placed_at,
            size=(sent.width, sent.height),
        )
        return self._checked(placed, edge=edge, element=element)

    def _drawn_name(self, bitmap: Bitmap) -> str:
        """Return the name this job queues ``bitmap`` under: its first salted content name that is free.

        A name is free when no stored-only label ``image`` reserves it and no download holds it,
        or the download holding it is this very bitmap. A download holds it in this job, or in an
        earlier label of the batch with no clear since (``HeldImages.sent``), whose picture the
        printer still has under it. Picked without queuing, so a refusal can name what the job
        would have sent.
        """
        salt = 0
        while not self._name_is_free(drawn_image_name(bitmap, salt=salt), bitmap):
            salt += 1
        return drawn_image_name(bitmap, salt=salt)

    def _name_is_free(self, name: str, bitmap: Bitmap) -> bool:
        """Return True when ``bitmap`` may be queued under ``name`` in this job."""
        if name in self.reserved_image_names:
            return False
        held = next((download.command for download in self.downloads if download.name == name), None)
        if held is None and not self.clear_runs_before:
            held = self.held_images.sent.get(name)
        if held is None:
            return True
        # Every download of the job or of its batch fits one row record and goes to the image
        # module, so an image too wide for a row, or a job without a module, never holds a name.
        if bitmap.width > MAX_WIDTH or self.image_module is None:
            return False
        return held == download_command(name, bitmap, module=self.image_module)

    def _queue_drawn(
        self, name: str, bitmap: Bitmap, *, module: str, element: int, label_name: str | None = None
    ) -> str:
        """Queue ``bitmap`` into ``module`` under ``name`` (from :meth:`_drawn_name`), once, and return the name.

        ``label_name`` is the label ``image`` element's own name, None for an image the library drew.
        """
        if all(download.name != name for download in self.downloads):
            command = download_command(name, bitmap, module=module)
            self.downloads.append(ImageDownload(name=name, command=command, element=element, label_name=label_name))
        return name


__all__ = [
    "DRAWN_IMAGE_PREFIX",
    "MAX_FIELD_VALUE",
    "MAX_SYSTEM_DISTANCE_MM",
    "MIN_MAX_LABEL_LENGTH_MM",
    "MIN_START_OF_PRINT_MM",
    "NAME_ALPHABET",
    "ImageDownload",
    "ImageEdge",
    "PlacedImage",
    "PrinterState",
    "RenderContext",
    "RenderOptions",
    "drawn_image_name",
    "resolved_image_module",
    "resolved_printer_state",
]
