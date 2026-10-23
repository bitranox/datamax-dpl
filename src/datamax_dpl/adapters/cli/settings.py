"""Printer and camera settings: the ``[printer]`` and ``[camera]`` sections plus command-line overrides.

Command-line flags win over every configuration layer. The models refuse unknown values here, at
the boundary, so a typo in a config file is reported as a configuration error with the key named.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...domain.camera import CameraSettings
from ...domain.enums import FallbackPolicy
from ...domain.errors import ConfigurationError
from ...domain.render_context import RenderOptions

if TYPE_CHECKING:
    from lib_layered_config import Config


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
    cutter: Literal["yes", "no", "unknown"] = "unknown"
    options: list[str] = Field(default_factory=list[str])


class PrinterOverrides(BaseModel):
    """Command-line values that replace configured ones when given (None means not given)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str | None = None
    firmware: str | None = None
    uri: str | None = None
    policy: FallbackPolicy | None = None
    timeout: float | None = None
    cutter: Literal["yes", "no", "unknown"] | None = None
    options: tuple[str, ...] = ()


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
    given = {k: v for k, v in overrides.model_dump(exclude={"options"}).items() if v is not None}
    options = sorted({*base.options, *overrides.options})
    return base.model_copy(update={**given, "options": options})


def camera_settings(config: Config) -> CameraSettings:
    """Read the ``[camera]`` section.

    Raises:
        ConfigurationError: A configured value is invalid; the message names the key.
    """
    try:
        return CameraSettings.model_validate(dict(_section(config, "camera")))
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc, section="camera")) from None


_CUTTER: dict[str, bool | None] = {"yes": True, "no": False, "unknown": None}


def render_options(settings: PrinterSettings) -> RenderOptions:
    """The render options a printer configuration implies.

    Example:
        >>> render_options(PrinterSettings(model="dmx-600", cutter="yes")).cutter_installed
        True
    """
    return RenderOptions(
        model=settings.model,
        firmware=settings.firmware,
        policy=settings.policy,
        cutter_installed=_CUTTER[settings.cutter],
        options=frozenset(settings.options),
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
