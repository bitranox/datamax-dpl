"""The machine-readable answer every command gives, and the one place that writes it.

``--json`` prints an indented envelope, ``--json-bare`` the same on one line; in both modes stdout
carries the envelope and nothing else, and narration goes to stderr. The shape is the one the
sibling bitranox CLIs use, ``{ok, command, data, skipped}``, so an LLM driving several of them
reads one format. Exit codes do not depend on the output mode: 0 done, 1 refused (the answer is
no, for example a label with an error finding), 2 could not run; ``info`` and the config commands
keep their own codes (see :mod:`.exit_codes`).
"""

from __future__ import annotations

import json
import re
import sys
from enum import Enum, IntEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny

from ...domain.printable import one_line
from ..error_text import error_text
from . import safe_console


class Outcome(IntEnum):
    """Process exit codes of the label and printer commands."""

    OK = 0
    REFUSED = 1
    ERROR = 2


class CommandName(str, Enum):
    """The command an envelope answers for, as its ``command`` field spells it.

    A subcommand carries its group: ``calibrate print``.

    Example:
        >>> CommandName.CALIBRATE_PRINT.value
        'calibrate print'
    """

    CAPABILITIES = "capabilities"
    CALIBRATE = "calibrate"
    CALIBRATE_PRINT = "calibrate print"
    CALIBRATE_SET = "calibrate set"
    CANCEL = "cancel"
    CAPTURE = "capture"
    CONFIG = "config"
    CONFIG_DEPLOY = "config-deploy"
    CONFIG_GENERATE_EXAMPLES = "config-generate-examples"
    CUT = "cut"
    DETECT = "detect"
    EXPLAIN = "explain"
    FEED = "feed"
    INFO = "info"
    MEMORY = "memory"
    MODELS = "models"
    PAUSE = "pause"
    PREVIEW = "preview"
    PRINT = "print"
    RENDER = "render"
    RESET = "reset"
    SCHEMA = "schema"
    STATUS = "status"
    VALIDATE = "validate"
    VERIFY = "verify"


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
    command: CommandName
    data: SerializeAsAny[PayloadT]
    skipped: list[str] = Field(default_factory=list[str])


class ErrorEnvelope(BaseModel):
    """The answer when a command could not run or refused before producing a payload.

    ``command`` is None when the command line named none: no command, or an unknown one.
    """

    ok: bool = False
    command: CommandName | None
    error: str
    message: str


_LONE_SURROGATE = re.compile(r"[\ud800-\udfff]")


class _Started:
    """Whether this run has begun writing its envelope: stdout carries one, so no second may follow."""

    envelope = False


def forget_started_envelope() -> None:
    """Start a new run: nothing of its answer has been written."""
    _Started.envelope = False


def envelope_started() -> bool:
    """Whether this run has begun writing an envelope, whole by now or cut off."""
    return _Started.envelope


#: Characters handed to stdout at a time. Each layer below copies what it is handed (the encode
#: check, a text stream's newline translation, its encoder), so an envelope carrying a whole batch's
#: job written at once cost several times its size on top of the text, one copy more on Windows.
_ENVELOPE_PIECE_CHARS = 64 * 1024


def write_envelope(document: BaseModel, *, mode: OutputMode) -> None:
    """Write ``document`` to stdout, encode-safe for consoles that cannot show every character.

    Dumped through plain data rather than ``model_dump_json``, which raises on a lone surrogate:
    that is how a file name that is not UTF-8 reaches ``argv`` on POSIX, so a path in a payload would
    turn a job already sent into a crash with no envelope. A surrogate is written as its JSON escape
    and every other character stays as it is.
    """
    separators = (",", ": ") if mode.indent is not None else (",", ":")
    dumped = json.dumps(document.model_dump(mode="json"), indent=mode.indent, separators=separators, ensure_ascii=False)
    text = _LONE_SURROGATE.sub(lambda found: f"\\u{ord(found[0]):04x}", dumped)
    _Started.envelope = True
    for start in range(0, len(text), _ENVELOPE_PIECE_CHARS):
        sys.stdout.write(safe_console.encode_safe(text[start : start + _ENVELOPE_PIECE_CHARS], sys.stdout.encoding))
    sys.stdout.write("\n")
    sys.stdout.flush()


def write_error_envelope(exc: BaseException, *, command: CommandName | None, mode: OutputMode) -> None:
    """Write the error envelope of ``exc`` in machine mode; human mode writes nothing."""
    if mode.machine:
        write_envelope(ErrorEnvelope(command=command, error=type(exc).__name__, message=error_text(exc)), mode=mode)


def report_failure(exc: BaseException, *, command: CommandName, mode: OutputMode) -> None:
    """Report a failure: an error envelope on stdout in machine mode, one line on stderr always."""
    sys.stderr.write(safe_console.encode_safe(f"Error: {one_line(error_text(exc))}\n", sys.stderr.encoding))
    write_error_envelope(exc, command=command, mode=mode)


__all__ = [
    "HUMAN",
    "CommandName",
    "Envelope",
    "ErrorEnvelope",
    "Outcome",
    "OutputMode",
    "envelope_started",
    "error_text",
    "forget_started_envelope",
    "report_failure",
    "write_envelope",
    "write_error_envelope",
]
