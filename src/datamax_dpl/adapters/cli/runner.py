"""Run a command body and turn its outcome into stdout, stderr and an exit code.

Every label and printer command hands :func:`run_command` a body returning a
:class:`CommandResult`. Any failure becomes an error envelope (machine mode) and one stderr line,
with exit 1 for a refusal and 2 for anything that kept the command from running, a failure nobody
foresaw included: a caller reads exit 1 as "the answer is no" and parses stdout whatever happened.
The human text of a success goes to stdout, or to stderr when the job itself went there; the envelope
replaces it in machine mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import click
import lib_cli_exit_tools
from pydantic import BaseModel, ValidationError

from ...domain.errors import CameraError, ConfigurationError, LabelRejectedError, TransportError
from ...domain.placeholders import MissingFieldError
from ...domain.profiles import UnknownModelError
from . import safe_console
from .constants import TRACEBACK_VERBOSE_LIMIT
from .context import CLIContext, get_cli_context
from .envelope import CommandName, Envelope, Outcome, report_failure, write_envelope
from .interrupt import raise_if_interrupted
from .label_io import LabelInputError, validation_lines

if TYPE_CHECKING:
    from collections.abc import Callable

    import rich_click

#: Failures that mean "the answer is no": the input was understood and refused.
REFUSALS: tuple[type[BaseException], ...] = (LabelRejectedError,)
#: Failures foreseen as keeping the command from running: their message says all there is, so
#: ``--traceback`` adds nothing to them. Anything else exits the same way, with a traceback on request.
ERRORS: tuple[type[BaseException], ...] = (
    ConfigurationError,
    TransportError,
    CameraError,
    UnknownModelError,
    MissingFieldError,
    ValueError,
    OSError,
)
#: How click itself ends or refuses a command: not failures of the command's work.
_CLICK_CONTROL: tuple[type[BaseException], ...] = (
    click.ClickException,
    click.exceptions.Exit,
    click.exceptions.Abort,
)


@dataclass
class CommandResult:
    """What a command body produced.

    Attributes:
        ok: False when the answer is no (exit 1), e.g. a label with error findings.
        payload: The envelope's ``data``.
        human: Text printed on stdout in human mode.
        skipped: Inputs the command passed over.
        notes: Text printed on stderr in human mode, for a command whose job owns stdout.
    """

    ok: bool
    payload: BaseModel
    human: str = ""
    skipped: list[str] = field(default_factory=list[str])
    notes: str = ""


def _fail(exc: BaseException, *, command: CommandName, cli_ctx: CLIContext) -> Outcome:
    if isinstance(exc, ValidationError):
        exc = LabelInputError(validation_lines(exc))
    report_failure(exc, command=command, mode=cli_ctx.mode)
    if cli_ctx.traceback and not isinstance(exc, (*REFUSALS, *ERRORS)):
        lib_cli_exit_tools.print_exception_message(trace_back=True, length_limit=TRACEBACK_VERBOSE_LIMIT)
    return Outcome.REFUSED if isinstance(exc, REFUSALS) else Outcome.ERROR


def run_command(ctx: rich_click.Context, *, command: CommandName, body: Callable[[CLIContext], CommandResult]) -> None:
    """Run ``body`` and exit with its outcome.

    Raises:
        click.exceptions.Exit: Always; carries the exit code.
    """
    cli_ctx = get_cli_context(ctx)
    try:
        result = body(cli_ctx)
        # The last record's fill comes after the pass over the records has ended, and a single
        # label has no pass: what was lost there still must not read as a run that finished.
        raise_if_interrupted()
    except _CLICK_CONTROL:
        raise
    except Exception as exc:
        ctx.exit(_fail(exc, command=command, cli_ctx=cli_ctx))
        return
    if cli_ctx.mode.machine:
        envelope = Envelope[BaseModel](ok=result.ok, command=command, data=result.payload, skipped=result.skipped)
        write_envelope(envelope, mode=cli_ctx.mode)
    elif result.human:
        safe_console.echo(result.human, err=cli_ctx.job_on_stdout)
    if result.notes and not cli_ctx.mode.machine:
        safe_console.echo(result.notes, err=True)
    ctx.exit(Outcome.OK if result.ok else Outcome.REFUSED)


__all__ = ["ERRORS", "REFUSALS", "CommandResult", "run_command"]
