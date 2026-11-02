"""Options every command that renders or talks to a printer shares, and how they become settings."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

import rich_click as click

from ....domain.enums import CutterPresence, FallbackPolicy, HardwareOption
from ....domain.errors import ConfigurationError
from ....domain.profiles import Firmware, parse_firmware
from ...config.sections import PRINTER_SECTION, printer_section
from ..settings import PrinterOverrides, PrinterSettings, printer_settings, transport_tuning
from ..typed_click import option

if TYPE_CHECKING:
    from lib_layered_config import Config

    from ....application.ports import OpenPrinter
    from ..context import CLIContext

_Decorator = Callable[[Callable[..., Any]], Callable[..., Any]]

_RENDER_OPTIONS: tuple[_Decorator, ...] = (
    option(
        "--model",
        default=None,
        help=f"Printer model profile (see 'datamax-dpl models'); default {PRINTER_SECTION.setting('model')}.",
    ),
    option("--firmware", default=None, help="Printer firmware, e.g. 2.01; unlocks features newer firmware has."),
    option(
        "--policy",
        type=click.Choice([p.value for p in FallbackPolicy]),
        default=None,
        help="degrade: apply fallbacks and report them; strict: refuse instead.",
    ),
    option("--option", "options", multiple=True, help="Installed printer option, e.g. datamatrix (repeatable)."),
    option(
        "--cutter",
        type=click.Choice([c.value for c in CutterPresence]),
        default=None,
        help=f"Whether the printer has a working cutter; default {PRINTER_SECTION.setting('cutter')}.",
    ),
)

#: A configured printer by name ([printers.<name>]) or a printer address; render and validate take
#: it too, because a named printer decides the model and the scale factors.
_PRINTER_CHOICE = option(
    "--printer",
    "printer",
    default=None,
    help=(
        f"Configured printer name ({printer_section('NAME').header}) or URI, e.g. tcp://192.0.2.60:9100;"
        f" default {PRINTER_SECTION.setting('uri')}."
    ),
)

_PRINTER_OPTIONS: tuple[_Decorator, ...] = (
    _PRINTER_CHOICE,
    option(
        "--timeout",
        type=float,
        default=None,
        help=(
            f"Seconds to wait for the printer; default {PRINTER_SECTION.setting('timeout')}. For status, memory"
            f" and verify, the whole wait for the printer instead of {PRINTER_SECTION.setting('status_wait')}."
        ),
    ),
)


def _stack(decorators: tuple[_Decorator, ...]) -> _Decorator:
    def apply(function: Callable[..., Any]) -> Callable[..., Any]:
        for decorator in reversed(decorators):
            function = decorator(function)
        return function

    return apply


render_options_decorator = _stack(_RENDER_OPTIONS)
#: For commands that render but never talk to the printer (render, validate).
printer_choice_decorator = _PRINTER_CHOICE
printer_options_decorator = _stack(_PRINTER_OPTIONS)


def loaded_config(cli_ctx: CLIContext) -> Config:
    """Return the configuration the root loaded.

    Raises:
        ConfigurationError: It could not be loaded; the runner reports it with exit 2, in the
            output shape the caller asked for.
    """
    if cli_ctx.config_error is not None:
        msg = f"configuration could not be loaded: {cli_ctx.config_error}"
        raise ConfigurationError(msg) from cli_ctx.config_error
    return cli_ctx.config


def command_overrides(given: Mapping[str, Any]) -> PrinterOverrides:
    """Read the printer options a command was given (its click keyword arguments) into overrides.

    The click options are the command's raw input, so they are validated whole: the model's
    ``extra="forbid"`` refuses an option name it does not know, where picking known names out of
    ``given`` would silently drop a renamed one. ``--option`` and ``--firmware`` are read first so
    an unknown or malformed value is refused as a configuration error (see :func:`hardware_options`
    and :func:`firmware_option`).

    Raises:
        ConfigurationError: An ``--option`` value names no :class:`HardwareOption`, or
            ``--firmware`` is not ``<major>.<minor>``.
        pydantic.ValidationError: Another option holds a value of the wrong type.

    Example:
        >>> command_overrides({"model": "allegro-2", "options": ("datamatrix",)}).model
        'allegro-2'
    """
    options = hardware_options(given.get("options") or ())
    firmware = firmware_option(given.get("firmware"))
    return PrinterOverrides.model_validate({**given, "options": options, "firmware": firmware})


def resolve_settings(cli_ctx: CLIContext, overrides: PrinterOverrides) -> PrinterSettings:
    """Merge the ``[printer]`` configuration with the command-line ``overrides``.

    Raises:
        ConfigurationError: The configuration could not be loaded, or a configured value is invalid.
    """
    return printer_settings(loaded_config(cli_ctx), overrides)


def printer_opener(cli_ctx: CLIContext, settings: PrinterSettings) -> OpenPrinter:
    """The printer opener for ``settings``: the composition's, with their transport tuning bound."""
    return cli_ctx.services.tuned_open_printer(transport_tuning(settings))


def hardware_options(values: Iterable[str]) -> frozenset[HardwareOption]:
    """Read the ``--option`` values, refusing one this package does not know.

    The refusal is a configuration error rather than a click usage error so that it reaches the
    caller in the output shape it asked for (a JSON envelope under ``--json``), the same way an
    unknown ``[printer] options`` entry does.

    Raises:
        ConfigurationError: A value names no :class:`HardwareOption`.

    Example:
        >>> sorted(o.value for o in hardware_options(["datamatrix"]))
        ['datamatrix']
        >>> hardware_options(["laser"])
        Traceback (most recent call last):
        ...
        datamax_dpl.domain.errors.ConfigurationError: --option: Input should be 'datamatrix', got 'laser'
    """
    given = tuple(values)
    known = {member.value: member for member in HardwareOption}
    unknown = [value for value in given if value not in known]
    if unknown:
        msg = f"--option: Input should be {_alternatives(list(known))}, got {unknown[0]!r}"
        raise ConfigurationError(msg)
    return frozenset(known[value] for value in given)


def firmware_option(value: str | None) -> Firmware | None:
    """Read the ``--firmware`` value, refusing a malformed one.

    Refused as a configuration error for the reason :func:`hardware_options` gives. Empty text
    parses to None, the same as not giving the flag, so the configured firmware applies.

    Raises:
        ConfigurationError: The value is not ``<major>.<minor>``.

    Example:
        >>> firmware_option("2.01")
        Firmware(major=2, minor=1)
        >>> firmware_option(None) is None
        True
        >>> firmware_option("two")
        Traceback (most recent call last):
        ...
        datamax_dpl.domain.errors.ConfigurationError: --firmware: firmware must look like 2.01, got 'two'
    """
    if value is None:
        return None
    try:
        return parse_firmware(value)
    except ValueError as exc:
        raise ConfigurationError(f"--firmware: {exc}") from None


def _alternatives(values: list[str]) -> str:
    """Quote and join ``values`` the way pydantic lists an enum's members: 'a', 'b' or 'c'."""
    quoted = [repr(value) for value in values]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} or {quoted[-1]}"


__all__ = [
    "command_overrides",
    "firmware_option",
    "hardware_options",
    "loaded_config",
    "printer_choice_decorator",
    "printer_opener",
    "printer_options_decorator",
    "render_options_decorator",
    "resolve_settings",
]
