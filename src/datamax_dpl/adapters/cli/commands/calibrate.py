"""Calibration commands: print the two calibration labels, and store what was measured on them."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar

import rich_click as click
from pydantic import BaseModel, ConfigDict

from ....application.use_cases import send_bytes
from ....domain.calibration import (
    ALONG_LENGTH_MM,
    across_frame_width,
    across_label,
    along_label,
    default_along_length,
    longest_along_length,
    offset_from_borders,
    scale_from_measurement,
)
from ....domain.enums import FindingCode
from ....domain.errors import ConfigurationError
from ....domain.findings import Finding
from ....domain.render import build_job, effective_calibration, format_row_limit
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
    from ....domain.profiles import CalibrationDefaults
    from ....domain.render import RenderResult
    from ....domain.render_context import RenderOptions
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
    """Result of ``calibrate print``; the factors and offsets are the ones the labels printed with."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printer: str
    name: str
    model: str
    labels: list[Axis]
    scale_across: float
    scale_along: float
    offset_across: float
    offset_along: float
    bytes_sent: int
    findings: list[Finding]
    # Set only when the along bar is refused for passing the format length: the longest
    # --along-length (whole 10 mm) that fits at this calibration, None when not even 20 mm does.
    along_length_that_fits: float | None = None
    hint: str | None = None


class _Change(BaseModel):
    """One setting ``calibrate set`` changed; ``effective`` is what the configuration now resolves to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    old: float
    new: float
    effective: float | None


class ScaleChange(_Change):
    """A scale factor from a length measured against its nominal length, in mm."""

    measured: float
    nominal: float


class OffsetChange(_Change):
    """A position offset from the two borders measured on opposite sides of the frame, in mm.

    ``low`` is the left (across) or bottom (along) border, ``high`` the right or top one.
    """

    low: float
    high: float


_ChangeT = TypeVar("_ChangeT", bound=_Change)


class CalibrateSetPayload(BaseModel):
    """Result of ``calibrate set``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printer: str
    file: str
    changes: list[ScaleChange]
    offsets: list[OffsetChange]
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
@option(
    "--along-length",
    type=float,
    default=None,
    help="Length of the along bar in mm, at most 200; default the longest whole 10 mm one format holds.",
)
@render_options_decorator
@printer_options_decorator
@click.pass_context
def cli_calibrate_print(
    ctx: click.Context, axis: str, width: float | None, height: float | None, along_length: float | None, **given: Any
) -> None:
    """Print the across and along calibration labels with the printer's current scale factors.

    Measure the across label's frame from outside edge to outside edge, and the along label's
    bar on the liner; 'calibrate set' turns the measurements into the printer's new factors.
    """

    def body(cli_ctx: CLIContext) -> CommandResult:
        settings = resolve_settings(cli_ctx, **given)
        axes = AxisChoice(axis).axes
        options = render_options(settings)
        sizes = _Sizes(width=width, height=height, along_length=along_length)
        labels, bar = _labels(settings, axes, options=options, sizes=sizes)
        results = {part: build_job(label, options) for part, label in labels.items()}
        payload = _print_payload(settings, effective_calibration(options), axes=axes, results=list(results.values()))
        if not all(result.ok for result in results.values()):
            if _only_the_along_bar_too_long(results):
                along = labels[Axis.ALONG]
                payload = _with_length_hint(payload, along, settings=settings, options=options, below=bar)
            human = "REFUSED: nothing sent" if payload.hint is None else f"REFUSED: nothing sent\n{payload.hint}"
            return CommandResult(ok=False, payload=payload, human=human)
        uri = require_uri(settings)
        # One send for both formats: a print server can refuse a reconnect for a second or two.
        report = send_bytes(
            b"".join(r.data for r in results.values()),
            uri=uri,
            timeout=settings.timeout,
            open_printer=cli_ctx.services.open_printer,
        )
        payload = payload.model_copy(update={"printer": uri, "bytes_sent": report.bytes_sent})
        return CommandResult(ok=True, payload=payload, human=f"sent {', '.join(axes)} to {uri}")

    run_command(ctx, command="calibrate print", body=body)


def _only_the_along_bar_too_long(results: dict[Axis, RenderResult]) -> bool:
    """Whether the along bar's length is the job's only error, so a shorter bar alone would print."""
    errors = [(axis, finding.code) for axis, result in results.items() for finding in result.errors]
    return bool(errors) and all(
        (axis, code) == (Axis.ALONG, FindingCode.FORMAT_PAST_MAX_LENGTH) for axis, code in errors
    )


def _with_length_hint(
    payload: CalibratePrintPayload, label: Label, *, settings: PrinterSettings, options: RenderOptions, below: float
) -> CalibratePrintPayload:
    """Add the longest along length that fits to the payload of a refused along label."""
    title = _title(settings, effective_calibration(options).scale_along)
    # along_label always sets the width, so the 0 never reaches longest_along_length.
    fits = longest_along_length(width=label.width or 0.0, title=title, options=options, below=below)
    return payload.model_copy(update={"along_length_that_fits": fits, "hint": _length_hint(fits)})


def _length_hint(fits: float | None) -> str:
    """Name the along length to print instead; the same length goes to 'calibrate set'."""
    if fits is None:
        return (
            "not even a 20 mm along bar fits in one format at this calibration: check scale_along, "
            "offset_along and max_format_rows"
        )
    return (
        f"the along bar is longer than one format holds at this calibration: print it with "
        f"--along-length {fits:g}, and pass the same --along-length {fits:g} to 'calibrate set'"
    )


def _print_payload(
    settings: PrinterSettings,
    calibration: CalibrationDefaults,
    *,
    axes: tuple[Axis, ...],
    results: list[RenderResult],
) -> CalibratePrintPayload:
    """The payload before anything is sent: the values the labels printed with and every finding."""
    return CalibratePrintPayload(
        printer=settings.uri,
        name=settings.name,
        model=settings.model,
        labels=list(axes),
        scale_across=calibration.scale_across,
        scale_along=calibration.scale_along,
        offset_across=calibration.offset_across,
        offset_along=calibration.offset_along,
        bytes_sent=0,
        findings=[finding for result in results for finding in result.findings],
    )


class _Sizes(BaseModel):
    """The label sizes and along bar length given on the command line; None takes the default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    width: float | None
    height: float | None
    along_length: float | None


def _labels(
    settings: PrinterSettings, axes: tuple[Axis, ...], *, options: RenderOptions, sizes: _Sizes
) -> tuple[dict[Axis, Label], float]:
    """Build the labels in ``axes`` order, and return them with the along bar's length in mm.

    The across label is built first, so a label too small for it is refused for that before the
    along length is worked out; the length is :data:`ALONG_LENGTH_MM` when no along label is built.
    """
    calibration = effective_calibration(options)
    labels: dict[Axis, Label] = {}
    bar = ALONG_LENGTH_MM
    for axis in axes:
        label_width = _size("width", sizes.width, settings.label_width, settings)
        if axis is Axis.ALONG:
            bar = _along_bar_length(sizes.along_length, settings=settings, width=label_width, options=options)
            labels[axis] = along_label(width=label_width, length=bar, title=_title(settings, calibration.scale_along))
            continue
        label_height = _size("height", sizes.height, settings.label_height, settings)
        labels[axis] = across_label(width=label_width, height=label_height, title=_across_title(settings, calibration))
    return labels, bar


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
@option("--width", type=float, default=None, help="Width the labels were printed at; default label_width.")
@option(
    "--along-length",
    type=float,
    default=None,
    help="Length the along bar was printed at, in mm; default the length 'calibrate print' defaults to.",
)
@option("--top", type=float, default=None, help="Measured border above the across label's frame, in mm.")
@option("--bottom", type=float, default=None, help="Measured border below the across label's frame, in mm.")
@option("--left", type=float, default=None, help="Measured border left of the across label's frame, in mm.")
@option("--right", type=float, default=None, help="Measured border right of the across label's frame, in mm.")
@option("--printer", default=None, help="Configured printer name ([printers.NAME]); default the [printer] section.")
@click.pass_context
def cli_calibrate_set(
    ctx: click.Context,
    *,
    across: float | None,
    along: float | None,
    width: float | None,
    along_length: float | None,
    top: float | None,
    bottom: float | None,
    left: float | None,
    right: float | None,
    printer: str | None,
) -> None:
    """Store the scale factors and offsets measured on the calibration labels in the user configuration.

    Each new factor is the one the label printed with (the model's measured default where
    none is configured) times measured over nominal length; the across nominal is the frame's
    width, the label width less 2 mm on each side, and the along nominal is the bar length
    'calibrate print' printed by default, unless --along-length names the one printed. The
    borders around the frame come in pairs (--top with --bottom, --left with --right); each pair
    moves the print half their difference toward the wider border, from the offset in force.
    Everything is written into the printer's section of the user configuration, in the file that
    already holds that section.
    """

    def body(cli_ctx: CLIContext) -> CommandResult:
        name = _printer_name(printer)
        settings = resolve_settings(cli_ctx, printer=name or None)
        # The labels printed with the effective values, the model's default where none is set,
        # so each new value starts from those.
        options = render_options(settings)
        calibration = effective_calibration(options)
        measured = {Axis.ACROSS: across, Axis.ALONG: along}
        nominal = {
            Axis.ACROSS: lambda: across_frame_width(_size("width", width, settings.label_width, settings)),
            Axis.ALONG: lambda: _along_bar_length(along_length, settings=settings, width=width, options=options),
        }
        changes = [
            _change(axis, calibration, measured=value, nominal=nominal[axis]())
            for axis, value in measured.items()
            if value is not None
        ]
        offsets = _offsets(calibration, top=top, bottom=bottom, left=left, right=right)
        if not changes and not offsets:
            msg = (
                "nothing measured: pass --across MM, --along MM, or a pair of borders (--top/--bottom, --left/--right)"
            )
            raise ConfigurationError(msg)
        values = {c.key: c.new for c in [*changes, *offsets]}
        written = cli_ctx.services.set_printer_values(printer=name, values=values, profile=cli_ctx.profile)
        changes = _with_effective(cli_ctx, name, changes)
        offsets = _with_effective(cli_ctx, name, offsets)
        warnings = [_warning(c, written=str(written)) for c in [*changes, *offsets] if c.effective != c.new]
        for warning in warnings:
            safe_console.echo(warning, err=True)
        payload = CalibrateSetPayload(
            printer=name, file=str(written), changes=changes, offsets=offsets, warnings=warnings
        )
        human = [f"{c.key} {c.old:g} -> {c.new:g} (measured {c.measured:g} of {c.nominal:g} mm)" for c in changes]
        human += [f"{o.key} {o.old:g} -> {o.new:g} mm (borders {o.low:g} and {o.high:g} mm)" for o in offsets]
        return CommandResult(ok=True, payload=payload, human="\n".join([*human, f"written to {written}"]))

    run_command(ctx, command="calibrate set", body=body)


def _along_bar_length(
    given: float | None, *, settings: PrinterSettings, width: float | None, options: RenderOptions
) -> float:
    """The along bar length as given, else the one 'calibrate print' prints and 'calibrate set' measures against.

    That default is the longest bar one format holds, worked out on the along label as print
    builds it, so it needs the label width wherever a format limit applies; 200 mm without one.
    """
    if given is not None:
        return given
    if format_row_limit(options) is None:
        return ALONG_LENGTH_MM
    label_width = width if width is not None else settings.label_width
    if label_width is None:
        where = f"[printers.{settings.name}]" if settings.name else "[printer]"
        msg = (
            "the along bar's default length depends on the label width: pass --along-length MM (the "
            f"length printed on the label), or --width MM, or set label_width in {where}"
        )
        raise ConfigurationError(msg)
    title = _title(settings, effective_calibration(options).scale_along)
    return default_along_length(width=label_width, title=title, options=options)


def _printer_name(given: str | None) -> str:
    if given is not None and is_printer_uri(given):
        msg = f"calibrate set writes into a printer's configuration: pass a configured printer name, not {given!r}"
        raise ConfigurationError(msg)
    return given or ""


def _change(axis: Axis, calibration: CalibrationDefaults, *, measured: float, nominal: float) -> ScaleChange:
    old = calibration.scale_across if axis is Axis.ACROSS else calibration.scale_along
    new = scale_from_measurement(old, measured=measured, nominal=nominal)
    return ScaleChange(key=f"scale_{axis.value}", measured=measured, nominal=nominal, old=old, new=new, effective=None)


def _offsets(
    calibration: CalibrationDefaults,
    *,
    top: float | None,
    bottom: float | None,
    left: float | None,
    right: float | None,
) -> list[OffsetChange]:
    pairs = [
        ("offset_across", calibration.offset_across, (left, "--left"), (right, "--right")),
        ("offset_along", calibration.offset_along, (bottom, "--bottom"), (top, "--top")),
    ]
    offsets: list[OffsetChange] = []
    for key, old, (low, low_flag), (high, high_flag) in pairs:
        if low is None and high is None:
            continue
        if low is None or high is None:
            given, missing = (high_flag, low_flag) if low is None else (low_flag, high_flag)
            msg = f"{given} needs {missing}: the offset comes from the borders on both sides of the frame"
            raise ConfigurationError(msg)
        new = offset_from_borders(old, low=low, high=high)
        offsets.append(OffsetChange(key=key, low=low, high=high, old=old, new=new, effective=None))
    return offsets


def _with_effective(cli_ctx: CLIContext, name: str, changes: list[_ChangeT]) -> list[_ChangeT]:
    """Read the configuration again: a .env file, an environment variable or --set can still win.

    The value read back is the effective one, so a key no layer sets reads as the model's default,
    never as unreadable; ``effective`` stays None only when the configuration does not load.
    """
    config, error = load_config(
        cli_ctx.services, profile=cli_ctx.profile, env_file=cli_ctx.env_file, set_overrides=cli_ctx.set_overrides
    )
    if error is not None:
        return changes
    settings = printer_settings(config, PrinterOverrides(printer=name or None))
    effective = effective_calibration(render_options(settings))
    return [c.model_copy(update={"effective": getattr(effective, c.key)}) for c in changes]


def _warning(change: _Change, *, written: str) -> str:
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


def _across_title(settings: PrinterSettings, calibration: CalibrationDefaults) -> str:
    offsets = f"OFFSET ACROSS {calibration.offset_across:+.2f} ALONG {calibration.offset_along:+.2f}"
    # Its own line, so a wrap never strands a lone value away from its axis name.
    return f"{_title(settings, calibration.scale_across)}\n{offsets}"


__all__ = [
    "AxisChoice",
    "CalibratePrintPayload",
    "CalibrateSetPayload",
    "OffsetChange",
    "ScaleChange",
    "cli_calibrate",
    "cli_calibrate_print",
    "cli_calibrate_set",
]
