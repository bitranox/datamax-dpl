"""Options every command that renders or talks to a printer shares, and how they become settings."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import rich_click as click

from ...domain.enums import FallbackPolicy
from ..config_load import require_config
from ..context import CLIContext
from ..settings import PrinterOverrides, PrinterSettings, printer_settings
from ..typed_click import option

_Decorator = Callable[[Callable[..., Any]], Callable[..., Any]]

_RENDER_OPTIONS: tuple[_Decorator, ...] = (
    option("--model", default=None, help="Printer model profile (see 'datamax-dpl models'); default [printer] model."),
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
        type=click.Choice(["yes", "no", "unknown"]),
        default=None,
        help="Whether the printer has a working cutter; default [printer] cutter.",
    ),
)

_PRINTER_OPTIONS: tuple[_Decorator, ...] = (
    option("--printer", "uri", default=None, help="Printer URI, e.g. tcp://192.0.2.60:9100; default [printer] uri."),
    option("--timeout", type=float, default=None, help="Seconds to wait for the printer; default [printer] timeout."),
)


def _stack(decorators: tuple[_Decorator, ...]) -> _Decorator:
    def apply(function: Callable[..., Any]) -> Callable[..., Any]:
        for decorator in reversed(decorators):
            function = decorator(function)
        return function

    return apply


render_options_decorator = _stack(_RENDER_OPTIONS)
printer_options_decorator = _stack(_PRINTER_OPTIONS)


def resolve_settings(ctx: click.Context, cli_ctx: CLIContext, **given: Any) -> PrinterSettings:
    """Merge the ``[printer]`` configuration with the options this command was given."""
    config = require_config(ctx, cli_ctx)
    known = {key: given.get(key) for key in ("model", "firmware", "uri", "policy", "timeout", "cutter")}
    overrides = PrinterOverrides(**known, options=tuple(given.get("options") or ()))
    return printer_settings(config, overrides)


__all__ = ["printer_options_decorator", "render_options_decorator", "resolve_settings"]
