"""Render options and the per-render context every encoder step shares.

The context answers three questions for the encoders: does this model (at this firmware, with
these options) have a feature, how is a length written in this job's DPL unit, and where do
findings go.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from .enums import Attribute, FallbackPolicy, Unit
from .units import DplUnit, to_dots, to_dpl, to_inches

if TYPE_CHECKING:
    from .findings import FindingCollector
    from .profiles import Feature, Firmware, PrinterProfile

#: Largest value of a four-digit DPL distance field.
MAX_FIELD_VALUE = 9999


class RenderOptions(BaseModel):
    """How to render: for which model, at which firmware, with which policy.

    Attributes:
        model: Profile id (``datamax-dpl models`` lists them).
        firmware: Printer firmware ``"<major>.<minor>"``; empty means the oldest the manual covers.
        policy: ``degrade`` applies fallbacks, ``strict`` refuses the label instead.
        cutter_installed: Whether the printer has its cutter installed and enabled; None if unknown.
        options: Installed hardware options, e.g. ``{"datamatrix"}``.

    Example:
        >>> RenderOptions(model="dmx-600").policy
        <FallbackPolicy.DEGRADE: 'degrade'>
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = "generic"
    firmware: str = ""
    policy: FallbackPolicy = FallbackPolicy.DEGRADE
    cutter_installed: bool | None = None
    options: frozenset[str] = Field(default_factory=frozenset[str])


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
        system_prefix: System commands sent before ``<STX>L`` (downloads, sensor, stop position).
        system_suffix: System commands sent after the format (cut now).
        image_counter: Number of raster fallback images created so far, for their names.
    """

    profile: PrinterProfile
    firmware: Firmware | None
    options: frozenset[str]
    unit: Unit
    dpl_unit: DplUnit
    findings: FindingCollector
    cutter_installed: bool | None = None
    symbol_set: str | None = None
    default_attribute: Attribute = Attribute.XOR
    system_prefix: list[bytes] = field(default_factory=list[bytes])
    system_suffix: list[bytes] = field(default_factory=list[bytes])
    image_counter: int = 0

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
        return f"{self.profile.name} needs the '{support.option}' option installed"

    def distance(self, value: float, *, element: int | None, what: str) -> int:
        """Convert a label length to this job's DPL unit, refusing values past four digits."""
        converted = to_dpl(value, self.unit, target=self.dpl_unit, dpi=self.profile.dpi).value
        if converted > MAX_FIELD_VALUE:
            self.findings.refuse(
                "distance_too_large",
                f"{what} is {converted} {self.dpl_unit.value} units; DPL fields hold at most {MAX_FIELD_VALUE}",
                element=element,
            )
            return MAX_FIELD_VALUE
        return converted

    def inch_hundredths(self, value: float) -> int:
        """Convert a label length to hundredths of an inch (system commands use inch mode)."""
        return to_dpl(value, self.unit, target=DplUnit.INCH_HUNDREDTHS, dpi=self.profile.dpi).value

    def dots(self, value: float) -> int:
        """Convert a label length to whole printer dots, at least one (a size)."""
        return to_dots(value, self.unit, dpi=self.profile.dpi)

    def offset_dots(self, value: float) -> int:
        """Convert a label length to the nearest whole number of dots, zero allowed (a position)."""
        return round(to_inches(value, self.unit, dpi=self.profile.dpi) * self.profile.dpi)

    def next_image_name(self) -> str:
        """Return a fresh name for a raster fallback image (at most 8 characters)."""
        self.image_counter += 1
        return f"DPLR{self.image_counter:04d}"


__all__ = ["MAX_FIELD_VALUE", "RenderContext", "RenderOptions"]
