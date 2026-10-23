"""Use cases: render labels (one or a batch of data records), print, query and verify printers.

Every use case takes its collaborators (printer opener, camera, clock) as keyword arguments, so
the CLI wires production adapters and the tests wire in-memory ones.
"""

from __future__ import annotations

from contextlib import closing
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ..domain.batch import cut_across_records
from ..domain.detect import Detection, Evidence, suggest_profiles
from ..domain.enums import Severity
from ..domain.errors import LabelRejectedError, PrinterProtocolError, PrinterTimeoutError
from ..domain.findings import Finding
from ..domain.label import Cut
from ..domain.placeholders import fill_label
from ..domain.render import RenderResult, build_job
from ..domain.status import (
    CANCEL,
    CUT_NOW,
    FORM_FEED,
    MEMORY_REQUEST,
    RESET,
    SERIAL_TEST_REQUEST,
    STATUS_REQUEST,
    TOGGLE_PAUSE,
    VERSION_REQUEST,
    PrinterStatus,
    parse_ascii_status,
    parse_memory_report,
    parse_version,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ..domain.label import Label
    from ..domain.render_context import RenderOptions
    from .ports import OpenPrinter, PrinterTransport


class BatchResult(BaseModel):
    """A label rendered once per data record.

    Attributes:
        data: All jobs back to back, in record order, with any batch cut commands between them.
        results: One render result per record.
        batch_findings: Findings about the batch as a whole (cutting across records).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    data: bytes
    results: tuple[RenderResult, ...]
    batch_findings: tuple[Finding, ...] = ()

    @property
    def ok(self) -> bool:
        """True when no record and no batch finding is an error."""
        batch_ok = all(f.severity is not Severity.ERROR for f in self.batch_findings)
        return batch_ok and all(result.ok for result in self.results)

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
    cut = label.cut
    if cut is None or cut.every == 1 or label.quantity > 1:
        results = tuple(build_job(fill_label(label, record), options) for record in records)
        return BatchResult(data=b"".join(result.data for result in results), results=results)
    per_record = label.model_copy(update={"cut": Cut(every=1, at_end=False)})
    results = tuple(build_job(fill_label(per_record, record), options) for record in records)
    data, findings = cut_across_records([r.data for r in results], cut=cut, options=options)
    return BatchResult(data=data, results=results, batch_findings=findings)


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


#: Lines in the ``<STX>KQ`` memory report (docs/dpl-reference.md section 8).
MEMORY_REPORT_LINES = 8


class DetectReport(BaseModel):
    """Evidence collected from the printer and the profiles that fit it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: Evidence
    detection: Detection


def _ask(printer: PrinterTransport, request: bytes, *, timeout: float, lines: int = 1) -> bytes | None:
    try:
        return printer.query(request, timeout=timeout, lines=lines)
    except (PrinterTimeoutError, PrinterProtocolError):
        return None


def detect_printer(*, uri: str, timeout: float, open_printer: OpenPrinter) -> DetectReport:
    """Ask the printer what it answers and suggest the profiles that fit.

    Raises:
        UnsupportedOperationError: The channel cannot read answers.
        TransportError: The printer could not be reached.
    """
    with closing(open_printer(uri, timeout=timeout)) as printer:
        serial = _ask(printer, SERIAL_TEST_REQUEST, timeout=timeout)
        version = _ask(printer, VERSION_REQUEST, timeout=timeout)
        memory = _ask(printer, MEMORY_REQUEST, timeout=timeout, lines=MEMORY_REPORT_LINES)
    evidence = Evidence(
        serial_test=None if serial is None else serial.strip().startswith(b"Y"),
        version=None if version is None else parse_version(version),
        memory=None if memory is None else parse_memory_report(memory),
    )
    return DetectReport(evidence=evidence, detection=suggest_profiles(evidence))


__all__ = [
    "BatchResult",
    "DetectReport",
    "PrintReport",
    "PrinterCommand",
    "detect_printer",
    "print_result",
    "query_status",
    "render_batch",
    "render_label",
    "send_bytes",
    "send_command",
]
