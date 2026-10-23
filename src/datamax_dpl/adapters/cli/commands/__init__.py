"""CLI command implementations.

Collects all subcommand functions and re-exports them for registration
with the root CLI group.

Contents:
    * Info commands from :mod:`.info`
    * Config commands from :mod:`.config`
"""

from __future__ import annotations

from .config import cli_config, cli_config_deploy, cli_config_generate_examples
from .info import cli_info
from .labels import cli_capabilities, cli_explain, cli_models, cli_render, cli_schema, cli_validate

__all__ = [
    "cli_capabilities",
    "cli_config",
    "cli_config_deploy",
    "cli_config_generate_examples",
    "cli_explain",
    "cli_info",
    "cli_models",
    "cli_render",
    "cli_schema",
    "cli_validate",
]
