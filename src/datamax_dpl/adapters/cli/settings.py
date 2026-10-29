"""Printer and camera settings: the ``[printer]`` and ``[camera]`` sections plus command-line overrides.

Command-line flags win over every configuration layer. The models refuse unknown values here, at
the boundary, so a typo in a config file is reported as a configuration error with the key named.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ...domain.camera import CameraSettings
from ...domain.enums import CutterPresence, FallbackPolicy, HardwareOption
from ...domain.errors import ConfigurationError
from ...domain.render_context import RenderOptions

if TYPE_CHECKING:
    from lib_layered_config import Config

_T = TypeVar("_T")


class PrinterSettings(BaseModel):
    """The ``[printer]`` section.

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
    # Same range as RenderOptions, so a bad value is refused here with its config key named.
    scale_across: float = Field(default=1.0, ge=0.5, le=1.5)
    scale_along: float = Field(default=1.0, ge=0.5, le=1.5)

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
    uri: str | None = None
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
    try:
        base = PrinterSettings.model_validate(dict(_section(config, "printer")))
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc, section="printer")) from None
    # model_copy, not a new PrinterSettings: the overrides are already typed, and re-validating
    # would move the refusal of a bad --timeout away from the transport, which names its range.
    return base.model_copy(
        update={
            "model": _given(overrides.model, base.model),
            "firmware": _given(overrides.firmware, base.firmware),
            "uri": _given(overrides.uri, base.uri),
            "policy": _given(overrides.policy, base.policy),
            "timeout": _given(overrides.timeout, base.timeout),
            "cutter": _given(overrides.cutter, base.cutter),
            # Options add up instead of replacing: --option names one more installed option.
            "options": base.options | overrides.options,
        }
    )


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
    )


def require_uri(settings: PrinterSettings) -> str:
    """Return the printer URI or raise when none is configured or given.

    Raises:
        ConfigurationError: Neither ``--printer`` nor ``[printer] uri`` names a printer.
    """
    if not settings.uri:
        msg = "no printer given: pass --printer URI or set [printer] uri"
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
