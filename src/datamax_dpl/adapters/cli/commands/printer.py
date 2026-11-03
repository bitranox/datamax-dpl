"""Printer commands: print, status, detect, memory, single commands and preview."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import rich_click as click
from pydantic import BaseModel, ConfigDict

from ....application.ports import PrinterEndpoint
from ....application.upload_check import (
    MIN_UPLOAD_CHECK_ROUNDS,
    CheckedJob,
    ResetCheck,
    UploadCheck,
    UploadRefusedError,
    print_checked,
)
from ....application.use_cases import (
    ONE_WAY_LINK,
    AnswerWait,
    BatchReport,
    BatchResult,
    PrinterCommand,
    detect_printer,
    print_result,
    query_status,
    read_memory_directory,
    record_renders,
    render_label,
    report_of,
    send_command,
)
from ....domain.enums import MemoryKind, StatusProblem
from ....domain.errors import AnswerNotEndedError, ConfigurationError, PreviewTooLongError
from ....domain.held_images import READ_AT_SEND, HeldImages
from ....domain.memory import NO_MODULES_MEANING, MemoryDirectory
from ....domain.placeholders import fill_record
from ....domain.preview import canvas_for, check_preview_length
from ....domain.profiles import get_profile
from ....domain.status import PrinterStatus
from ...config.sections import PREVIEW_SECTION, printer_section
from ...preview import pillow_available, preview_findings, render_preview_png
from ...transport.uri import is_one_way, unsupported_query
from ..constants import CLICK_CONTEXT_SETTINGS
from ..envelope import CommandName
from ..label_io import STDIN, LabelInputError, load_label, load_records
from ..payloads import FindingOut, SentTo, findings_text, refused_text, render_payload, went_out_text
from ..runner import CommandResult, run_command
from ..settings import (
    PrinterOverrides,
    PrinterSettings,
    input_bounds,
    preview_settings,
    render_options,
    require_uri,
)
from ..typed_click import argument, option
from .labels import JobRoute, check_loaded, load_batch
from .model_options import (
    command_overrides,
    loaded_config,
    parsed_printer_uri,
    printer_opener,
    printer_options_decorator,
    render_options_decorator,
    resolve_sending_settings,
    resolve_settings,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ....application.ports import JobSpool
    from ....domain.label import Label
    from ....domain.render import RenderResult
    from ....domain.render_context import RenderOptions
    from ..context import CLIContext

_DATA_HELP = "Data records (CSV, JSON array or JSON lines) filling {name} placeholders, one label each."


@click.command("print", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("label_source", metavar="LABEL")
@option("--data", default=None, help=_DATA_HELP)
@render_options_decorator
@printer_options_decorator
@click.pass_context
def cli_print(ctx: click.Context, label_source: str, data: str | None, **given: Any) -> None:
    """Render a label and send it to the printer; nothing is sent when a finding is an error."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        # Removed on the way out, whichever way that is: it holds the batch's jobs as checked.
        with cli_ctx.services.open_spool() as spool:
            return _print_body(cli_ctx, label_source, data, given=given, spool=spool)

    run_command(ctx, command=CommandName.PRINT, body=body)


def _print_body(
    cli_ctx: CLIContext, label_source: str, data: str | None, *, given: Mapping[str, Any], spool: JobSpool
) -> CommandResult:
    """Run ``print``: a batch's check keeps each record in ``spool``, and the send reads it back."""
    settings = resolve_sending_settings(cli_ctx, command_overrides(given))
    label = load_label(label_source, bounds=input_bounds(settings))
    records = None if data is None else load_batch(data, settings)
    link = _link(settings)
    options = _print_options(settings, link=link)
    result = check_loaded(label, records, options, settings, route=JobRoute(names=_names_at_send(link), spool=spool))
    uri = link.uri
    if not result.ok:
        return _render_refused(result, verdict="REFUSED: nothing sent", settings=settings)
    endpoint = PrinterEndpoint(uri=uri, timeout=settings.timeout, open_printer=printer_opener(cli_ctx, settings))
    if link.one_way:
        # A one-way link cannot read the directory: the job goes out at once, as it always did.
        report = print_result(result, endpoint=endpoint)
        sent, bytes_sent = result, report.bytes_sent
    else:
        job = CheckedJob(
            renders=result.renders() if isinstance(result, BatchReport) else record_renders(label, None, options),
            check=_upload_check(settings, cli_ctx),
            reset=_reset_check(settings),
        )
        try:
            checked = print_checked(result, job, endpoint=endpoint)
        except UploadRefusedError as exc:
            return _upload_refused(exc, uri=uri, settings=settings)
        sent, bytes_sent = checked.result, checked.bytes_sent
    payload = render_payload(sent, sent=SentTo(uri, bytes_sent), settings=settings)
    human = "\n".join(part for part in (f"sent {bytes_sent} bytes to {uri}", went_out_text(payload)) if part)
    return CommandResult(ok=True, payload=payload, human=human)


def _names_at_send(link: _Link) -> HeldImages:
    """What a job sent over ``link`` knows of the printer's stored image names before it goes.

    A two-way link reads them right before each label's downloads; a one-way link cannot, and a
    single label says why in its ``image_names_unread`` finding.
    """
    return HeldImages(unread_because=ONE_WAY_LINK) if link.one_way else READ_AT_SEND


def _render_refused(result: RenderResult | BatchReport, *, verdict: str, settings: PrinterSettings) -> CommandResult:
    """The result of a print whose label has an error finding: nothing went out, and why."""
    payload = render_payload(result, settings=settings)
    return CommandResult(ok=False, payload=payload, human=f"{verdict}\n{refused_text(payload)}")


def _upload_refused(exc: UploadRefusedError, *, uri: str, settings: PrinterSettings) -> CommandResult:
    """The result of a print whose upload check refused a label: its findings, what was sent."""
    # A command sends a report or one label; a held batch is the library's, and reports the same way.
    result = report_of(exc.result) if isinstance(exc.result, BatchResult) else exc.result
    payload = render_payload(result, sent=SentTo(uri, exc.bytes_sent), settings=settings)
    return CommandResult(ok=False, payload=payload, human=f"REFUSED: {exc}\n{refused_text(payload)}")


def _upload_check(settings: PrinterSettings, cli_ctx: CLIContext) -> UploadCheck:
    """How ``print`` checks each label's image upload: ``stored_names_wait`` per read, ending on ``directory_idle``."""
    return UploadCheck(
        wait=_stored_names_wait(settings, cli_ctx), rounds=settings.upload_check_rounds, idle=settings.directory_idle
    )


def _reset_check(settings: PrinterSettings) -> ResetCheck | None:
    """How ``print`` asks for a reset report before a job and acts on one; None (``reset_check`` off) asks nothing."""
    if not settings.reset_check:
        return None
    return ResetCheck(
        wait=settings.reset_check_wait,
        calibrate=settings.calibrate_after_reset,
        text=settings.calibration_label_text,
        off_because=f"{printer_section(settings.name).setting('calibrate_after_reset')} is off",
    )


class StatusPayload(BaseModel):
    """Result of ``status``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printer: str
    idle: bool
    problems: list[StatusProblem]
    flags: PrinterStatus
    reset_reported: bool


#: What the human view says for an answer carrying a reset report; the printer gives the report once.
_RESET_REPORTED_TEXT = (
    "reset_reported   yes: the printer reported a power-on or software reset, and this read took the report, "
    "so the next job's first label may print shifted"
)


@click.command("status", context_settings=CLICK_CONTEXT_SETTINGS)
@printer_options_decorator
@click.pass_context
def cli_status(ctx: click.Context, **given: Any) -> None:
    """Ask the printer for its status (needs a channel that can read: tcp or serial)."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, command_overrides(given))
        uri = _queryable_uri(settings)
        status = query_status(
            endpoint=PrinterEndpoint(uri=uri, timeout=settings.timeout, open_printer=printer_opener(cli_ctx, settings)),
            wait=_answer_wait(settings, cli_ctx),
        )
        payload = StatusPayload(
            printer=uri,
            idle=status.idle,
            problems=list(status.problems),
            flags=status,
            reset_reported=status.reset_reported,
        )
        lines = [f"{state.flag.value:16} {'yes' if state.is_set else 'no'}" for state in status.flags()]
        if status.reset_reported:
            lines.append(_RESET_REPORTED_TEXT)
        return CommandResult(ok=not status.problems, payload=payload, human="\n".join(lines))

    run_command(ctx, command=CommandName.STATUS, body=body)


@dataclass(frozen=True, slots=True)
class _Link:
    """The printer a ``print`` goes to: its URI, parsed once, and whether it can answer."""

    uri: str
    one_way: bool


def _link(settings: PrinterSettings) -> _Link:
    """The printer ``settings`` name and whether its channel is one-way.

    Raises:
        ConfigurationError: No printer is given, or its URI is malformed (:func:`parsed_printer_uri`).
    """
    return _Link(uri=require_uri(settings), one_way=is_one_way(parsed_printer_uri(settings)))


def _queryable_uri(settings: PrinterSettings) -> str:
    """The URI of a printer that can answer; a query of a file must not leave one behind.

    Raises:
        ConfigurationError: No printer is given, or its URI is malformed (:func:`parsed_printer_uri`).
        UnsupportedOperationError: The channel cannot read an answer; it was not opened.
    """
    parsed = parsed_printer_uri(settings)
    if is_one_way(parsed):
        raise unsupported_query(parsed)
    return require_uri(settings)


def _print_options(settings: PrinterSettings, *, link: _Link) -> RenderOptions:
    """The render options ``print`` renders with for the printer at ``link``.

    A two-way link leaves out the model's own image memory budget: its upload check reads whether
    the printer stored the images, which says more than a count of dot data can (user decision
    D63). With ``upload_check_rounds`` 0 no round reads them back, so the budget applies as on a
    one-way link. An ``image_memory_bytes`` configured applies on every link.

    Example:
        >>> settings = PrinterSettings(model="allegro-2")
        >>> _print_options(settings, link=_Link(uri="tcp://192.0.2.60:9100", one_way=False)).model_image_budget
        False
        >>> _print_options(settings, link=_Link(uri="file:job.dpl", one_way=True)).model_image_budget
        True
        >>> unchecked = PrinterSettings(model="allegro-2", upload_check_rounds=0)
        >>> _print_options(unchecked, link=_Link(uri="tcp://192.0.2.60:9100", one_way=False)).model_image_budget
        True
    """
    unchecked = settings.upload_check_rounds == MIN_UPLOAD_CHECK_ROUNDS
    return render_options(settings, model_image_budget=link.one_way or unchecked)


def wait_longer_hint(settings: PrinterSettings) -> str:
    """How to lengthen the wait ``status`` and ``memory`` keep trying for, named when it runs out.

    Example:
        >>> wait_longer_hint(PrinterSettings(name="lager"))
        'wait longer with [printers.lager] status_wait or --timeout'
    """
    return f"wait longer with {printer_section(settings.name).setting('status_wait')} or --timeout"


def _wait(settings: PrinterSettings, cli_ctx: CLIContext, *, seconds: float, wait_longer: str) -> AnswerWait:
    """A wait of ``seconds`` on the printer's retry interval and the services' clock and sleep."""
    return AnswerWait(
        seconds=seconds,
        retry_interval=settings.status_retry_interval,
        wait_longer=wait_longer,
        clock=cli_ctx.services.clock,
        sleep=cli_ctx.services.sleep,
    )


def _answer_wait(settings: PrinterSettings, cli_ctx: CLIContext) -> AnswerWait:
    """How long ``status`` and ``memory`` keep trying for the first answer (``--timeout`` already folded in)."""
    return _wait(settings, cli_ctx, seconds=settings.status_wait, wait_longer=wait_longer_hint(settings))


def stored_names_wait_longer_hint(settings: PrinterSettings) -> str:
    """How to lengthen the wait for the stored image names, named when it runs out.

    Example:
        >>> stored_names_wait_longer_hint(PrinterSettings())
        'wait longer with [printer] stored_names_wait'
    """
    return f"wait longer with {printer_section(settings.name).setting('stored_names_wait')}"


def _stored_names_wait(settings: PrinterSettings, cli_ctx: CLIContext) -> AnswerWait:
    """How long ``print`` keeps trying to read the stored image names: ``stored_names_wait``."""
    return _wait(
        settings, cli_ctx, seconds=settings.stored_names_wait, wait_longer=stored_names_wait_longer_hint(settings)
    )


@click.command("detect", context_settings=CLICK_CONTEXT_SETTINGS)
@printer_options_decorator
@click.pass_context
def cli_detect(ctx: click.Context, **given: Any) -> None:
    """Ask the printer what it answers and suggest which model profiles fit."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, command_overrides(given))
        report = detect_printer(
            endpoint=PrinterEndpoint(
                uri=_queryable_uri(settings), timeout=settings.timeout, open_printer=printer_opener(cli_ctx, settings)
            )
        )
        human = "\n".join([*report.detection.reasons, "fits: " + (", ".join(report.detection.candidates) or "none")])
        return CommandResult(ok=bool(report.detection.candidates), payload=report, human=human)

    run_command(ctx, command=CommandName.DETECT, body=body)


class MemoryPayload(BaseModel):
    """Result of ``memory``: one directory per kind asked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printer: str
    directories: list[MemoryDirectory]


class KindChoice(str, Enum):
    """Which stored data ``memory`` lists: one kind, or all three."""

    ALL = "all"
    FONT = MemoryKind.FONT.value
    GRAPHIC = MemoryKind.GRAPHIC.value
    FORMAT = MemoryKind.FORMAT.value

    @property
    def kinds(self) -> tuple[MemoryKind, ...]:
        """The kinds to ask for, in order: fonts, graphics and formats for ``all``."""
        return _KINDS[self]


_KINDS: Mapping[KindChoice, tuple[MemoryKind, ...]] = MappingProxyType(
    {
        KindChoice.ALL: (MemoryKind.FONT, MemoryKind.GRAPHIC, MemoryKind.FORMAT),
        KindChoice.FONT: (MemoryKind.FONT,),
        KindChoice.GRAPHIC: (MemoryKind.GRAPHIC,),
        KindChoice.FORMAT: (MemoryKind.FORMAT,),
    }
)


@click.command("memory", context_settings=CLICK_CONTEXT_SETTINGS)
@option(
    "--kind",
    type=click.Choice(sorted(choice.value for choice in KindChoice)),
    default=KindChoice.GRAPHIC.value,
    show_default=True,
    help="Which stored data to list: fonts, graphics, label formats, or all three.",
)
@option(
    "--idle",
    type=float,
    default=None,
    help="Seconds of silence that end the printer's answer [default: [printer] directory_idle, 1.0].",
)
@printer_options_decorator
@click.pass_context
def cli_memory(ctx: click.Context, kind: str, idle: float | None, **given: Any) -> None:
    """List the printer's memory modules (<STX>W): stored names and free bytes per module."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, command_overrides(given))
        uri = _queryable_uri(settings)
        silence = settings.directory_idle if idle is None else _idle_within_read_limit(idle, settings)
        try:
            directories = read_memory_directory(
                kinds=KindChoice(kind).kinds,
                endpoint=PrinterEndpoint(
                    uri=uri, timeout=settings.timeout, open_printer=printer_opener(cli_ctx, settings)
                ),
                idle=silence,
                wait=_answer_wait(settings, cli_ctx),
                read_limit=settings.directory_read_limit,
            )
        except AnswerNotEndedError as exc:
            raise _read_limit_advised(exc, settings) from None
        payload = MemoryPayload(printer=uri, directories=list(directories))
        return CommandResult(ok=True, payload=payload, human="\n".join(_directory_lines(directories)))

    run_command(ctx, command=CommandName.MEMORY, body=body)


def _idle_within_read_limit(idle: float, settings: PrinterSettings) -> float:
    """``--idle`` as given, refused when no answer ending on that much silence fits ``directory_read_limit``.

    Raises:
        ConfigurationError: ``idle`` is not shorter than the limit; the message names both.
    """
    limit = settings.directory_read_limit
    if idle < limit:
        return idle
    key = printer_section(settings.name).setting("directory_read_limit")
    msg = (
        f"--idle {idle:g} s is no shorter than {key} ({limit:g} s), the longest one answer may take up to "
        "that silence, so no answer could end within it"
    )
    raise ConfigurationError(msg)


def _read_limit_advised(error: AnswerNotEndedError, settings: PrinterSettings) -> AnswerNotEndedError:
    """``error`` advising the key that lengthens a memory answer: ``directory_read_limit`` of the section in use.

    Example:
        >>> str(_read_limit_advised(AnswerNotEndedError("the answer did not end"), PrinterSettings(name="lager")))
        'the answer did not end; raise [printers.lager] directory_read_limit'
    """
    advice = f"raise {printer_section(settings.name).setting('directory_read_limit')}"
    return AnswerNotEndedError(error.reason, advice=advice, unanswered=error.unanswered, attempts=error.attempts)


def _directory_lines(directories: tuple[MemoryDirectory, ...]) -> list[str]:
    """The human view: per kind, each module with its free bytes and the names it holds."""
    lines: list[str] = []
    if any(directory.reset_reported for directory in directories):
        lines.append(_RESET_REPORTED_TEXT)
    for directory in directories:
        lines.append(f"{directory.kind.value}:")
        if not directory.parsed:
            lines.append(f"  answer not understood: {directory.raw!r}")
        if directory.no_modules_available:
            lines.append(f"  printer reports no modules available ({NO_MODULES_MEANING})")
        for module in directory.modules:
            free = "unknown" if module.available_bytes is None else str(module.available_bytes)
            lines.append(f"  module {module.letter}: {free} bytes free")
            lines.extend(f"    {name}" for name in module.names)
    return lines


class SentPayload(BaseModel):
    """Result of a single printer command."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printer: str
    command: PrinterCommand
    bytes_sent: int


#: The envelope's command name for each single printer command; the CLI command takes the same spelling.
_SINGLE_COMMAND_NAMES: Mapping[PrinterCommand, CommandName] = MappingProxyType(
    {
        PrinterCommand.RESET: CommandName.RESET,
        PrinterCommand.CANCEL: CommandName.CANCEL,
        PrinterCommand.PAUSE: CommandName.PAUSE,
        PrinterCommand.FEED: CommandName.FEED,
        PrinterCommand.CUT: CommandName.CUT,
    }
)


def _single(command: PrinterCommand, help_text: str) -> click.Command:
    @click.command(command.value, context_settings=CLICK_CONTEXT_SETTINGS, help=help_text)
    @printer_options_decorator
    @click.pass_context
    def run(ctx: click.Context, **given: Any) -> None:
        def body(cli_ctx: CLIContext) -> CommandResult:
            settings = resolve_sending_settings(cli_ctx, command_overrides(given))
            uri = require_uri(settings)
            parsed_printer_uri(settings)
            report = send_command(
                command,
                endpoint=PrinterEndpoint(
                    uri=uri, timeout=settings.timeout, open_printer=printer_opener(cli_ctx, settings)
                ),
            )
            payload = SentPayload(printer=uri, command=command, bytes_sent=report.bytes_sent)
            return CommandResult(ok=True, payload=payload, human=f"{command.value} sent to {uri}")

        run_command(ctx, command=_SINGLE_COMMAND_NAMES[command], body=body)

    return run


cli_cut = _single(PrinterCommand.CUT, "Cut now (<STX>o); the printer needs its cutter enabled.")
cli_feed = _single(PrinterCommand.FEED, "Feed one label (<STX>F).")
cli_reset = _single(PrinterCommand.RESET, "Reset the printer (<SOH>#); clears its buffers.")
cli_cancel = _single(PrinterCommand.CANCEL, "Cancel the current job and pause (<SOH>C).")
cli_pause = _single(PrinterCommand.PAUSE, "Toggle pause (<SOH>B).")


class PreviewPayload(BaseModel):
    """Result of ``preview``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output: str
    width_dots: int
    height_dots: int
    dpi: float
    lettered: bool
    findings: list[FindingOut]


def _preview_limit(cli_ctx: CLIContext) -> float:
    """The longest side a preview may have, in millimetres: ``[preview] max_length_mm``."""
    return preview_settings(loaded_config(cli_ctx)).max_length_mm


def _refuse_long_preview(label: Label, options: RenderOptions, *, limit: float) -> None:
    """Refuse a label whose picture would pass ``limit``, before anything is drawn or printed.

    Raises:
        LabelInputError: The picture would be too long; the message names the key that allows it.
    """
    try:
        check_preview_length(canvas_for(label, get_profile(options.model), options=options), max_length_mm=limit)
    except PreviewTooLongError as exc:
        raise LabelInputError(f"{exc}; a longer one needs {PREVIEW_SECTION.setting('max_length_mm')}") from None


@click.command("preview", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("label_source", metavar="LABEL")
@option("-o", "--output", required=True, help="PNG file to write.")
@option("--data", default=None, help="Data records; the first record fills the placeholders.")
@option("--model", default=None, help="Printer model profile; decides the resolution and print width.")
@click.pass_context
def cli_preview(ctx: click.Context, label_source: str, output: str, data: str | None, model: str | None) -> None:
    """Draw the label at the printer's resolution as a PNG (text lettered or grey cells, barcodes hatched)."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        if output == STDIN:
            raise LabelInputError("preview --output - is not supported: a PNG goes to a file")
        settings = resolve_settings(cli_ctx, PrinterOverrides(model=model))
        bounds = input_bounds(settings)
        label = load_label(label_source, bounds=bounds)
        if data is not None:
            label = fill_record(label, next(iter(load_records(data, bounds=bounds))), number=0)
        profile = get_profile(settings.model)
        options = render_options(settings)
        limit = _preview_limit(cli_ctx)
        _refuse_long_preview(label, options, limit=limit)
        canvas = canvas_for(label, profile, options=options)
        # Rendered for how the job sends its fields: halved images and merged groups preview as they print.
        sent_as = render_label(label, options).sent_as
        picture = render_preview_png(label, profile, sent_as=sent_as, options=options, max_length_mm=limit)
        Path(output).write_bytes(picture)
        payload = PreviewPayload(
            output=output,
            width_dots=canvas.width,
            height_dots=canvas.height,
            dpi=profile.dpi,
            lettered=pillow_available(),
            findings=[FindingOut.of(finding, settings=settings) for finding in preview_findings(label)],
        )
        human = f"wrote {output} ({canvas.width}x{canvas.height} dots)"
        if payload.findings:
            human += "\n" + findings_text(payload.findings)
        return CommandResult(ok=True, payload=payload, human=human)

    run_command(ctx, command=CommandName.PREVIEW, body=body)


__all__ = [
    "KindChoice",
    "cli_cancel",
    "cli_cut",
    "cli_detect",
    "cli_feed",
    "cli_memory",
    "cli_pause",
    "cli_preview",
    "cli_print",
    "cli_reset",
    "cli_status",
]
