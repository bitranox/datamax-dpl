"""Display configuration - delegates to lib_layered_config.

Thin wrapper around lib_layered_config's Rich-styled display_config,
adding log flush before output to prevent mixing log messages with
configuration display.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import lib_log_rich.runtime
from lib_layered_config import Config
from lib_layered_config import OutputFormat as LibOutputFormat
from lib_layered_config import display_config as _lib_display

from datamax_dpl.domain.enums import OutputFormat

if TYPE_CHECKING:
    from rich.console import Console

# What Config.get returns for a missing key, as a key may hold None.
_ABSENT = object()


def config_view(config: Config, *, section: str | None = None) -> dict[str, Any]:
    """What ``config --format json`` prints, as data: redacted, the whole tree or one section.

    Args:
        config: The loaded configuration.
        section: A dotted section or key to show only that; None shows everything.

    Returns:
        The redacted tree, or ``{section: value}``.

    Raises:
        ValueError: If a section was requested that doesn't exist.

    Example:
        >>> config_view(Config({"printer": {"token": "x", "model": "allegro-2"}}, {}), section="printer")
        {'printer': {'token': '***REDACTED***', 'model': 'allegro-2'}}
    """
    if section is None:
        return config.as_dict(redact=True)
    if config.get(section, default=_ABSENT) is _ABSENT:
        raise ValueError(f"Section '{section}' not found")
    return {section: config.get_redacted(section)}


def display_config(
    config: Config,
    *,
    output_format: OutputFormat = OutputFormat.HUMAN,
    section: str | None = None,
    console: Console | None = None,
    profile: str | None = None,
) -> None:
    """Display configuration using lib_layered_config's Rich display.

    Flushes any pending log output before displaying to prevent
    mixing log messages with configuration output.

    Args:
        config: Already-loaded layered configuration object to display.
        output_format: Output format: OutputFormat.HUMAN for TOML-like display or
            OutputFormat.JSON for JSON. Defaults to OutputFormat.HUMAN.
        section: Optional section name to display only that section. When None,
            displays all configuration.
        console: Optional Rich Console for output. When None, uses the module-level
            default. Primarily useful for testing.
        profile: Optional profile name to include in provenance comments.

    Side Effects:
        Flushes pending log messages before display.
        Writes formatted configuration to stdout.

    Raises:
        ValueError: If a section was requested that doesn't exist.
    """
    # The library reads an empty name as "everything"; config_view calls it not found.
    if section == "":
        raise ValueError("Section '' not found")
    if lib_log_rich.runtime.is_initialised():
        lib_log_rich.runtime.flush()

    lib_format = LibOutputFormat(output_format.value)
    _lib_display(config, output_format=lib_format, section=section, profile=profile, console=console)


__all__ = ["config_view", "display_config"]
