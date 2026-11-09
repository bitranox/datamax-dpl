"""Print a label in one call, making every decision the ``print`` command makes.

What a job holds depends on the link it goes over, so the link is decided before the first render:
whether the model's image budget applies, and whether the printer's stored image names are read
at send or cannot be read at all. The ``print`` command and a library caller take this one route,
so they cannot come to different jobs for the same label.

Contents:
    * :class:`PrintRequest` - how a label is printed
    * :class:`PrintRefusedError` - the label has an error finding; nothing was sent
    * :func:`print_label` - render, refuse or send
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, TypedDict

from ..domain._invariants import require_update_fields
from ..domain.errors import LabelRejectedError
from ..domain.held_images import READ_AT_SEND, HeldImages
from ..domain.render_context import RenderOptions
from .upload_check import MIN_UPLOAD_CHECK_ROUNDS, CheckedJob, CheckedPrint, ResetCheck, UploadCheck, print_checked
from .use_cases import (
    DEFAULT_REPORT_LIMITS,
    ONE_WAY_LINK,
    BatchReport,
    ReportLimits,
    check_batch,
    error_findings,
    print_result,
    record_renders,
    render_label,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ..domain.label import Label
    from ..domain.render import RenderResult
    from .ports import JobSpool, PrinterEndpoint


class _BudgetUpdate(TypedDict):
    """The ``RenderOptions`` field :func:`print_label` decides for the link."""

    model_image_budget: bool


require_update_fields(_BudgetUpdate, RenderOptions)


class PrintRequest(NamedTuple):
    """How :func:`print_label` prints a label.

    Attributes:
        options: How to render. Its ``model_image_budget`` is decided here, from ``one_way`` and
            ``check.rounds``, whatever it is set to.
        endpoint: The printer, its timeout for each connect and write, and how to open it.
        check: How the upload is checked on a two-way link; a one-way link has no answer to check.
        one_way: Whether the channel cannot read the printer's answer, as
            :func:`~datamax_dpl.adapters.transport.printer_uri_is_one_way` says of its URI. It has
            no default: the job is rendered differently for each answer.
        records: One mapping of field name to value per label, or None for one label. Without
            ``spool`` they must be iterable again; see
            :func:`~datamax_dpl.application.use_cases.check_batch`.
        reset: How a reset report before a two-way job is asked for and acted on; None asks nothing.
        limits: How much of what grows with the records a batch's report keeps.
        spool: Where a batch keeps each record as it is checked, for the send to read back.
    """

    options: RenderOptions
    endpoint: PrinterEndpoint
    check: UploadCheck
    one_way: bool
    records: Iterable[Mapping[str, str]] | None = None
    reset: ResetCheck | None = None
    limits: ReportLimits = DEFAULT_REPORT_LIMITS
    spool: JobSpool | None = None


class PrintRefusedError(LabelRejectedError):
    """The label, or a record of the batch, has an error finding; the printer was not opened.

    Attributes:
        result: The job as it was checked, every finding included.
    """

    def __init__(self, result: RenderResult | BatchReport) -> None:
        """Keep the refused job and its error findings."""
        super().__init__("label has errors; nothing was sent", error_findings(result))
        self.result = result


def _options_for(request: PrintRequest) -> RenderOptions:
    """``request.options`` with the model's image budget set for the link.

    A two-way link leaves the budget out: its upload check reads whether the printer stored the
    images, which says more than a count of dot data can (user decision D63). With no round to
    read them back, the budget applies as on a one-way link. A configured ``image_memory_bytes``
    applies on every link.
    """
    unchecked = request.check.rounds == MIN_UPLOAD_CHECK_ROUNDS
    return request.options.model_copy(update=_BudgetUpdate(model_image_budget=request.one_way or unchecked))


def _names_at_send(*, one_way: bool) -> HeldImages:
    """What a job knows of the printer's stored image names before it goes."""
    return HeldImages(unread_because=ONE_WAY_LINK) if one_way else READ_AT_SEND


def _checked(label: Label, request: PrintRequest, options: RenderOptions) -> RenderResult | BatchReport:
    """Render ``label``, or go over its records, for the link ``request`` names."""
    if request.records is None:
        return render_label(label, options, held=_names_at_send(one_way=request.one_way))
    return check_batch(
        label,
        request.records,
        options,
        read_names_at_send=not request.one_way,
        limits=request.limits,
        spool=request.spool,
    )


def print_label(label: Label, request: PrintRequest) -> CheckedPrint[RenderResult | BatchReport]:
    """Render ``label`` for the link ``request`` names and send it, as the ``print`` command does.

    A one-way link gets the whole job at once and is never read. A two-way link gets it label by
    label through :func:`~datamax_dpl.application.upload_check.print_checked`, each label's image
    upload checked before its format goes out.

    Args:
        label: The label; with ``request.records``, the label holding the placeholders.
        request: The render options, the printer and the link, and how the job is checked.

    Returns:
        What was sent where, and the job as it went out with its findings.

    Raises:
        PrintRefusedError: The job has an error finding; the printer was not opened.
        UploadRefusedError: A label's images were not stored after every round; its format and
            everything after it were not sent. Its ``bytes_sent`` says how much went out.
        UnknownModelError: ``request.options.model`` names no profile.
        MissingFieldError: A record lacks a field a placeholder names.
        TransportError: The printer could not be reached or written.
        ValueError: ``request.check`` is not usable, or ``request.records`` can be read only once
            and no ``request.spool`` is given.
        pydantic.ValidationError: A filled value breaks a field limit.

    Examples:
        >>> from datamax_dpl import AnswerWait, Label, PrinterEndpoint, RenderOptions, Text, UploadCheck
        >>> from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
        >>> printer = RecordingPrinter(bidirectional=False)
        >>> printed = print_label(
        ...     Label(elements=[Text(x=1, y=1, text="HELLO")]),
        ...     PrintRequest(
        ...         options=RenderOptions(model="dmx-600"),
        ...         endpoint=PrinterEndpoint(
        ...             uri="file:job.dpl", timeout=5.0, open_printer=open_recording_printer(printer)
        ...         ),
        ...         check=UploadCheck(wait=AnswerWait(seconds=1)),
        ...         one_way=True,
        ...     ),
        ... )
        >>> printed.result.ok, b"HELLO" in b"".join(printer.sent)
        (True, True)
    """
    options = _options_for(request)
    result = _checked(label, request, options)
    if not result.ok:
        raise PrintRefusedError(result)
    if request.one_way:
        # The caller's word on the link, not the channel's: the job was rendered for a link no read comes back on.
        report = print_result(result, endpoint=request.endpoint)
        return CheckedPrint(uri=request.endpoint.uri, bytes_sent=report.bytes_sent, result=result)
    renders = result.renders() if isinstance(result, BatchReport) else record_renders(label, None, options)
    job = CheckedJob(renders=renders, check=request.check, reset=request.reset)
    return print_checked(result, job, endpoint=request.endpoint)


__all__ = ["PrintRefusedError", "PrintRequest", "print_label"]
