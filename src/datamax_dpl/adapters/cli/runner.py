"""Run a command body and turn its outcome into stdout, stderr and an exit code.

Every label and printer command hands :func:`run_command` a body returning a
:class:`CommandResult`. Expected failures become an error envelope (machine mode) or one stderr
line, with exit 1 for a refusal and 2 for anything that kept the command from running; the human
text of a success goes to stdout, the envelope replaces it in machine mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from ...domain.errors import CameraError, ConfigurationError, LabelRejectedError, TransportError
from ...domain.placeholders import MissingFieldError
from ...domain.profiles import UnknownModelError
from . import safe_console
from .context import CLIContext, get_cli_context
from .envelope import Envelope, Outcome, report_failure, write_envelope
from .label_io import LabelInputError, validation_lines

if TYPE_CHECKING:
    from collections.abc import Callable

    import rich_click as click

#: Failures that mean "the answer is no": the input was understood and refused.
REFUSALS: tuple[type[BaseException], ...] = (LabelRejectedError,)
#: Failures that kept the command from running.
ERRORS: tuple[type[BaseException], ...] = (
    LabelInputError,
    ConfigurationError,
    TransportError,
    CameraError,
    UnknownModelError,
    MissingFieldError,
    ValueError,
    OSError,
)


@dataclass
class CommandResult:
    """What a command body produced.

    Attributes:
        ok: False when the answer is no (exit 1), e.g. a label with error findings.
        payload: The envelope's ``data``.
        human: Text printed on stdout in human mode.
        skipped: Inputs the command passed over.
    """

    ok: bool
    payload: BaseModel
    human: str = ""
    skipped: list[str] = field(default_factory=list[str])


def _fail(exc: BaseException, *, command: str, cli_ctx: CLIContext) -> Outcome:
    if isinstance(exc, ValidationError):
        exc = LabelInputError(validation_lines(exc))
    report_failure(exc, command=command, mode=cli_ctx.mode)
    return Outcome.REFUSED if isinstance(exc, REFUSALS) else Outcome.ERROR


def run_command(ctx: click.Context, *, command: str, body: Callable[[CLIContext], CommandResult]) -> None:
    """Run ``body`` and exit with its outcome.

    Raises:
        click.exceptions.Exit: Always; carries the exit code.
    """
    cli_ctx = get_cli_context(ctx)
    try:
        result = body(cli_ctx)
    except Exception as exc:
        if not isinstance(exc, (*REFUSALS, *ERRORS, ValidationError)):
            raise
        ctx.exit(_fail(exc, command=command, cli_ctx=cli_ctx))
        return
    if cli_ctx.mode.machine:
        envelope = Envelope[BaseModel](ok=result.ok, command=command, data=result.payload, skipped=result.skipped)
        write_envelope(envelope, mode=cli_ctx.mode)
    elif result.human:
        safe_console.echo(result.human)
    ctx.exit(Outcome.OK if result.ok else Outcome.REFUSED)


__all__ = ["ERRORS", "REFUSALS", "CommandResult", "run_command"]
