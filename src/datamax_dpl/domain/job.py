"""Job-level DPL: system commands before the format, format settings, quantity, cutting.

System commands that carry a distance are written in inch mode, preceded by ``<STX>n``, so the
job does not depend on whatever unit mode the printer was left in.
"""

from __future__ import annotations

from .enums import MediaSensor, SpeedCode
from .label import Label
from .profiles import Feature, SpeedRange
from .records import STX, line
from .render_context import RenderContext
from .units import DplUnit

INCH_MODE = STX + b"n"
CUT_NOW = STX + b"o"
_MAX_STOP_POSITION = 999


def _system(command: str) -> bytes:
    return STX + command.encode("ascii")


def _media_commands(label: Label, ctx: RenderContext) -> list[bytes]:
    if label.media is None:
        return []
    if label.media is MediaSensor.CONTINUOUS:
        length = label.continuous_length or label.height
        if length is None:
            ctx.findings.refuse("continuous_length_missing", "continuous media needs continuous_length or height")
            return []
        return [_system(f"c{ctx.inch_hundredths(length):04d}")]
    if not ctx.supports(Feature.SENSOR_SELECT):
        ctx.findings.deviate(
            "sensor_select_unsupported",
            f"choosing the media sensor: {ctx.requirement(Feature.SENSOR_SELECT)}",
            fallback="the printer keeps its configured sensor",
        )
        return []
    letter = "e" if label.media is MediaSensor.GAP else "r"
    return [_system("c0000"), _system(letter)]


def _stop_command(value: float, ctx: RenderContext) -> list[bytes]:
    hundredths = ctx.inch_hundredths(value)
    if hundredths > _MAX_STOP_POSITION:
        ctx.findings.refuse("stop_position_too_large", f"stop position takes at most {_MAX_STOP_POSITION}/100 in")
        return []
    return [_system(f"f{hundredths:03d}")]


def _present_commands(label: Label, ctx: RenderContext) -> list[bytes]:
    commands: list[bytes] = []
    if label.stop_position is not None:
        if ctx.supports(Feature.STOP_POSITION):
            commands += _stop_command(label.stop_position, ctx)
        else:
            ctx.findings.deviate(
                "stop_position_unsupported",
                f"stop position: {ctx.requirement(Feature.STOP_POSITION)}",
                fallback="labels stop at the printer's default position",
            )
    if label.present_distance is not None:
        commands += _present_distance(label.present_distance, ctx)
    return commands


def _present_distance(value: float, ctx: RenderContext) -> list[bytes]:
    if ctx.supports(Feature.PRESENT_DISTANCE):
        return [_system(f"Kf{ctx.inch_hundredths(value):04d}")]
    if ctx.supports(Feature.STOP_POSITION):
        ctx.findings.deviate(
            "present_distance_unsupported",
            f"present distance: {ctx.requirement(Feature.PRESENT_DISTANCE)}",
            fallback="sent as stop position (measured from the sensor, not from the next label)",
        )
        return _stop_command(value, ctx)
    ctx.findings.deviate(
        "present_distance_unsupported",
        f"present distance: {ctx.requirement(Feature.PRESENT_DISTANCE)}",
        fallback="ignored",
    )
    return []


def _pause_command(label: Label, ctx: RenderContext) -> list[bytes]:
    if not label.pause_each_label:
        return []
    if ctx.supports(Feature.PAUSE_EACH_LABEL):
        ctx.findings.info("pause_persists", "pause after each label stays on until the printer is reset")
        return [_system("J")]
    ctx.findings.deviate(
        "pause_each_label_unsupported",
        f"pause after each label: {ctx.requirement(Feature.PAUSE_EACH_LABEL)}",
        fallback="the batch prints without pausing",
    )
    return []


def system_prefix(label: Label, ctx: RenderContext) -> list[bytes]:
    """Return the system commands that go before ``<STX>L`` (without image downloads)."""
    commands = _media_commands(label, ctx) + _present_commands(label, ctx) + _pause_command(label, ctx)
    needs_inch = bool(commands) or not ctx.supports(Feature.FORMAT_METRIC)
    return ([INCH_MODE] if needs_inch else []) + commands


def _speed_line(prefix: str, wanted: SpeedCode | None, allowed: SpeedRange | None, ctx: RenderContext) -> list[bytes]:
    if wanted is None:
        return []
    if allowed is None:
        ctx.findings.deviate(
            "speed_unsupported", f"{ctx.profile.name} has no setting for '{prefix}'", fallback="printer default kept"
        )
        return []
    if not allowed.contains(wanted):
        clamped = allowed.clamp(wanted)
        ctx.findings.deviate(
            "speed_out_of_range",
            f"speed {wanted.value} is outside {allowed.low.value}-{allowed.high.value} on {ctx.profile.name}",
            fallback=f"speed {clamped.value}",
        )
        wanted = clamped
    return [line(f"{prefix}{wanted.value}")]


def _heat_line(label: Label, ctx: RenderContext) -> list[bytes]:
    if label.heat is None:
        return []
    heat = label.heat
    if heat > ctx.profile.heat_max:
        ctx.findings.deviate(
            "heat_out_of_range",
            f"heat {heat} is above the {ctx.profile.name} maximum {ctx.profile.heat_max}",
            fallback=f"heat {ctx.profile.heat_max}",
        )
        heat = ctx.profile.heat_max
    return [line(f"H{heat:02d}")]


def _speed_lines(label: Label, ctx: RenderContext) -> list[bytes]:
    backup = ctx.profile.backup_speed if ctx.supports(Feature.BACKUP_SPEED) else None
    return (
        _speed_line("P", label.print_speed, ctx.profile.print_speed, ctx)
        + _speed_line("S", label.slew_speed, ctx.profile.slew_speed, ctx)
        + _speed_line("p", label.backup_speed, backup, ctx)
    )


def _offset_lines(label: Label, ctx: RenderContext) -> list[bytes]:
    lines: list[bytes] = []
    if label.column_offset is not None:
        lines.append(line(f"C{ctx.distance(label.column_offset, element=None, what='column offset'):04d}"))
    if label.row_offset is not None:
        lines.append(line(f"R{ctx.distance(label.row_offset, element=None, what='row offset'):04d}"))
    return lines


def _look_lines(label: Label, ctx: RenderContext) -> list[bytes]:
    lines: list[bytes] = []
    if label.attribute is not None and ctx.supports(Feature.ATTRIBUTE):
        lines.append(line(f"A{label.attribute.value}"))
    elif label.attribute is not None:
        ctx.findings.deviate(
            "attribute_unsupported",
            f"label attribute: {ctx.requirement(Feature.ATTRIBUTE)}",
            fallback="printed with the default attribute",
        )
    if label.mirror and ctx.supports(Feature.MIRROR):
        lines.append(line("M"))
    elif label.mirror:
        ctx.findings.refuse("mirror_unsupported", f"mirror: {ctx.requirement(Feature.MIRROR)}")
    return lines


def _symbol_set_line(label: Label, ctx: RenderContext) -> list[bytes]:
    if label.symbol_set is None:
        return []
    if ctx.supports(Feature.SCALABLE_FONTS):
        return [line(f"yS{label.symbol_set}")]
    ctx.findings.deviate(
        "symbol_set_unsupported",
        f"symbol sets apply to scalable fonts: {ctx.requirement(Feature.SCALABLE_FONTS)}",
        fallback="ignored",
    )
    return []


def _cut_lines(label: Label, ctx: RenderContext) -> list[bytes]:
    cut = label.cut
    if cut is None or cut.every == 1:
        return []
    if cut.every >= label.quantity:
        ctx.findings.info(
            "cut_by_not_below_quantity",
            f"cut every {cut.every} needs more than {cut.every} labels; the printer's own cut setting applies",
        )
        return []
    if ctx.supports(Feature.CUT_BY):
        return [line(f":{cut.every:04d}")]
    ctx.findings.deviate(
        "cut_by_unsupported",
        f"cutting every {cut.every} labels: {ctx.requirement(Feature.CUT_BY)}",
        fallback="the printer cuts after every label",
    )
    return []


def format_header(label: Label, ctx: RenderContext) -> list[bytes]:
    """Return ``<STX>L`` and the format settings that precede the records."""
    unit_line = [line("m" if ctx.dpl_unit is DplUnit.MM_TENTHS else "n")] if ctx.supports(Feature.FORMAT_METRIC) else []
    dot_size = [line(f"D{label.dot_size.width}{label.dot_size.height}")] if label.dot_size else []
    settings = (
        unit_line
        + dot_size
        + _heat_line(label, ctx)
        + _speed_lines(label, ctx)
        + _offset_lines(label, ctx)
        + _look_lines(label, ctx)
        + _symbol_set_line(label, ctx)
        + _cut_lines(label, ctx)
    )
    extras = ([line("z")] if label.plain_zero else []) + ([line(f"^{label.count_by:02d}")] if label.count_by else [])
    return [line(STX + b"L"), *settings, *extras]


def _check_cutter(label: Label, ctx: RenderContext) -> None:
    if label.cut is None:
        return
    if ctx.cutter_installed is False:
        ctx.findings.refuse("no_cutter", "the label asks for cutting but the printer is configured without a cutter")
    elif ctx.cutter_installed is None:
        ctx.findings.info("cutter_assumed", "cut commands assume the printer's cutter is installed and enabled")


def _rest_cut(label: Label, ctx: RenderContext) -> list[bytes]:
    cut = label.cut
    if cut is None or not cut.at_end or cut.every == 1 or label.quantity % cut.every == 0:
        return []
    if ctx.supports(Feature.CUT_NOW):
        return [CUT_NOW]
    ctx.findings.deviate(
        "cut_now_unsupported",
        f"cutting the last group now: {ctx.requirement(Feature.CUT_NOW)}",
        fallback="the last group is cut when the next job starts",
    )
    return []


def format_footer(label: Label, ctx: RenderContext) -> list[bytes]:
    """Return the quantity and end-of-format lines; queue a final cut where it is needed."""
    _check_cutter(label, ctx)
    ctx.system_suffix.extend(_rest_cut(label, ctx))
    return [line(f"Q{label.quantity:04d}"), line("E")]


__all__ = ["CUT_NOW", "INCH_MODE", "format_footer", "format_header", "system_prefix"]
