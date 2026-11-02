"""Printer commands: print, status, detect, memory, single commands, preview, capture and verify."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import rich_click as click
from pydantic import BaseModel, ConfigDict

from ....application.upload_check import CheckedJob, ResetCheck, UploadCheck, UploadRefusedError, print_checked
from ....application.use_cases import (
    DIRECTORY_IDLE_SECONDS,
    AnswerWait,
    BatchReport,
    PrinterCommand,
    detect_printer,
    print_result,
    query_status,
    read_memory_directory,
    record_renders,
    render_label,
    render_with_stored_names,
    send_command,
    stored_image_names,
)
from ....application.verify import IdleWait, verify_print
from ....domain.enums import FindingCode, ImageModule, MemoryKind, Severity, StatusProblem
from ....domain.errors import CameraError, PreviewTooLongError
from ....domain.held_images import HeldImages
from ....domain.memory import NO_MODULES_MEANING, MemoryDirectory
from ....domain.placeholders import fill_label
from ....domain.preview import canvas_for, check_preview_length
from ....domain.profiles import get_profile
from ....domain.render import RenderResult
from ....domain.status import PrinterStatus
from ...camera import require_camera
from ...config.sections import CAMERA_SECTION, PREVIEW_SECTION, printer_section
from ...preview import pillow_available, preview_findings, render_preview_png
from ...transport.uri import is_one_way, unsupported_query
from ..constants import CLICK_CONTEXT_SETTINGS
from ..envelope import CommandName
from ..label_io import STDIN, LabelInputError, load_label, load_records
from ..payloads import FindingOut, RenderPayload, SentTo, findings_text, payload_findings_text, render_payload
from ..runner import CommandResult, run_command
from ..settings import (
    PrinterOverrides,
    PrinterSettings,
    camera_settings,
    preview_settings,
    render_options,
    require_uri,
)
from ..typed_click import argument, option
from .labels import check_loaded, load_batch
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

    from ....application.use_cases import BatchResult, RenderedJob
    from ....domain.camera import CameraSettings
    from ....domain.label import Label
    from ....domain.render_context import RenderOptions
    from ..context import CLIContext
    from ..label_io import Records

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
        settings = resolve_sending_settings(cli_ctx, command_overrides(given))
        label = load_label(label_source)
        records = None if data is None else load_batch(data, settings)
        link = _link(settings)
        options = _print_options(settings, link=link)
        rendered = _render_to_print(label, records, options, settings, cli_ctx, link=link)
        result = rendered.result
        uri = link.uri
        if not result.ok:
            return _render_refused(result, verdict="REFUSED: nothing sent", settings=settings)
        open_printer = printer_opener(cli_ctx, settings)
        if link.one_way:
            # A one-way link cannot read the directory: the job goes out at once, as it always did.
            report = print_result(result, uri=uri, timeout=settings.timeout, open_printer=open_printer)
            sent, bytes_sent = result, report.bytes_sent
        else:
            job = CheckedJob(
                renders=result.renders() if isinstance(result, BatchReport) else record_renders(label, None, options),
                check=_upload_check(settings, cli_ctx),
                held=rendered.held or HeldImages(),
                reset=_reset_check(settings),
            )
            try:
                checked = print_checked(result, job, uri=uri, timeout=settings.timeout, open_printer=open_printer)
            except UploadRefusedError as exc:
                return _upload_refused(exc, uri=uri, settings=settings)
            sent, bytes_sent = checked.result, checked.bytes_sent
        payload = render_payload(sent, include_dpl=False, sent=SentTo(uri, bytes_sent), settings=settings)
        human = "\n".join([f"sent {bytes_sent} bytes to {uri}", *_reported_lines(payload)])
        return CommandResult(ok=True, payload=payload, human=human)

    run_command(ctx, command=CommandName.PRINT, body=body)


def _render_refused(
    result: RenderResult | BatchResult | BatchReport, *, verdict: str, settings: PrinterSettings
) -> CommandResult:
    """The result of a print or verify whose label has an error finding: nothing went out, and why."""
    payload = render_payload(result, include_dpl=False, settings=settings)
    errors = [finding for finding in payload.findings if finding.severity is Severity.ERROR]
    return CommandResult(ok=False, payload=payload, human=f"{verdict}\n{payload_findings_text(payload, errors)}")


def _upload_refused(exc: UploadRefusedError, *, uri: str, settings: PrinterSettings) -> CommandResult:
    """The result of a print or verify whose upload check refused a label: its findings, what was sent."""
    payload = render_payload(exc.result, include_dpl=False, sent=SentTo(uri, exc.bytes_sent), settings=settings)
    errors = [finding for finding in payload.findings if finding.severity is Severity.ERROR]
    return CommandResult(ok=False, payload=payload, human=f"REFUSED: {exc}\n{payload_findings_text(payload, errors)}")


def _upload_check(settings: PrinterSettings, cli_ctx: CLIContext) -> UploadCheck:
    """How ``print`` and ``verify`` check each label's image upload: ``stored_names_wait`` per read."""
    return UploadCheck(wait=_stored_names_wait(settings, cli_ctx), rounds=settings.upload_check_rounds)


def _reset_check(settings: PrinterSettings) -> ResetCheck:
    """How ``print`` and ``verify`` ask for a reset report before a job, and what they do on one."""
    return ResetCheck(
        wait=settings.reset_check_wait,
        calibrate=settings.calibrate_after_reset,
        text=settings.calibration_label_text,
    )


def _reported_lines(payload: RenderPayload) -> list[str]:
    """The human view of a job that went out: every warning, and what a reset changed even as an info.

    A fallback the job took is what a reader would otherwise find only on the paper, and an extra
    label or a shift to expect after a reset is one too.
    """
    text = payload_findings_text(payload, [f for f in payload.findings if _worth_telling(f)])
    return [text] if text else []


def _worth_telling(finding: FindingOut) -> bool:
    reset = (FindingCode.PRINTER_RESET_CALIBRATED, FindingCode.PRINTER_RESET_NOT_CALIBRATED)
    return finding.severity is not Severity.INFO or finding.code in reset


def _told_lines(told: list[FindingOut]) -> list[str]:
    return [findings_text(told)] if told else []


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
            uri=uri,
            timeout=settings.timeout,
            open_printer=printer_opener(cli_ctx, settings),
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
    """The printer a ``print`` or ``verify`` goes to: its URI, parsed once, and whether it can answer."""

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
    """The render options ``print`` and ``verify`` render with for the printer at ``link``.

    A two-way link leaves out the model's own image memory budget: its upload check reads whether
    the printer stored the images, which says more than a count of dot data can (user decision
    D63). An ``image_memory_bytes`` configured applies on every link.

    Example:
        >>> settings = PrinterSettings(model="allegro-2")
        >>> _print_options(settings, link=_Link(uri="tcp://192.0.2.60:9100", one_way=False)).model_image_budget
        False
        >>> _print_options(settings, link=_Link(uri="file:job.dpl", one_way=True)).model_image_budget
        True
    """
    return render_options(settings, model_image_budget=link.one_way)


def _render_to_print(
    label: Label,
    records: Records | None,
    options: RenderOptions,
    settings: PrinterSettings,
    cli_ctx: CLIContext,
    *,
    link: _Link,
) -> RenderedJob[RenderResult | BatchReport]:
    """Render a job to be printed, reading the printer's stored image names where no clear precedes it.

    The records are loaded once by the caller and every pass goes over them: ``--data -`` is stdin,
    which cannot be read a second time. A batch is gone over whole here, so that a bad record refuses
    it before any byte goes to the printer, and comes back as a report holding no job. Returns the
    job and the names it was rendered with.
    """

    def read(module: ImageModule) -> HeldImages:
        return stored_image_names(
            module=module,
            uri=link.uri,
            timeout=settings.timeout,
            open_printer=printer_opener(cli_ctx, settings),
            wait=_stored_names_wait(settings, cli_ctx),
            one_way=link.one_way,
        )

    return render_with_stored_names(lambda held: check_loaded(label, records, options, settings, held=held), read=read)


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
    """How long ``print`` and ``verify`` keep trying to read the stored image names: ``stored_names_wait``."""
    return _wait(
        settings, cli_ctx, seconds=settings.stored_names_wait, wait_longer=stored_names_wait_longer_hint(settings)
    )


def _idle_wait(settings: PrinterSettings, cli_ctx: CLIContext) -> IdleWait:
    """How long ``verify`` waits for the printer to finish: the wait ``status`` uses, polled while busy."""
    return IdleWait(
        answer=_answer_wait(settings, cli_ctx),
        poll_interval=settings.verify_poll_interval,
        one_way_settle=settings.one_way_settle,
    )


@click.command("detect", context_settings=CLICK_CONTEXT_SETTINGS)
@printer_options_decorator
@click.pass_context
def cli_detect(ctx: click.Context, **given: Any) -> None:
    """Ask the printer what it answers and suggest which model profiles fit."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, command_overrides(given))
        report = detect_printer(
            uri=_queryable_uri(settings), timeout=settings.timeout, open_printer=printer_opener(cli_ctx, settings)
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
    default=DIRECTORY_IDLE_SECONDS,
    show_default=True,
    help="Seconds of silence that end the printer's answer.",
)
@printer_options_decorator
@click.pass_context
def cli_memory(ctx: click.Context, kind: str, idle: float, **given: Any) -> None:
    """List the printer's memory modules (<STX>W): stored names and free bytes per module."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, command_overrides(given))
        uri = _queryable_uri(settings)
        directories = read_memory_directory(
            kinds=KindChoice(kind).kinds,
            uri=uri,
            timeout=settings.timeout,
            idle=idle,
            open_printer=printer_opener(cli_ctx, settings),
            wait=_answer_wait(settings, cli_ctx),
        )
        payload = MemoryPayload(printer=uri, directories=list(directories))
        return CommandResult(ok=True, payload=payload, human="\n".join(_directory_lines(directories)))

    run_command(ctx, command=CommandName.MEMORY, body=body)


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
                command, uri=uri, timeout=settings.timeout, open_printer=printer_opener(cli_ctx, settings)
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
        label = load_label(label_source)
        if data is not None:
            label = fill_label(label, next(iter(load_records(data))))
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
            findings=[FindingOut.of(finding) for finding in preview_findings(label)],
        )
        human = f"wrote {output} ({canvas.width}x{canvas.height} dots)"
        if payload.findings:
            human += "\n" + findings_text(payload.findings)
        return CommandResult(ok=True, payload=payload, human=human)

    run_command(ctx, command=CommandName.PREVIEW, body=body)


@click.command(
    "capture",
    context_settings=CLICK_CONTEXT_SETTINGS,
    # help=, not the docstring, so the section is rendered rather than spelled by hand.
    help=f"Take one photo with the configured camera ({CAMERA_SECTION.header} section).",
)
@option("--output-dir", default=".", help="Folder the photo is written to.")
@click.pass_context
def cli_capture(ctx: click.Context, output_dir: str) -> None:
    """Take one photo with the configured camera."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        camera = camera_settings(loaded_config(cli_ctx))
        photo = cli_ctx.services.capture_image(camera, output_dir=Path(output_dir))
        return CommandResult(ok=True, payload=photo, human=f"photo {photo.path}")

    run_command(ctx, command=CommandName.CAPTURE, body=body)


class VerifyPayload(BaseModel):
    """Result of ``verify``: where the photo and the expected picture are."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printer: str
    bytes_sent: int
    status: PrinterStatus | None
    photo: str
    expected: str
    findings: list[FindingOut]


def _printable_camera(camera: CameraSettings) -> CameraSettings:
    """Return settings that name a camera, refusing before any label goes out.

    Raises:
        CameraError: They name none; no label was printed.
    """
    try:
        require_camera(camera)
    except CameraError as exc:
        raise CameraError(f"{exc}; nothing was printed") from exc
    return camera


@click.command("verify", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("label_source", metavar="LABEL")
@option("--output-dir", default=".", help="Folder for the photo and the expected preview.")
@render_options_decorator
@printer_options_decorator
@click.pass_context
def cli_verify(ctx: click.Context, label_source: str, output_dir: str, **given: Any) -> None:
    """Print a label, wait until the printer is done, photograph it, and write the expected preview beside it."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_sending_settings(cli_ctx, command_overrides(given))
        # Loaded once and rendered from that copy: LABEL '-' is stdin, which cannot be read twice.
        label = load_label(label_source)
        link = _link(settings)
        options = _print_options(settings, link=link)
        # The expected picture is drawn after the print, so a label too long for it stops here.
        _refuse_long_preview(label, options, limit=_preview_limit(cli_ctx))
        rendered = _render_to_print(label, None, options, settings, cli_ctx, link=link)
        result = rendered.result
        uri = link.uri
        if not isinstance(result, RenderResult) or not result.ok:
            return _render_refused(result, verdict="REFUSED", settings=settings)
        folder = Path(output_dir)
        services = cli_ctx.services
        camera = _printable_camera(camera_settings(loaded_config(cli_ctx)))
        checked = (
            None
            if link.one_way
            else CheckedJob(
                renders=record_renders(label, None, options),
                check=_upload_check(settings, cli_ctx),
                held=rendered.held or HeldImages(),
                reset=_reset_check(settings),
            )
        )
        try:
            report = verify_print(
                result,
                label,
                options=options,
                uri=uri,
                timeout=settings.timeout,
                camera=camera,
                output_dir=folder,
                open_printer=printer_opener(cli_ctx, settings),
                capture=services.capture_image,
                sleep=services.sleep,
                wait=_idle_wait(settings, cli_ctx),
                checked=checked,
            )
        except UploadRefusedError as exc:
            return _upload_refused(exc, uri=uri, settings=settings)
        expected = folder / f"expected-{datetime.now(tz=timezone.utc):%Y%m%dT%H%M%S%fZ}.png"
        expected.write_bytes(report.expected_png)
        payload = VerifyPayload(
            printer=require_uri(settings),
            bytes_sent=report.bytes_sent,
            status=report.status,
            photo=str(report.photo.path),
            expected=str(expected),
            findings=[] if report.result is None else [FindingOut.of(f) for f in report.result.findings],
        )
        told = [f for f in payload.findings if _worth_telling(f)]
        human = "\n".join([f"photo:    {payload.photo}", f"expected: {payload.expected}", *_told_lines(told)])
        return CommandResult(ok=True, payload=payload, human=human)

    run_command(ctx, command=CommandName.VERIFY, body=body)


__all__ = [
    "KindChoice",
    "cli_cancel",
    "cli_capture",
    "cli_cut",
    "cli_detect",
    "cli_feed",
    "cli_memory",
    "cli_pause",
    "cli_preview",
    "cli_print",
    "cli_reset",
    "cli_status",
    "cli_verify",
]
