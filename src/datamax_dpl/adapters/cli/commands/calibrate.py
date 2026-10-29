"""Calibration commands: print the two calibration labels for a printer."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

import rich_click as click
from pydantic import BaseModel, ConfigDict

from ....application.use_cases import send_bytes
from ....domain.calibration import ALONG_LENGTH_MM, across_label, along_label
from ....domain.errors import ConfigurationError
from ....domain.findings import Finding
from ....domain.render import build_job
from ....domain.units import Axis
from ..constants import CLICK_CONTEXT_SETTINGS
from ..runner import CommandResult, run_command
from ..settings import render_options, require_uri
from ..typed_click import option
from .model_options import printer_options_decorator, render_options_decorator, resolve_settings

if TYPE_CHECKING:
    from ....domain.label import Label
    from ..context import CLIContext
    from ..settings import PrinterSettings


class AxisChoice(str, Enum):
    """Which calibration labels ``calibrate print`` sends."""

    ACROSS = "across"
    ALONG = "along"
    BOTH = "both"

    @property
    def axes(self) -> tuple[Axis, ...]:
        """The labels to print, across first."""
        return (Axis.ACROSS, Axis.ALONG) if self is AxisChoice.BOTH else (Axis(self.value),)


class CalibratePrintPayload(BaseModel):
    """Result of ``calibrate print``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printer: str
    name: str
    model: str
    labels: list[Axis]
    scale_across: float
    scale_along: float
    bytes_sent: int
    findings: list[Finding]


@click.group("calibrate", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_calibrate() -> None:
    """Print calibration labels to measure a printer's scale factors."""


@cli_calibrate.command("print", context_settings=CLICK_CONTEXT_SETTINGS)
@option(
    "--axis", type=click.Choice([c.value for c in AxisChoice]), default=AxisChoice.BOTH.value, help="Labels to print."
)
@option("--width", type=float, default=None, help="Label width in mm; default the printer's label_width.")
@option("--height", type=float, default=None, help="Label height in mm; default the printer's label_height.")
@option("--along-length", type=float, default=ALONG_LENGTH_MM, help="Length of the along bar in mm, at most 200.")
@render_options_decorator
@printer_options_decorator
@click.pass_context
def cli_calibrate_print(
    ctx: click.Context, axis: str, width: float | None, height: float | None, along_length: float, **given: Any
) -> None:
    """Print the across and along calibration labels with the printer's current scale factors.

    Measure the across label's frame from outside edge to outside edge, and the along label's
    bar on the liner.
    """

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, **given)
        axes = AxisChoice(axis).axes
        labels = [_label(settings, part, width=width, height=height, along_length=along_length) for part in axes]
        options = render_options(settings)
        results = [build_job(label, options) for label in labels]
        findings = [finding for result in results for finding in result.findings]
        payload = CalibratePrintPayload(
            printer=settings.uri,
            name=settings.name,
            model=settings.model,
            labels=list(axes),
            scale_across=settings.scale_across,
            scale_along=settings.scale_along,
            bytes_sent=0,
            findings=findings,
        )
        if not all(result.ok for result in results):
            return CommandResult(ok=False, payload=payload, human="REFUSED: nothing sent")
        uri = require_uri(settings)
        # One send for both formats: a print server can refuse a reconnect for a second or two.
        report = send_bytes(
            b"".join(r.data for r in results),
            uri=uri,
            timeout=settings.timeout,
            open_printer=cli_ctx.services.open_printer,
        )
        payload = payload.model_copy(update={"printer": uri, "bytes_sent": report.bytes_sent})
        return CommandResult(ok=True, payload=payload, human=f"sent {', '.join(axes)} to {uri}")

    run_command(ctx, command="calibrate print", body=body)


def _label(
    settings: PrinterSettings, axis: Axis, *, width: float | None, height: float | None, along_length: float
) -> Label:
    label_width = _size("width", width, settings.label_width, settings)
    if axis is Axis.ALONG:
        return along_label(width=label_width, length=along_length, title=_title(settings, settings.scale_along))
    label_height = _size("height", height, settings.label_height, settings)
    return across_label(width=label_width, height=label_height, title=_title(settings, settings.scale_across))


def _size(side: str, given: float | None, configured: float | None, settings: PrinterSettings) -> float:
    if given is not None:
        return given
    if configured is not None:
        return configured
    where = f"[printers.{settings.name}]" if settings.name else "[printer]"
    msg = f"no label {side}: pass --{side} in mm, or set label_width and label_height in {where}"
    raise ConfigurationError(msg)


def _title(settings: PrinterSettings, scale: float) -> str:
    return f"{settings.name.upper()} SCALE {scale:.4f}".strip()


__all__ = ["AxisChoice", "CalibratePrintPayload", "cli_calibrate", "cli_calibrate_print"]
