"""CLI command implementations.

Collects all subcommand functions and re-exports them for registration
with the root CLI group.

Contents:
    * Info commands from :mod:`.info`
    * Config commands from :mod:`.config`
"""

from __future__ import annotations

from .calibrate import cli_calibrate
from .config import cli_config, cli_config_deploy, cli_config_generate_examples
from .info import cli_info
from .labels import cli_capabilities, cli_explain, cli_models, cli_render, cli_schema, cli_validate
from .printer import (
    cli_cancel,
    cli_capture,
    cli_cut,
    cli_detect,
    cli_feed,
    cli_pause,
    cli_preview,
    cli_print,
    cli_reset,
    cli_status,
    cli_verify,
)

__all__ = [
    "cli_calibrate",
    "cli_cancel",
    "cli_capabilities",
    "cli_capture",
    "cli_config",
    "cli_config_deploy",
    "cli_config_generate_examples",
    "cli_cut",
    "cli_detect",
    "cli_explain",
    "cli_feed",
    "cli_info",
    "cli_models",
    "cli_pause",
    "cli_preview",
    "cli_print",
    "cli_render",
    "cli_reset",
    "cli_schema",
    "cli_status",
    "cli_validate",
    "cli_verify",
]
