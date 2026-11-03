"""Load the layered configuration for the CLI, and refuse a command that needs a broken one.

The root group loads the configuration before any subcommand's options are parsed, so it
cannot report a failure the way the subcommand would, and it must not block a command that
never reads the configuration: ``config-deploy`` is how a broken file gets replaced, and
``info`` or ``--help`` have to keep working meanwhile. The root therefore records the
failure, and each command that reads the configuration asks for it through
:func:`require_config`.

What the command line itself gets wrong is not a configuration failure: a malformed or
conflicting ``--set`` or an invalid ``--profile`` name is a usage error (exit 2), checked BEFORE
loading so that a broken file cannot hide it from a command that does not read the
configuration.

Contents:
    * :func:`load_config` - load with profile, ``.env`` and ``--set``, or say why not.
    * :func:`start_logging` - start logging; an invalid ``[lib_log_rich]`` value or ``LOG_*``
      variable is a load failure.
    * :func:`check_logging` - the same refusal for a configuration loaded after logging started.
    * :func:`require_config` - the configuration, or exit 78 naming the failure.
    * :func:`report_load_failure` - one line per problem, after the traceback on request.
    * :func:`echo_load_traceback` - the loader's traceback alone, for a caller with its own line.
"""

from __future__ import annotations

from dataclasses import dataclass
from traceback import format_exception
from typing import TYPE_CHECKING, NoReturn

import rich_click as click
from lib_layered_config import Config, ConfigError

from datamax_dpl.adapters.config.loader import validate_profile
from datamax_dpl.adapters.config.overrides import apply_overrides, nest_overrides
from datamax_dpl.adapters.error_text import error_text
from datamax_dpl.adapters.logging.setup import InvalidLoggingConfigError

from . import safe_console
from .envelope import CommandName, write_error_envelope
from .exit_codes import ExitCode

if TYPE_CHECKING:
    from datamax_dpl.composition import AppServices

    from .context import CLIContext

#: Every way loading a configuration can fail: a broken or invalid file (ConfigError, which
#: lib_layered_config's own validation error subclasses, and which since 6.0.0 also covers a
#: TOML or ``.env`` file that is not UTF-8) and a file the running user cannot read (OSError).
#: Anything else the loader raises is a bug and propagates as one.
_LOAD_ERRORS = (ConfigError, OSError)


def _check_command_line(profile: str | None, set_overrides: tuple[str, ...]) -> None:
    """Refuse a malformed or conflicting ``--set`` or an invalid ``--profile`` name (exit 2)."""
    try:
        if profile is not None:
            validate_profile(profile)
        nest_overrides(set_overrides)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class ConfigLoad:
    """A configuration as loaded, or why it could not be.

    Attributes:
        config: The loaded configuration; empty when ``error`` is set.
        error: Why loading (or starting logging with it) failed, None when it did not.
    """

    config: Config
    error: Exception | None


def load_config(
    services: AppServices, *, profile: str | None, env_file: str | None, set_overrides: tuple[str, ...]
) -> ConfigLoad:
    """Load the layered configuration, or say why it could not be loaded.

    Args:
        services: The composition's services; only ``get_config`` is used.
        profile: The profile to load, or None for none.
        env_file: An explicit ``.env`` file, or None to search for one.
        set_overrides: The ``--set`` values, applied only to a configuration that loaded.

    Returns:
        The configuration and no error, or an empty configuration and the exception that says
        why loading failed.

    Raises:
        click.UsageError: A ``--set`` value is malformed, two of them conflict, or the
            profile name is invalid;
            that is a command-line error (exit 2), not a configuration one, and it is
            raised whether or not the configuration would load.

    Example:
        >>> from datamax_dpl.composition import build_testing
        >>> loaded = load_config(build_testing(), profile=None, env_file=None, set_overrides=("a.b=1",))
        >>> loaded.config.get("a"), loaded.error
        ({'b': 1}, None)
    """
    _check_command_line(profile, set_overrides)
    try:
        config = services.get_config(profile=profile, dotenv_path=env_file)
    except _LOAD_ERRORS as exc:
        return ConfigLoad(config=Config({}, {}), error=exc)
    return ConfigLoad(config=apply_overrides(config, set_overrides), error=None)


def start_logging(services: AppServices, loaded: ConfigLoad, *, env_file: str | None) -> ConfigLoad:
    """Start logging with ``config``; a logging section it refuses is recorded like a load failure.

    An invalid ``[lib_log_rich]`` value or ``LOG_*`` variable would otherwise stop every
    command, ``config-deploy`` (which replaces the file holding it) included. ``init_logging``
    then starts logging with its defaults (and no ``LOG_*`` variable, if one of them is the
    refused setting) before it raises, and the commands that read the configuration refuse with
    exit 78 and the refusal: ``lib_log_rich.<key>: <reason>`` for a problem the section's type
    check finds, lib_log_rich's own message for a value only lib_log_rich refuses, whether it
    came from the section or a ``LOG_*`` variable (``lib_log_rich: Invalid runtime settings:
    console_level: Unknown log level: 'bogus'`` for the section, the same line naming
    ``LOG_CONSOLE_LEVEL`` for the variable).

    Args:
        services: The composition's services; only ``init_logging`` is used.
        loaded: What :func:`load_config` returned.
        env_file: The ``--env-file`` path, or None; logging reads its ``LOG_*`` lines from it.

    Returns:
        ``loaded`` unchanged, or an empty configuration and the earlier failure, or the
        logging failure when no earlier one was recorded.
    """
    try:
        services.init_logging(loaded.config, dotenv_path=env_file)
    except InvalidLoggingConfigError as exc:
        return ConfigLoad(config=Config({}, {}), error=loaded.error or exc)
    return loaded


def check_logging(services: AppServices, config: Config) -> Exception | None:
    """Judge the logging settings of a configuration loaded after logging started; a refusal is returned.

    ``config --profile`` reloads the configuration once logging is running, so
    :func:`start_logging` never sees the reloaded ``[lib_log_rich]`` section; without this an
    invalid value there would display with exit 0 while plain ``config`` exits 78. The check
    refuses what :func:`start_logging` refuses, in the same words, and never touches the
    running runtime: the command keeps logging with the settings logging started with.

    Args:
        services: The composition's services; only ``check_logging_config`` is used.
        config: The configuration :func:`load_config` returned.

    Returns:
        None, or the refusal.
    """
    try:
        services.check_logging_config(config)
    except InvalidLoggingConfigError as exc:
        return exc
    return None


def echo_load_traceback(error: Exception, *, show_traceback: bool) -> None:
    """Write the loader's chained traceback to stderr when ``--traceback`` was given.

    The failure is recorded, not raised, so the usual ``--traceback`` handling in ``main()``
    never sees it; without this the flag would show nothing about why loading failed.

    Args:
        error: The exception :func:`load_config` returned.
        show_traceback: Whether ``--traceback`` was given.
    """
    if show_traceback:
        safe_console.echo("".join(format_exception(error)).rstrip(), err=True)


def report_load_failure(error: Exception, *, show_traceback: bool) -> None:
    """Write why the configuration did not load: one line per problem, after the traceback on request.

    Args:
        error: The exception :func:`load_config` returned.
        show_traceback: Whether ``--traceback`` was given.
    """
    echo_load_traceback(error, show_traceback=show_traceback)
    problems = error.problems if isinstance(error, InvalidLoggingConfigError) else [error_text(error)]
    for problem in problems:
        safe_console.echo(f"Error: {problem}", err=True)


def fail_config_load(ctx: click.Context, cli_ctx: CLIContext, error: Exception) -> NoReturn:
    """End the ``config`` command for a configuration that did not load: stderr, envelope, exit 78.

    Args:
        ctx: The running command's click context.
        cli_ctx: The state the root group stored.
        error: Why it did not load.

    Raises:
        click.exceptions.Exit: Always, with exit code 78.
    """
    report_load_failure(error, show_traceback=cli_ctx.traceback)
    write_error_envelope(error, command=CommandName.CONFIG, mode=cli_ctx.mode)
    ctx.exit(ExitCode.CONFIG_ERROR)


def require_config(ctx: click.Context, cli_ctx: CLIContext) -> Config:
    """Return the configuration the root loaded, or end the command with exit 78.

    Args:
        ctx: The running command's click context.
        cli_ctx: The state the root group stored.

    Returns:
        The loaded configuration.

    Raises:
        click.exceptions.Exit: The configuration could not be loaded; one stderr line names
            the reason, after the loader's traceback when ``--traceback`` was given.
    """
    if cli_ctx.config_error is not None:
        fail_config_load(ctx, cli_ctx, cli_ctx.config_error)
    return cli_ctx.config


__all__ = [
    "ConfigLoad",
    "check_logging",
    "echo_load_traceback",
    "fail_config_load",
    "load_config",
    "report_load_failure",
    "require_config",
    "start_logging",
]
