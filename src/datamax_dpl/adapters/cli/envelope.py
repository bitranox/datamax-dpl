"""The machine-readable answer every label and printer command gives, and the one place that writes it.

``--json`` prints an indented envelope, ``--json-bare`` the same on one line; in both modes stdout
carries the envelope and nothing else, and narration goes to stderr. The shape is the one the
sibling bitranox CLIs use, ``{ok, command, data, skipped}``, so an LLM driving several of them
reads one format. Exit codes do not depend on the output mode: 0 done, 1 refused (the answer is
no, for example a label with an error finding), 2 could not run.
"""

from __future__ import annotations

import sys
from enum import IntEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny

from . import safe_console


class Outcome(IntEnum):
    """Process exit codes of the label and printer commands."""

    OK = 0
    REFUSED = 1
    ERROR = 2


class OutputMode(BaseModel):
    """Which shape this invocation answers in, decided once from ``--json`` and ``--json-bare``.

    Example:
        >>> OutputMode.of(as_json=False, as_json_bare=True).indent is None
        True
    """

    model_config = ConfigDict(frozen=True)

    machine: bool
    indent: int | None

    @classmethod
    def of(cls, *, as_json: bool, as_json_bare: bool) -> OutputMode:
        """The mode the two flags describe; bare implies machine."""
        return cls(machine=as_json or as_json_bare, indent=None if as_json_bare else 2)


HUMAN = OutputMode(machine=False, indent=2)

PayloadT = TypeVar("PayloadT", bound=BaseModel)


class Envelope(BaseModel, Generic[PayloadT]):
    """What a command reports when it ran; ``ok`` is false when the answer was no.

    Always parametrise it (``Envelope[RenderPayload](...)``) so the payload's own fields are
    serialised; ``skipped`` lists inputs the command passed over and is empty when none were.
    """

    ok: bool
    command: str
    data: SerializeAsAny[PayloadT]
    skipped: list[str] = Field(default_factory=list[str])


class ErrorEnvelope(BaseModel):
    """The answer when a command could not run or refused before producing a payload."""

    ok: bool = False
    command: str
    error: str
    message: str


def write_envelope(document: BaseModel, *, mode: OutputMode) -> None:
    """Write ``document`` to stdout, encode-safe for consoles that cannot show every character."""
    text = document.model_dump_json(indent=mode.indent)
    sys.stdout.write(safe_console.encode_safe(text, sys.stdout.encoding) + "\n")
    sys.stdout.flush()


def report_failure(exc: BaseException, *, command: str, mode: OutputMode) -> None:
    """Report a failure: an error envelope on stdout in machine mode, one line on stderr always."""
    sys.stderr.write(f"Error: {exc}\n")
    if mode.machine:
        write_envelope(ErrorEnvelope(command=command, error=type(exc).__name__, message=str(exc)), mode=mode)


__all__ = [
    "HUMAN",
    "Envelope",
    "ErrorEnvelope",
    "Outcome",
    "OutputMode",
    "report_failure",
    "write_envelope",
]
