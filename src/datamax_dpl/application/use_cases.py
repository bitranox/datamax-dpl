"""Use cases: render labels (one or a batch of data records), print, query and verify printers.

Every use case takes its collaborators (printer opener, camera, clock) as keyword arguments, so
the CLI wires production adapters and the tests wire in-memory ones.
"""

from __future__ import annotations

from contextlib import closing
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ..domain.errors import LabelRejectedError, PrinterTimeoutError
from ..domain.placeholders import fill_label
from ..domain.render import RenderResult, build_job
from ..domain.status import (
    CANCEL,
    CUT_NOW,
    FORM_FEED,
    RESET,
    STATUS_REQUEST,
    TOGGLE_PAUSE,
    PrinterStatus,
    parse_ascii_status,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ..domain.findings import Finding
    from ..domain.label import Label
    from ..domain.render_context import RenderOptions
    from .ports import Clock, OpenPrinter, Sleep


class BatchResult(BaseModel):
    """A label rendered once per data record.

    Attributes:
        data: All jobs back to back, in record order.
        results: One render result per record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    data: bytes
    results: tuple[RenderResult, ...]

    @property
    def ok(self) -> bool:
        """True when every record rendered without an error finding."""
        return all(result.ok for result in self.results)

    def findings(self) -> list[tuple[int, Finding]]:
        """Every finding with the index of the record it belongs to."""
        return [(i, f) for i, result in enumerate(self.results) for f in result.findings]


def render_label(label: Label, options: RenderOptions) -> RenderResult:
    """Render one label and report every finding (never raises for a finding)."""
    return build_job(label, options)


def render_batch(label: Label, records: Iterable[Mapping[str, str]], options: RenderOptions) -> BatchResult:
    """Render ``label`` once per record, filling its ``{name}`` placeholders.

    Raises:
        MissingFieldError: A record lacks a field a placeholder names.
        pydantic.ValidationError: A filled value breaks a field limit.
    """
    results = tuple(build_job(fill_label(label, record), options) for record in records)
    return BatchResult(data=b"".join(result.data for result in results), results=results)


class PrintReport(BaseModel):
    """What was sent where."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uri: str
    bytes_sent: int


def send_bytes(data: bytes, *, uri: str, timeout: float, open_printer: OpenPrinter) -> PrintReport:
    """Send ``data`` to the printer at ``uri``.

    Raises:
        TransportError: The printer could not be reached or written.
    """
    with closing(open_printer(uri, timeout=timeout)) as printer:
        printer.send(data)
    return PrintReport(uri=uri, bytes_sent=len(data))


def print_result(
    result: RenderResult | BatchResult, *, uri: str, timeout: float, open_printer: OpenPrinter
) -> PrintReport:
    """Send a rendered job, refusing one that has error findings.

    Raises:
        LabelRejectedError: The job has an error finding; nothing was sent.
        TransportError: The printer could not be reached or written.
    """
    if not result.ok:
        msg = "label has errors; nothing was sent"
        raise LabelRejectedError(msg)
    return send_bytes(result.data, uri=uri, timeout=timeout, open_printer=open_printer)


class PrinterCommand(str, Enum):
    """Single printer commands the CLI offers."""

    RESET = "reset"
    CANCEL = "cancel"
    PAUSE = "pause"
    FEED = "feed"
    CUT = "cut"


_COMMAND_BYTES: dict[PrinterCommand, bytes] = {
    PrinterCommand.RESET: RESET,
    PrinterCommand.CANCEL: CANCEL,
    PrinterCommand.PAUSE: TOGGLE_PAUSE,
    PrinterCommand.FEED: FORM_FEED,
    PrinterCommand.CUT: CUT_NOW,
}


def send_command(command: PrinterCommand, *, uri: str, timeout: float, open_printer: OpenPrinter) -> PrintReport:
    """Send one immediate or system command (reset, cancel, pause, feed, cut)."""
    return send_bytes(_COMMAND_BYTES[command], uri=uri, timeout=timeout, open_printer=open_printer)


def query_status(*, uri: str, timeout: float, open_printer: OpenPrinter) -> PrinterStatus:
    """Ask the printer for its status (``<SOH>A``).

    Raises:
        UnsupportedOperationError: The channel cannot read answers (file, stdout, device file).
        PrinterTimeoutError: The printer did not answer in time.
        PrinterProtocolError: The answer was not a status string.
    """
    with closing(open_printer(uri, timeout=timeout)) as printer:
        return parse_ascii_status(printer.query(STATUS_REQUEST, timeout=timeout))


def wait_until_idle(
    *, uri: str, timeout: float, poll_seconds: float, open_printer: OpenPrinter, sleep: Sleep, clock: Clock
) -> PrinterStatus:
    """Poll the status until the printer is idle, or raise when ``timeout`` passes.

    Raises:
        PrinterTimeoutError: Still busy at the deadline.
    """
    deadline = clock() + timeout
    with closing(open_printer(uri, timeout=timeout)) as printer:
        while True:
            status = parse_ascii_status(printer.query(STATUS_REQUEST, timeout=timeout))
            if status.idle or status.problems:
                return status
            if clock() >= deadline:
                msg = f"printer still busy after {timeout:g} s"
                raise PrinterTimeoutError(msg)
            sleep(poll_seconds)


__all__ = [
    "BatchResult",
    "PrintReport",
    "PrinterCommand",
    "print_result",
    "query_status",
    "render_batch",
    "render_label",
    "send_bytes",
    "send_command",
    "wait_until_idle",
]
