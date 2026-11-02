"""Click context helpers for CLI state management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import lib_cli_exit_tools

from .envelope import HUMAN, OutputMode

if TYPE_CHECKING:
    import rich_click as click
    from lib_layered_config import Config

    from datamax_dpl.composition import AppServices


@dataclass(frozen=True, slots=True)
class TracebackState:
    """Captured traceback configuration of ``lib_cli_exit_tools``.

    Attributes:
        enabled: Whether full tracebacks are shown.
        force_color: Whether they are coloured even off a terminal.
    """

    enabled: bool
    force_color: bool


@dataclass(slots=True)
class CLIContext:
    """Typed CLI context for Click subcommand access.

    Attributes:
        traceback: Whether verbose tracebacks were requested.
        config: The loaded layered configuration; empty when ``config_error`` is set, so a
            command that reads it asks through ``config_load.require_config``.
        services: All application services from the composition layer.
        profile: Optional configuration profile name.
        set_overrides: Raw ``--set`` strings, reapplied when a subcommand reloads the
            configuration with another profile.
        env_file: The root's ``--env-file``, for the same reload.
        config_error: Why the configuration could not be loaded, None when it loaded. The
            root records the exception; each command that reads the configuration reports it.
        mode: Output shape requested by ``--json`` / ``--json-bare``.
        job_on_stdout: The command sends its job to standard output (printer URI ``-``), so what
            it says to a person goes to stderr.
    """

    traceback: bool
    config: Config
    services: AppServices
    profile: str | None = None
    set_overrides: tuple[str, ...] = ()
    env_file: str | None = None
    config_error: Exception | None = None
    mode: OutputMode = HUMAN
    job_on_stdout: bool = False


def store_cli_context(
    ctx: click.Context,
    *,
    traceback: bool,
    config: Config,
    services: AppServices,
    profile: str | None = None,
    set_overrides: tuple[str, ...] = (),
    env_file: str | None = None,
    config_error: Exception | None = None,
    mode: OutputMode = HUMAN,
) -> None:
    """Store CLI state in the Click context for subcommand access.

    Args:
        ctx: Click context associated with the current invocation.
        traceback: Whether verbose tracebacks were requested.
        config: Loaded layered configuration object for all subcommands.
        services: All application services from composition layer.
        profile: Optional configuration profile name.
        set_overrides: Raw ``--set`` override strings for reapplication when
            subcommands reload config with a different profile.
        env_file: The root's ``--env-file``, for the same reload.
        config_error: Why the configuration could not be loaded, None when it loaded.
        mode: Output shape requested by ``--json`` / ``--json-bare``.

    Example:
        >>> from click.testing import CliRunner
        >>> from unittest.mock import MagicMock
        >>> from datamax_dpl.composition import build_production
        >>> ctx = MagicMock()
        >>> ctx.obj = None
        >>> mock_config = MagicMock()
        >>> services = build_production()
        >>> store_cli_context(ctx, traceback=True, config=mock_config, services=services, profile="test")
        >>> ctx.obj.traceback
        True
    """
    ctx.obj = CLIContext(
        traceback=traceback,
        config=config,
        services=services,
        profile=profile,
        set_overrides=set_overrides,
        env_file=env_file,
        config_error=config_error,
        mode=mode,
    )


def get_cli_context(ctx: click.Context) -> CLIContext:
    """Retrieve typed CLI state from Click context.

    Args:
        ctx: Click context containing CLI state.

    Returns:
        CLIContext dataclass with typed access to CLI state.

    Raises:
        RuntimeError: If CLI context was not properly initialized.

    Example:
        >>> from unittest.mock import MagicMock
        >>> ctx = MagicMock()
        >>> mock_config = MagicMock()
        >>> mock_services = MagicMock()
        >>> ctx.obj = CLIContext(traceback=False, config=mock_config, services=mock_services)
        >>> cli_ctx = get_cli_context(ctx)
        >>> cli_ctx.traceback
        False
    """
    if not isinstance(ctx.obj, CLIContext):
        raise RuntimeError("CLI context not initialized. Call store_cli_context first.")
    return ctx.obj


def output_mode(ctx: click.Context) -> OutputMode:
    """The output shape the root stored; human for a command run without the root group.

    Example:
        >>> from unittest.mock import MagicMock
        >>> ctx = MagicMock()
        >>> ctx.obj = None
        >>> output_mode(ctx).machine
        False
    """
    return ctx.obj.mode if isinstance(ctx.obj, CLIContext) else HUMAN


def apply_traceback_preferences(enabled: bool) -> None:
    """Synchronise shared traceback flags with the requested preference.

    Args:
        enabled: ``True`` enables full tracebacks with colour.

    Example:
        >>> apply_traceback_preferences(True)
        >>> bool(lib_cli_exit_tools.config.traceback)
        True
    """
    lib_cli_exit_tools.config.traceback = bool(enabled)
    lib_cli_exit_tools.config.traceback_force_color = bool(enabled)


def snapshot_traceback_state() -> TracebackState:
    """Capture the current traceback configuration for later restoration.

    Returns:
        Both flags as they are now.

    Example:
        >>> state = snapshot_traceback_state()
        >>> isinstance(state.enabled, bool) and isinstance(state.force_color, bool)
        True
    """
    return TracebackState(
        enabled=bool(getattr(lib_cli_exit_tools.config, "traceback", False)),
        force_color=bool(getattr(lib_cli_exit_tools.config, "traceback_force_color", False)),
    )


def restore_traceback_state(state: TracebackState) -> None:
    """Reapply a previously captured traceback configuration.

    Args:
        state: What :func:`snapshot_traceback_state` captured.

    Example:
        >>> original = snapshot_traceback_state()
        >>> apply_traceback_preferences(True)
        >>> restore_traceback_state(original)
        >>> lib_cli_exit_tools.config.traceback == original.enabled
        True
    """
    lib_cli_exit_tools.config.traceback = state.enabled
    lib_cli_exit_tools.config.traceback_force_color = state.force_color


__all__ = [
    "CLIContext",
    "TracebackState",
    "apply_traceback_preferences",
    "get_cli_context",
    "output_mode",
    "restore_traceback_state",
    "snapshot_traceback_state",
    "store_cli_context",
]
