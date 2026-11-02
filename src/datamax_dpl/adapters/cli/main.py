"""CLI entry point and execution wrapper.

Provides the main entry point used by console scripts and ``python -m``
execution, ensuring consistent error handling and traceback restoration.

Contents:
    * :func:`main` - Primary entry point for CLI execution.
"""

from __future__ import annotations

import sys
import threading
from typing import TYPE_CHECKING

import click
import lib_cli_exit_tools
import lib_log_rich.runtime

from datamax_dpl import __init__conf__

from .constants import TRACEBACK_SUMMARY_LIMIT, TRACEBACK_VERBOSE_LIMIT
from .context import (
    apply_traceback_preferences,
    restore_traceback_state,
    snapshot_traceback_state,
)
from .envelope import CommandName, ErrorEnvelope, OutputMode, write_envelope

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from datamax_dpl.composition import AppServices


_JSON_FLAG = "--json"
_JSON_BARE_FLAG = "--json-bare"
_JSON_FLAGS = frozenset({_JSON_FLAG, _JSON_BARE_FLAG})


def _global_flags(args: Sequence[str], root: click.Command) -> frozenset[str]:
    """The JSON flags among the global options of ``args``, read as the root's parser reads them.

    The root's own parse may be what failed, or may not have run yet (an unknown command is found
    before it), so the context cannot say. Stops at the first word that is neither an option nor
    the value of one, which is the subcommand.
    """
    takes_value = {
        name for param in root.params if isinstance(param, click.Option) and not param.is_flag for name in param.opts
    }
    flags: set[str] = set()
    words = iter(args)
    for word in words:
        if not word.startswith("-") or word == "--":
            break
        if word in _JSON_FLAGS:
            flags.add(word)
        elif word in takes_value:
            next(words, None)
    return frozenset(flags)


def _misplaced_flag(exc: click.ClickException) -> str | None:
    """The JSON flag a caller typed after the subcommand, where it does not parse."""
    name = exc.option_name if isinstance(exc, click.NoSuchOption) else None
    return name if name in _JSON_FLAGS else None


def _usage_error_mode(args: Sequence[str], exc: click.ClickException, root: click.Command) -> OutputMode:
    """The shape the command line asked for, although the error came before anything could store it."""
    flags = _global_flags(args, root) | {flag for flag in (_misplaced_flag(exc),) if flag}
    return OutputMode.of(as_json=_JSON_FLAG in flags, as_json_bare=_JSON_BARE_FLAG in flags)


def _failed_command(exc: click.ClickException) -> CommandName | None:
    """The command a usage error belongs to; None for a bad global option or an unknown command."""
    ctx = exc.ctx if isinstance(exc, click.UsageError) else None
    if ctx is None:
        return None
    _program, _space, path = ctx.command_path.partition(" ")
    try:
        return CommandName(path)
    except ValueError:
        return None


def _report_click_error(exc: click.ClickException, *, mode: OutputMode) -> None:
    """Show click's own text on stderr and, in machine mode, the error envelope on stdout."""
    exc.show()
    if mode.machine:
        envelope = ErrorEnvelope(command=_failed_command(exc), error=type(exc).__name__, message=exc.format_message())
        write_envelope(envelope, mode=mode)


def _run_cli(argv: Sequence[str] | None, *, services_factory: Callable[[], AppServices]) -> int:
    """Execute the CLI with exception handling.

    Args:
        argv: Optional sequence of CLI arguments. None uses sys.argv.
        services_factory: Factory function that returns AppServices. Passed via ctx.obj.

    Returns:
        Exit code produced by the command.
    """
    from .root import cli  # noqa: PLC0415 - deferred: lazy-loads the command tree so importing main stays cheap

    # Use Click's native invocation with obj parameter since lib_cli_exit_tools.run_cli
    # doesn't support passing obj. We replicate its behavior while adding obj support.
    args = list(argv) if argv is not None else sys.argv[1:]

    try:
        # cli is a rich_click group whose own main() reimplements click's Command.main().
        # Under standalone_mode=False it catches the click.exceptions.Exit that ctx.exit()
        # raises and RETURNS its exit code, and it returns a callback's plain return value the
        # same way, so the exit code arrives here as a return value and never as an Exit.
        # Commands therefore exit through ctx.exit() and never raise a bare SystemExit, which
        # would fall into the catch-all branch below and print as "SystemExit: N".
        exit_code = cli.main(
            args=args,
            prog_name=__init__conf__.shell_command,
            obj=services_factory,
            standalone_mode=False,
        )
        return exit_code if isinstance(exit_code, int) else 0
    except click.ClickException as exc:
        _report_click_error(exc, mode=_usage_error_mode(args, exc, cli))
        return exc.exit_code
    except BaseException as exc:
        # Catch BaseException (not just Exception) to handle SystemExit, KeyboardInterrupt,
        # and all errors at the CLI boundary. This ensures consistent error formatting via
        # lib_cli_exit_tools regardless of exception type. Intentional, not a bug.
        tracebacks_enabled = bool(getattr(lib_cli_exit_tools.config, "traceback", False))
        apply_traceback_preferences(tracebacks_enabled)
        length_limit = TRACEBACK_VERBOSE_LIMIT if tracebacks_enabled else TRACEBACK_SUMMARY_LIMIT
        lib_cli_exit_tools.print_exception_message(trace_back=tracebacks_enabled, length_limit=length_limit)
        return lib_cli_exit_tools.get_system_exit_code(exc)


def main(
    argv: Sequence[str] | None = None,
    *,
    restore_traceback: bool = True,
    services_factory: Callable[[], AppServices] | None = None,
) -> int:
    """Execute the CLI with error handling and return the exit code.

    Provides the single entry point used by console scripts and
    ``python -m`` execution so that behaviour stays identical across transports.

    Args:
        argv: Optional sequence of CLI arguments. None uses sys.argv.
        restore_traceback: Whether to restore prior traceback configuration after execution.
        services_factory: Factory function returning AppServices. Required.
            Callers outside the adapters layer should pass ``build_production``.

    Returns:
        Exit code reported by the CLI run.

    Raises:
        ValueError: If services_factory is not provided.

    Example:
        >>> from datamax_dpl.composition import build_production
        >>> exit_code = main(["--help"], services_factory=build_production)  # doctest: +SKIP
        >>> exit_code == 0  # doctest: +SKIP
        True
    """
    if services_factory is None:
        raise ValueError("services_factory is required. Pass build_production from composition layer.")

    previous_state = snapshot_traceback_state()
    try:
        return _run_cli(argv, services_factory=services_factory)
    finally:
        if restore_traceback:
            restore_traceback_state(previous_state)
        # Only shutdown logging from main thread to avoid killing logging for other threads.
        is_main_thread = threading.current_thread() is threading.main_thread()
        if is_main_thread and lib_log_rich.runtime.is_initialised():
            lib_log_rich.runtime.shutdown()


__all__ = ["main"]
