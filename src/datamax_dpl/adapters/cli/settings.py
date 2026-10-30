"""Printer and camera settings: the ``[printer]`` and ``[camera]`` sections plus command-line overrides.

Command-line flags win over every configuration layer. The models refuse unknown values here, at
the boundary, so a typo in a config file is reported as a configuration error with the key named.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from ...domain.camera import CameraSettings
from ...domain.enums import CutterPresence, FallbackPolicy, HardwareOption, ImageModule
from ...domain.errors import ConfigurationError
from ...domain.job import MAX_STOP_POSITION_MM
from ...domain.label import stop_position_or_printer
from ...domain.render_context import RenderOptions
from ...domain.units import MAX_OFFSET_MM, MAX_SCALE, MIN_SCALE
from ..transport.uri import is_printer_name, is_printer_uri

if TYPE_CHECKING:
    from lib_layered_config import Config

_T = TypeVar("_T")

_BoundedStop = Annotated[float, Field(gt=0, le=MAX_STOP_POSITION_MM)]
_ConfiguredStop = Annotated[_BoundedStop | Literal["printer"], stop_position_or_printer(TypeAdapter(_BoundedStop))]


class PrinterSettings(BaseModel):
    """The ``[printer]`` section, merged with a ``[printers.<name>]`` section when one is chosen.

    ``name`` is the chosen printer's name, empty when none was; ``label_width`` and
    ``label_height`` are the loaded labels' size in millimetres, when known. A scale, offset,
    stop position, image memory budget or image module left unset (None) takes the model's
    default when the job renders.

    Example:
        >>> PrinterSettings().model
        'generic'
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    model: str = "generic"
    firmware: str = ""
    uri: str = ""
    policy: FallbackPolicy = FallbackPolicy.DEGRADE
    timeout: float = Field(default=10.0, gt=0)
    cutter: CutterPresence = CutterPresence.UNKNOWN
    options: frozenset[HardwareOption] = Field(default_factory=frozenset[HardwareOption])
    # Same range as RenderOptions, so a bad value is refused here with its config key named. None
    # is not 1.0 or 0.0: a neutral value set here wins over the model's measured default.
    scale_across: float | None = Field(default=None, ge=MIN_SCALE, le=MAX_SCALE)
    scale_along: float | None = Field(default=None, ge=MIN_SCALE, le=MAX_SCALE)
    offset_across: float | None = Field(default=None, ge=-MAX_OFFSET_MM, le=MAX_OFFSET_MM)
    offset_along: float | None = Field(default=None, ge=-MAX_OFFSET_MM, le=MAX_OFFSET_MM)
    # Bounded here, unlike RenderOptions, so a config typo is refused with its key named before any
    # job is built, rather than as a STOP_POSITION_TOO_LARGE finding on every label. "printer"
    # sends no stop and keeps the printer's own; the cutter stop has no such value, since unset
    # already sends none for a job that cuts.
    stop_position: _ConfiguredStop | None = None
    cutter_stop_position: float | None = Field(default=None, gt=0, le=MAX_STOP_POSITION_MM)
    # None is not "no budget": it takes the model's, which most models do not have.
    image_memory_bytes: int | None = Field(default=None, gt=0)
    # None is not module A: it takes the model's built-in module, E on a DMX 600/800 and none on a
    # Prodigy or Prodigy Plus, which refuses a job that downloads an image until this is set.
    image_module: ImageModule | None = None
    name: str = ""
    label_width: float | None = Field(default=None, gt=0)
    label_height: float | None = Field(default=None, gt=0)

    @field_validator("firmware", mode="before")
    @classmethod
    def _firmware_as_text(cls, value: object) -> object:
        """Accept 2.01 as the layered config delivers an unquoted env value: a number.

        A float loses trailing zeros (2.10 arrives as 2.1), which ``parse_firmware`` reads the
        same way, so the comparison is unaffected.
        """
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return value


class PrinterOverrides(BaseModel):
    """Command-line values that replace configured ones when given (None means not given)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str | None = None
    firmware: str | None = None
    printer: str | None = None
    policy: FallbackPolicy | None = None
    timeout: float | None = None
    cutter: CutterPresence | None = None
    options: frozenset[HardwareOption] = Field(default_factory=frozenset[HardwareOption])


def _section(config: Config, name: str) -> Mapping[str, Any]:
    section: object = config.get(name, {})
    return cast("Mapping[str, Any]", section) if isinstance(section, Mapping) else {}


def _describe(exc: ValidationError, *, section: str) -> str:
    return "; ".join(f"{section}.{'.'.join(str(p) for p in error['loc'])}: {error['msg']}" for error in exc.errors())


def printer_settings(config: Config, overrides: PrinterOverrides) -> PrinterSettings:
    """Merge the ``[printer]`` section with command-line overrides.

    Raises:
        ConfigurationError: A configured value is invalid; the message names the key.
    """
    name = overrides.printer if overrides.printer and not is_printer_uri(overrides.printer) else ""
    section = "printer" if not name else f"printers.{name}"
    try:
        base = PrinterSettings.model_validate({**_section(config, "printer"), **_named(config, name), "name": name})
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc, section=section)) from None
    uri = overrides.printer if overrides.printer and is_printer_uri(overrides.printer) else None
    # model_copy, not a new PrinterSettings: the overrides are already typed, and re-validating
    # would move the refusal of a bad --timeout away from the transport, which names its range.
    return base.model_copy(
        update={
            "model": _given(overrides.model, base.model),
            "firmware": _given(overrides.firmware, base.firmware),
            "uri": _given(uri, base.uri),
            "policy": _given(overrides.policy, base.policy),
            "timeout": _given(overrides.timeout, base.timeout),
            "cutter": _given(overrides.cutter, base.cutter),
            # Options add up instead of replacing: --option names one more installed option.
            "options": base.options | overrides.options,
        }
    )


def _named(config: Config, name: str) -> Mapping[str, Any]:
    """Return the ``[printers.<name>]`` section, or nothing when no name was chosen.

    Raises:
        ConfigurationError: A configured section has an invalid name, or none is called ``name``.
    """
    printers = _section(config, "printers")
    invalid = sorted(key for key in printers if not is_printer_name(key))
    if invalid:
        msg = f"printers.{invalid[0]}: a printer name takes letters, digits, '_' and '-' (not first), no ':'"
        raise ConfigurationError(msg)
    if not name:
        return {}
    section: object = printers.get(name)
    if not isinstance(section, Mapping):
        configured = ", ".join(sorted(printers)) or "none"
        msg = f"no printer named '{name}' in [printers]; configured: {configured}"
        raise ConfigurationError(msg)
    return cast("Mapping[str, Any]", section)


def _given(override: _T | None, configured: _T) -> _T:
    """Return the command-line value when one was given, else the configured one."""
    return configured if override is None else override


def camera_settings(config: Config) -> CameraSettings:
    """Read the ``[camera]`` section.

    Raises:
        ConfigurationError: A configured value is invalid; the message names the key.
    """
    try:
        return CameraSettings.model_validate(dict(_section(config, "camera")))
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc, section="camera")) from None


_CUTTER: dict[CutterPresence, bool | None] = {
    CutterPresence.YES: True,
    CutterPresence.NO: False,
    CutterPresence.UNKNOWN: None,
}


def render_options(settings: PrinterSettings) -> RenderOptions:
    """The render options a printer configuration implies.

    Example:
        >>> render_options(PrinterSettings(model="dmx-600", cutter=CutterPresence.YES)).cutter_installed
        True
    """
    return RenderOptions(
        model=settings.model,
        firmware=settings.firmware,
        policy=settings.policy,
        cutter_installed=_CUTTER[settings.cutter],
        options=settings.options,
        scale_across=settings.scale_across,
        scale_along=settings.scale_along,
        offset_across=settings.offset_across,
        offset_along=settings.offset_along,
        stop_position_mm=settings.stop_position,
        cutter_stop_position_mm=settings.cutter_stop_position,
        image_memory_bytes=settings.image_memory_bytes,
        image_module=settings.image_module,
    )


def require_uri(settings: PrinterSettings) -> str:
    """Return the printer URI or raise when none is configured or given.

    Raises:
        ConfigurationError: Neither ``--printer`` nor ``[printer] uri`` names a printer.
    """
    if not settings.uri:
        msg = "no printer given: pass --printer NAME or URI, or set [printer] uri"
        raise ConfigurationError(msg)
    return settings.uri


__all__ = [
    "PrinterOverrides",
    "PrinterSettings",
    "camera_settings",
    "printer_settings",
    "render_options",
    "require_uri",
]
