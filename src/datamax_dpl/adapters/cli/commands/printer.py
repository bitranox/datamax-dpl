"""Printer commands: print, status, detect, single commands, preview, capture and verify."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import rich_click as click
from pydantic import BaseModel, ConfigDict

from ....application.use_cases import PrinterCommand, detect_printer, print_result, query_status, send_command
from ....application.verify import verify_print
from ....domain.preview import canvas_for
from ....domain.profiles import get_profile
from ....domain.render import RenderResult
from ...preview import pillow_available, render_preview_png
from ..constants import CLICK_CONTEXT_SETTINGS
from ..label_io import load_label, load_records
from ..payloads import render_payload
from ..runner import CommandResult, run_command
from ..settings import camera_settings, require_uri
from ..typed_click import argument, option
from .labels import build
from .model_options import loaded_config, printer_options_decorator, render_options_decorator, resolve_settings

if TYPE_CHECKING:
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
        settings = resolve_settings(cli_ctx, **given)
        result = build(label_source, data, given, cli_ctx)
        payload = render_payload(result, include_dpl=False)
        if not result.ok:
            return CommandResult(ok=False, payload=payload, human="REFUSED: nothing sent")
        uri = require_uri(settings)
        report = print_result(result, uri=uri, timeout=settings.timeout, open_printer=cli_ctx.services.open_printer)
        payload = payload.model_copy(update={"printer": uri, "bytes_sent": report.bytes_sent})
        return CommandResult(ok=True, payload=payload, human=f"sent {report.bytes_sent} bytes to {uri}")

    run_command(ctx, command="print", body=body)


class StatusPayload(BaseModel):
    """Result of ``status``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printer: str
    idle: bool
    problems: list[str]
    flags: dict[str, bool]


@click.command("status", context_settings=CLICK_CONTEXT_SETTINGS)
@printer_options_decorator
@click.pass_context
def cli_status(ctx: click.Context, **given: Any) -> None:
    """Ask the printer for its status (needs a channel that can read: tcp or serial)."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, **given)
        uri = require_uri(settings)
        status = query_status(uri=uri, timeout=settings.timeout, open_printer=cli_ctx.services.open_printer)
        flags = status.model_dump()
        payload = StatusPayload(printer=uri, idle=status.idle, problems=list(status.problems), flags=flags)
        lines = [f"{name:16} {'yes' if value else 'no'}" for name, value in flags.items()]
        return CommandResult(ok=not status.problems, payload=payload, human="\n".join(lines))

    run_command(ctx, command="status", body=body)


@click.command("detect", context_settings=CLICK_CONTEXT_SETTINGS)
@printer_options_decorator
@click.pass_context
def cli_detect(ctx: click.Context, **given: Any) -> None:
    """Ask the printer what it answers and suggest which model profiles fit."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, **given)
        report = detect_printer(
            uri=require_uri(settings), timeout=settings.timeout, open_printer=cli_ctx.services.open_printer
        )
        human = "\n".join([*report.detection.reasons, "fits: " + (", ".join(report.detection.candidates) or "none")])
        return CommandResult(ok=bool(report.detection.candidates), payload=report, human=human)

    run_command(ctx, command="detect", body=body)


class SentPayload(BaseModel):
    """Result of a single printer command."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printer: str
    command: str
    bytes_sent: int


def _single(command: PrinterCommand, help_text: str) -> click.Command:
    @click.command(command.value, context_settings=CLICK_CONTEXT_SETTINGS, help=help_text)
    @printer_options_decorator
    @click.pass_context
    def run(ctx: click.Context, **given: Any) -> None:
        def body(cli_ctx: CLIContext) -> CommandResult:
            settings = resolve_settings(cli_ctx, **given)
            uri = require_uri(settings)
            report = send_command(
                command, uri=uri, timeout=settings.timeout, open_printer=cli_ctx.services.open_printer
            )
            payload = SentPayload(printer=uri, command=command.value, bytes_sent=report.bytes_sent)
            return CommandResult(ok=True, payload=payload, human=f"{command.value} sent to {uri}")

        run_command(ctx, command=command.value, body=body)

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


@click.command("preview", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("label_source", metavar="LABEL")
@option("-o", "--output", required=True, help="PNG file to write.")
@option("--data", default=None, help="Data records; the first record fills the placeholders.")
@option("--model", default=None, help="Printer model profile; decides the resolution and print width.")
@click.pass_context
def cli_preview(ctx: click.Context, label_source: str, output: str, data: str | None, model: str | None) -> None:
    """Draw the label at the printer's resolution as a PNG (text as grey cells, barcodes hatched)."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, model=model)
        label = load_label(label_source)
        if data is not None:
            from ....domain.placeholders import fill_label  # noqa: PLC0415 - only the preview of a batch needs it

            label = fill_label(label, load_records(data)[0])
        profile = get_profile(settings.model)
        canvas = canvas_for(label, profile)
        Path(output).write_bytes(render_preview_png(label, profile))
        payload = PreviewPayload(
            output=output,
            width_dots=canvas.width,
            height_dots=canvas.height,
            dpi=profile.dpi,
            lettered=pillow_available(),
        )
        return CommandResult(ok=True, payload=payload, human=f"wrote {output} ({canvas.width}x{canvas.height} dots)")

    run_command(ctx, command="preview", body=body)


@click.command("capture", context_settings=CLICK_CONTEXT_SETTINGS)
@option("--output-dir", default=".", help="Folder the photo is written to.")
@click.pass_context
def cli_capture(ctx: click.Context, output_dir: str) -> None:
    """Take one photo with the configured camera ([camera] section)."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        camera = camera_settings(loaded_config(cli_ctx))
        photo = cli_ctx.services.capture_image(camera, output_dir=Path(output_dir))
        return CommandResult(ok=True, payload=photo, human=f"photo {photo.path}")

    run_command(ctx, command="capture", body=body)


class VerifyPayload(BaseModel):
    """Result of ``verify``: where the photo and the expected picture are."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printer: str
    bytes_sent: int
    status: dict[str, bool] | None
    photo: str
    expected: str


@click.command("verify", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("label_source", metavar="LABEL")
@option("--output-dir", default=".", help="Folder for the photo and the expected preview.")
@render_options_decorator
@printer_options_decorator
@click.pass_context
def cli_verify(ctx: click.Context, label_source: str, output_dir: str, **given: Any) -> None:
    """Print a label, wait until the printer is done, photograph it, and write the expected preview beside it."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, **given)
        label = load_label(label_source)
        result = build(label_source, None, given, cli_ctx)
        if not isinstance(result, RenderResult) or not result.ok:
            return CommandResult(ok=False, payload=render_payload(result, include_dpl=False), human="REFUSED")
        folder = Path(output_dir)
        services = cli_ctx.services
        report = verify_print(
            result,
            label,
            get_profile(settings.model),
            uri=require_uri(settings),
            timeout=settings.timeout,
            camera=camera_settings(loaded_config(cli_ctx)),
            output_dir=folder,
            open_printer=services.open_printer,
            capture=services.capture_image,
            sleep=services.sleep,
            clock=services.clock,
        )
        expected = folder / f"expected-{datetime.now(tz=timezone.utc):%Y%m%dT%H%M%S%fZ}.png"
        expected.write_bytes(report.expected_png)
        payload = VerifyPayload(
            printer=require_uri(settings),
            bytes_sent=report.bytes_sent,
            status=report.status.model_dump() if report.status else None,
            photo=str(report.photo.path),
            expected=str(expected),
        )
        human = f"photo:    {payload.photo}\nexpected: {payload.expected}"
        return CommandResult(ok=True, payload=payload, human=human)

    run_command(ctx, command="verify", body=body)


__all__ = [
    "cli_cancel",
    "cli_capture",
    "cli_cut",
    "cli_detect",
    "cli_feed",
    "cli_pause",
    "cli_preview",
    "cli_print",
    "cli_reset",
    "cli_status",
    "cli_verify",
]
