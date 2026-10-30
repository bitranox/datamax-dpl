"""Printer commands: print, status, detect, memory, single commands, preview, capture and verify."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import rich_click as click
from pydantic import BaseModel, ConfigDict

from ....application.use_cases import (
    DIRECTORY_IDLE_SECONDS,
    PrinterCommand,
    detect_printer,
    print_result,
    query_status,
    read_memory_directory,
    render_label,
    send_command,
)
from ....application.verify import verify_print
from ....domain.enums import StatusProblem
from ....domain.memory import MemoryDirectory, MemoryKind
from ....domain.preview import canvas_for
from ....domain.profiles import get_profile
from ....domain.render import RenderResult
from ....domain.status import MEMORY_DIRECTORY_REQUESTS, PrinterStatus
from ...preview import pillow_available, preview_findings, render_preview_png
from ..constants import CLICK_CONTEXT_SETTINGS
from ..label_io import load_label, load_records
from ..payloads import FindingOut, findings_text, render_payload
from ..runner import CommandResult, run_command
from ..settings import camera_settings, render_options, require_uri
from ..typed_click import argument, option
from .labels import render_loaded
from .model_options import loaded_config, printer_options_decorator, render_options_decorator, resolve_settings

if TYPE_CHECKING:
    from collections.abc import Mapping

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
        result = render_loaded(load_label(label_source), data, settings)
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
    problems: list[StatusProblem]
    flags: PrinterStatus


@click.command("status", context_settings=CLICK_CONTEXT_SETTINGS)
@printer_options_decorator
@click.pass_context
def cli_status(ctx: click.Context, **given: Any) -> None:
    """Ask the printer for its status (needs a channel that can read: tcp or serial)."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, **given)
        uri = require_uri(settings)
        status = query_status(uri=uri, timeout=settings.timeout, open_printer=cli_ctx.services.open_printer)
        payload = StatusPayload(printer=uri, idle=status.idle, problems=list(status.problems), flags=status)
        lines = [f"{name:16} {'yes' if is_set else 'no'}" for name, is_set in status.flags()]
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


class MemoryPayload(BaseModel):
    """Result of ``memory``: one directory per kind asked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printer: str
    directories: list[MemoryDirectory]


#: What the manual (``<STX>q`` Clear Module, page 35) says a "No Modules Available" answer means.
NO_MODULES_MEANING = "the manual: corrupt or worn-out module"
#: ``--kind`` value that lists fonts, graphics and formats, in that order.
ALL_KINDS = "all"
#: Each ``--kind`` value with the kinds it asks for.
_KIND_CHOICES: Mapping[str, tuple[MemoryKind, ...]] = MappingProxyType(
    {**{str(k): (k,) for k in MEMORY_DIRECTORY_REQUESTS}, ALL_KINDS: tuple(MEMORY_DIRECTORY_REQUESTS)}
)


@click.command("memory", context_settings=CLICK_CONTEXT_SETTINGS)
@option(
    "--kind",
    type=click.Choice(sorted(str(k) for k in _KIND_CHOICES)),
    default="graphic",
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
        settings = resolve_settings(cli_ctx, **given)
        uri = require_uri(settings)
        directories = read_memory_directory(
            kinds=_KIND_CHOICES[kind],
            uri=uri,
            timeout=settings.timeout,
            idle=idle,
            open_printer=cli_ctx.services.open_printer,
        )
        payload = MemoryPayload(printer=uri, directories=list(directories))
        return CommandResult(ok=True, payload=payload, human="\n".join(_directory_lines(directories)))

    run_command(ctx, command="memory", body=body)


def _directory_lines(directories: tuple[MemoryDirectory, ...]) -> list[str]:
    """The human view: per kind, each module with its free bytes and the names it holds."""
    lines: list[str] = []
    for directory in directories:
        lines.append(f"{directory.kind}:")
        if not directory.parsed:
            lines.append(f"  answer not understood: {directory.raw!r}")
        if directory.no_modules_available:
            lines.append(f"  printer reports no modules available ({NO_MODULES_MEANING})")
        for module in directory.modules:
            free = "unknown" if module.available_bytes is None else str(module.available_bytes)
            lines.append(f"  module {module.module}: {free} bytes free")
            lines.extend(f"    {name}" for name in module.names)
    return lines


class SentPayload(BaseModel):
    """Result of a single printer command."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printer: str
    command: PrinterCommand
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
            payload = SentPayload(printer=uri, command=command, bytes_sent=report.bytes_sent)
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
    findings: list[FindingOut]


@click.command("preview", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("label_source", metavar="LABEL")
@option("-o", "--output", required=True, help="PNG file to write.")
@option("--data", default=None, help="Data records; the first record fills the placeholders.")
@option("--model", default=None, help="Printer model profile; decides the resolution and print width.")
@click.pass_context
def cli_preview(ctx: click.Context, label_source: str, output: str, data: str | None, model: str | None) -> None:
    """Draw the label at the printer's resolution as a PNG (text lettered with Pillow, else grey cells)."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, model=model)
        label = load_label(label_source)
        if data is not None:
            from ....domain.placeholders import fill_label  # noqa: PLC0415 - only the preview of a batch needs it

            label = fill_label(label, load_records(data)[0])
        profile = get_profile(settings.model)
        options = render_options(settings)
        canvas = canvas_for(label, profile, options=options)
        # Rendered for how the job sends its fields: halved images and merged groups preview as they print.
        sent_as = render_label(label, options).sent_as
        Path(output).write_bytes(render_preview_png(label, profile, sent_as=sent_as, options=options))
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
    status: PrinterStatus | None
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
        # Loaded once and rendered from that copy: LABEL '-' is stdin, which cannot be read twice.
        label = load_label(label_source)
        result = render_loaded(label, None, settings)
        if not isinstance(result, RenderResult) or not result.ok:
            return CommandResult(ok=False, payload=render_payload(result, include_dpl=False), human="REFUSED")
        folder = Path(output_dir)
        services = cli_ctx.services
        report = verify_print(
            result,
            label,
            get_profile(settings.model),
            options=render_options(settings),
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
            status=report.status,
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
    "cli_memory",
    "cli_pause",
    "cli_preview",
    "cli_print",
    "cli_reset",
    "cli_status",
    "cli_verify",
]
