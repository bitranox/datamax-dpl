"""CLI entry point and execution wrapper.

Provides the main entry point used by console scripts and ``python -m``
execution, ensuring consistent error handling and traceback restoration.

Contents:
    * :func:`main` - Primary entry point for CLI execution.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import TYPE_CHECKING, NamedTuple, TextIO

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
from .envelope import (
    CommandName,
    ErrorEnvelope,
    Outcome,
    OutputMode,
    envelope_started,
    error_text,
    forget_started_envelope,
    write_envelope,
)
from .exit_codes import ExitCode
from .gone_reader import is_gone_reader_error
from .interrupt import noting_interrupts

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from datamax_dpl.composition import AppServices


_JSON_FLAG = "--json"
_JSON_BARE_FLAG = "--json-bare"
_JSON_FLAGS = frozenset({_JSON_FLAG, _JSON_BARE_FLAG})
_INTERRUPTED = "interrupted"
_JOB_MAY_BE_OUT = "; part of a job may already have been sent"
#: The commands that send anything to a printer: only their interrupt can leave a job half out.
_SENDING_COMMANDS = frozenset(
    {
        CommandName.PRINT,
        CommandName.CALIBRATE_PRINT,
        CommandName.RESET,
        CommandName.CANCEL,
        CommandName.PAUSE,
        CommandName.FEED,
        CommandName.CUT,
    }
)


class _CommandLine(NamedTuple):
    """A command line read as the root's parser reads it.

    Attributes:
        flags: The JSON flags among its global options.
        rest: The words after the global options, the subcommand first.
    """

    flags: frozenset[str]
    rest: tuple[str, ...]


def _read_command_line(args: Sequence[str], root: click.Command) -> _CommandLine:
    """Split ``args`` at the first word that is neither an option nor the value of one, the subcommand.

    The root's own parse may be what failed, or may not have run yet (an unknown command is found
    before it), so the context cannot say.
    """
    takes_value = {
        name for param in root.params if isinstance(param, click.Option) and not param.is_flag for name in param.opts
    }
    flags: set[str] = set()
    words = iter(args)
    for word in words:
        if not word.startswith("-") or word == "--":
            rest = () if word == "--" else (word, *words)
            return _CommandLine(flags=frozenset(flags), rest=rest)
        if word in _JSON_FLAGS:
            flags.add(word)
        elif word in takes_value:
            next(words, None)
    return _CommandLine(flags=frozenset(flags), rest=())


def _global_flags(args: Sequence[str], root: click.Command) -> frozenset[str]:
    """The JSON flags among the global options of ``args``."""
    return _read_command_line(args, root).flags


def _named_command(args: Sequence[str], root: click.Command) -> CommandName | None:
    """The command the command line names, a subcommand with its group; None when it names none."""
    rest = _read_command_line(args, root).rest
    known = {command.value: command for command in CommandName}
    return known.get(" ".join(rest[:2])) or known.get(" ".join(rest[:1]))


def _misplaced_flag(exc: click.ClickException) -> str | None:
    """The JSON flag a caller typed after the subcommand, where it does not parse."""
    name = exc.option_name if isinstance(exc, click.NoSuchOption) else None
    return name if name in _JSON_FLAGS else None


def _usage_error_mode(args: Sequence[str], exc: click.ClickException, root: click.Command) -> OutputMode:
    """The shape the command line asked for, although the error came before anything could store it."""
    flags = _global_flags(args, root) | {flag for flag in (_misplaced_flag(exc),) if flag}
    return OutputMode.of(as_json=_JSON_FLAG in flags, as_json_bare=_JSON_BARE_FLAG in flags)


def _report_click_error(exc: click.ClickException, *, mode: OutputMode, command: CommandName | None) -> None:
    """Show click's own text on stderr and, in machine mode, the error envelope on stdout."""
    exc.show()
    if mode.machine:
        envelope = ErrorEnvelope(command=command, error=type(exc).__name__, message=exc.format_message())
        write_envelope(envelope, mode=mode)


def _reader_left(exc: BaseException) -> bool:
    """Whether a command stopped because the reader of its output went away.

    click's own main and rich's broken-pipe handler both turn the error into ``SystemExit(1)`` raised
    while handling it, so the context is what tells that exit from a command's own. Only the direct
    context counts: a transport error raised from a socket's broken pipe is about the printer.

    Example:
        >>> _reader_left(BrokenPipeError(32, "Broken pipe"))
        True
        >>> _reader_left(SystemExit(1))
        False
    """
    cause = exc.__context__ if isinstance(exc, SystemExit) else exc
    return isinstance(cause, OSError) and is_gone_reader_error(cause)


def _discard_stream(stream: TextIO) -> None:
    """Point ``stream`` at the null device, so the interpreter's own flush at exit has nowhere to fail.

    The bytes that could not be written stay in the buffer, and that flush would otherwise print
    "Exception ignored" and exit 120 after every handler here has returned.
    """
    try:
        null = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        os.dup2(null, stream.fileno())
    except (OSError, ValueError):
        # A stream with no descriptor (a captured one) has no exit flush to protect.
        return
    finally:
        os.close(null)


class _Flushed(NamedTuple):
    """How the final flush of stdout and stderr went.

    Attributes:
        gone: A reader had gone.
        failure: The first failure that was not a gone reader (a full disk behind a redirect).
    """

    gone: bool
    failure: OSError | None


def _flush_streams() -> _Flushed:
    """Flush stdout and stderr, pointing a stream that fails at the null device.

    Output smaller than the stream's buffer has reached nothing until here: its command returned
    normally, so this is the one place every size passes through.
    """
    streams: tuple[TextIO | None, ...] = (sys.stdout, sys.stderr)
    gone = False
    failure: OSError | None = None
    for stream in streams:
        if stream is None:
            continue
        try:
            stream.flush()
        except OSError as exc:
            _discard_stream(stream)
            if is_gone_reader_error(exc):
                gone = True
            elif failure is None:
                failure = exc
    return _Flushed(gone=gone, failure=failure)


def _report_lost_output(failure: OSError) -> None:
    """Say on stderr that the answer did not get out; stderr itself may be what failed."""
    try:
        sys.stderr.write(f"Error: could not write the output: {error_text(failure)}\n")
        sys.stderr.flush()
    except OSError:
        # Nothing is left to tell; the exit code still says it.
        _discard_stream(sys.stderr)


def _invoke_cli(args: list[str], *, services_factory: Callable[[], AppServices]) -> int:
    """Run the command tree and report a usage error in the shape the command line asked for."""
    from .root import cli  # noqa: PLC0415 - deferred: lazy-loads the command tree so importing main stays cheap

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
        # The usage error is the outcome, whether or not its envelope still has a reader.
        try:
            _report_click_error(exc, mode=_usage_error_mode(args, exc, cli), command=_named_command(args, cli))
        except OSError as write_error:
            if not is_gone_reader_error(write_error):
                raise
        return exc.exit_code
    except click.exceptions.Abort as exc:
        # click spells both Ctrl-C and the end of a prompt's input as Abort; only the first is an
        # interrupt, and exit 1 would read as "refused, nothing was printed" with a job half out.
        if not isinstance(exc.__context__, KeyboardInterrupt):
            raise
        _report_interrupt(args, cli)
        return ExitCode.SIGNAL_INT


def _report_interrupt(args: Sequence[str], root: click.Command) -> None:
    """Say on stderr, and in the envelope a machine reader asked for, that the run was cut short.

    An envelope already begun stays the only one: stdout carries one, and a reader parsing it would
    fail on a second glued to it, or read a job that went out as one that did not. The exit code and
    stderr say the rest.
    """
    flags = _global_flags(args, root)
    mode = OutputMode.of(as_json=_JSON_FLAG in flags, as_json_bare=_JSON_BARE_FLAG in flags)
    command = _named_command(args, root)
    message = _INTERRUPTED + (_JOB_MAY_BE_OUT if command in _SENDING_COMMANDS else "")
    try:
        sys.stderr.write(f"Error: {message}\n")
        if mode.machine and not envelope_started():
            write_envelope(ErrorEnvelope(command=command, error="KeyboardInterrupt", message=message), mode=mode)
    except OSError as write_error:
        if not is_gone_reader_error(write_error):
            raise


def _run_cli(argv: Sequence[str] | None, *, services_factory: Callable[[], AppServices]) -> int:
    """Execute the CLI with exception handling.

    Args:
        argv: Optional sequence of CLI arguments. None uses sys.argv.
        services_factory: Factory function that returns AppServices. Passed via ctx.obj.

    Returns:
        Exit code produced by the command.
    """
    # Use Click's native invocation with obj parameter since lib_cli_exit_tools.run_cli
    # doesn't support passing obj. We replicate its behavior while adding obj support.
    args = list(argv) if argv is not None else sys.argv[1:]
    forget_started_envelope()

    try:
        return _invoke_cli(args, services_factory=services_factory)
    except BaseException as exc:
        if _reader_left(exc):
            return ExitCode.BROKEN_PIPE
        if isinstance(exc, OSError) and exc.filename is None:
            # run_command reports what a command's work raises, so a nameless one here comes from
            # writing its answer; the buffer still holds it, and the final flush must not fail again.
            _discard_stream(sys.stdout)
            _report_lost_output(exc)
            return Outcome.ERROR
        # Catch BaseException (not just Exception) to handle SystemExit, KeyboardInterrupt,
        # and all errors at the CLI boundary. This ensures consistent error formatting via
        # lib_cli_exit_tools regardless of exception type. Intentional, not a bug.
        tracebacks_enabled = bool(getattr(lib_cli_exit_tools.config, "traceback", False))
        apply_traceback_preferences(tracebacks_enabled)
        length_limit = TRACEBACK_VERBOSE_LIMIT if tracebacks_enabled else TRACEBACK_SUMMARY_LIMIT
        lib_cli_exit_tools.print_exception_message(trace_back=tracebacks_enabled, length_limit=length_limit)
        return lib_cli_exit_tools.get_system_exit_code(exc)


def _shut_down_logging() -> None:
    """Stop logging, from the main thread only so that logging for other threads goes on.

    A Ctrl-C here finds the command already done and its answer out, so it costs neither: the
    command's own exit code stands, and no traceback follows it.
    """
    if threading.current_thread() is not threading.main_thread():
        return
    try:
        if lib_log_rich.runtime.is_initialised():
            lib_log_rich.runtime.shutdown()
    except KeyboardInterrupt:
        return


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
        Exit code reported by the CLI run, or ``ExitCode.BROKEN_PIPE`` when the command succeeded or
        was cut off but the reader of its output had gone; a command's own failure keeps its code.

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
        with noting_interrupts():
            code = _run_cli(argv, services_factory=services_factory)
    finally:
        if restore_traceback:
            restore_traceback_state(previous_state)
        _shut_down_logging()
    # After the logging shutdown, which writes too. A failure outranks the broken pipe: a refusal
    # must not read as "the reader left" to a pipeline that reads the status.
    flushed = _flush_streams()
    if flushed.failure is not None:
        # A success whose answer was lost must not read as one; a command's own failure has said so
        # already, usually over the very same full disk, and one line says it.
        if code == ExitCode.SUCCESS:
            _report_lost_output(flushed.failure)
        return code or Outcome.ERROR
    return ExitCode.BROKEN_PIPE if flushed.gone and code == ExitCode.SUCCESS else code


__all__ = ["main"]
