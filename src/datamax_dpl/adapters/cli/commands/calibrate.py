"""Calibration commands: print the two calibration labels, and store what was measured on them."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

import rich_click as click
from pydantic import BaseModel, ConfigDict

from ....application.use_cases import send_bytes
from ....domain.calibration import (
    ALONG_LENGTH_MM,
    across_frame_width,
    across_label,
    along_label,
    scale_from_measurement,
)
from ....domain.errors import ConfigurationError
from ....domain.findings import Finding
from ....domain.render import build_job
from ....domain.units import Axis
from ...transport.uri import is_printer_uri
from .. import safe_console
from ..config_load import load_config
from ..constants import CLICK_CONTEXT_SETTINGS
from ..runner import CommandResult, run_command
from ..settings import PrinterOverrides, printer_settings, render_options, require_uri
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


class ScaleChange(BaseModel):
    """One scale factor ``calibrate set`` changed; ``effective`` is what the configuration now resolves to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    measured: float
    nominal: float
    old: float
    new: float
    effective: float | None


class CalibrateSetPayload(BaseModel):
    """Result of ``calibrate set``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printer: str
    file: str
    changes: list[ScaleChange]
    warnings: list[str]


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
    bar on the liner; 'calibrate set' turns the measurements into the printer's new factors.
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
    msg = f"no label {side}: pass --{side} in mm, or set label_{side} in {where}"
    raise ConfigurationError(msg)


@cli_calibrate.command("set", context_settings=CLICK_CONTEXT_SETTINGS)
@option("--across", type=float, default=None, help="Measured outside width of the across label's frame, in mm.")
@option("--along", type=float, default=None, help="Measured length of the along label's bar, in mm.")
@option("--width", type=float, default=None, help="Width the across label was printed at; default label_width.")
@option("--along-length", type=float, default=ALONG_LENGTH_MM, help="Length the along bar was printed at, in mm.")
@option("--printer", default=None, help="Configured printer name ([printers.NAME]); default the [printer] section.")
@click.pass_context
def cli_calibrate_set(
    ctx: click.Context,
    *,
    across: float | None,
    along: float | None,
    width: float | None,
    along_length: float,
    printer: str | None,
) -> None:
    """Store the scale factors measured on the calibration labels in the user configuration.

    Each new factor is the old one times measured over nominal length; the across nominal is
    the frame's width, the label width less 5 mm on each side. It is written into the
    printer's section of the user configuration, in the file that already holds that section.
    """

    def body(cli_ctx: CLIContext) -> CommandResult:
        name = _printer_name(printer)
        settings = resolve_settings(cli_ctx, printer=name or None)
        measured = {Axis.ACROSS: across, Axis.ALONG: along}
        nominal = {
            Axis.ACROSS: lambda: across_frame_width(_size("width", width, settings.label_width, settings)),
            Axis.ALONG: lambda: along_length,
        }
        changes = [
            _change(axis, settings, measured=value, nominal=nominal[axis]())
            for axis, value in measured.items()
            if value is not None
        ]
        if not changes:
            raise ConfigurationError("nothing measured: pass --across MM, --along MM or both")
        written = cli_ctx.services.set_printer_values(
            printer=name, values={c.key: c.new for c in changes}, profile=cli_ctx.profile
        )
        changes = _with_effective(cli_ctx, name, changes)
        warnings = [_warning(change, written=str(written)) for change in changes if change.effective != change.new]
        for warning in warnings:
            safe_console.echo(warning, err=True)
        payload = CalibrateSetPayload(printer=name, file=str(written), changes=changes, warnings=warnings)
        human = [f"{c.key} {c.old:g} -> {c.new:g} (measured {c.measured:g} of {c.nominal:g} mm)" for c in changes]
        return CommandResult(ok=True, payload=payload, human="\n".join([*human, f"written to {written}"]))

    run_command(ctx, command="calibrate set", body=body)


def _printer_name(given: str | None) -> str:
    if given is not None and is_printer_uri(given):
        msg = f"calibrate set writes into a printer's configuration: pass a configured printer name, not {given!r}"
        raise ConfigurationError(msg)
    return given or ""


def _change(axis: Axis, settings: PrinterSettings, *, measured: float, nominal: float) -> ScaleChange:
    old = settings.scale_across if axis is Axis.ACROSS else settings.scale_along
    new = scale_from_measurement(old, measured=measured, nominal=nominal)
    return ScaleChange(key=f"scale_{axis.value}", measured=measured, nominal=nominal, old=old, new=new, effective=None)


def _with_effective(cli_ctx: CLIContext, name: str, changes: list[ScaleChange]) -> list[ScaleChange]:
    """Read the configuration again: a .env file, an environment variable or --set can still win."""
    config, error = load_config(
        cli_ctx.services, profile=cli_ctx.profile, env_file=cli_ctx.env_file, set_overrides=cli_ctx.set_overrides
    )
    if error is not None:
        return changes
    effective = printer_settings(config, PrinterOverrides(printer=name or None))
    return [c.model_copy(update={"effective": getattr(effective, c.key)}) for c in changes]


def _warning(change: ScaleChange, *, written: str) -> str:
    if change.effective is None:
        return (
            f"warning: wrote {change.key} = {change.new:g} to {written}, but the configuration could not be read back"
        )
    return (
        f"warning: {change.key} is {change.effective:g}, not {change.new:g}: a .env file, an environment "
        f"variable or --set wins over {written}"
    )


def _title(settings: PrinterSettings, scale: float) -> str:
    return f"{settings.name.upper()} SCALE {scale:.4f}".strip()


__all__ = [
    "AxisChoice",
    "CalibratePrintPayload",
    "CalibrateSetPayload",
    "ScaleChange",
    "cli_calibrate",
    "cli_calibrate_print",
    "cli_calibrate_set",
]
