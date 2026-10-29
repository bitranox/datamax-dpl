"""Job-level DPL: system commands before the format, format settings, quantity, cutting.

System commands that carry a distance are written in inch mode, preceded by ``<STX>n``, so the
job does not depend on whatever unit mode the printer was left in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .enums import FindingCode, MediaSensor, SpeedCode
from .profiles import Feature, SpeedRange
from .records import STX, line
from .units import MM_PER_INCH, Axis, DplUnit

if TYPE_CHECKING:
    from .label import Label
    from .render_context import RenderContext

INCH_MODE = STX + b"n"
CUT_NOW = STX + b"o"
#: Largest stop position ``<STX>f`` takes: three digits of hundredths of an inch.
MAX_STOP_POSITION = 999
#: The same limit in millimetres (253.746), for a stop position configured in mm; rounded to the
#: thousandth, which holds it exactly, so a refusal names 253.746 rather than its float noise.
MAX_STOP_POSITION_MM = round(MAX_STOP_POSITION / 100 * MM_PER_INCH, 3)


def _system(command: str) -> bytes:
    return STX + command.encode("ascii")


def _media_commands(label: Label, ctx: RenderContext) -> list[bytes]:
    if label.media is None:
        return []
    if label.media is MediaSensor.CONTINUOUS:
        length = label.continuous_length or label.height
        if length is None:
            ctx.findings.refuse(
                FindingCode.CONTINUOUS_LENGTH_MISSING, "continuous media needs continuous_length or height"
            )
            return []
        return [_system(f"c{ctx.inch_hundredths(length, axis=Axis.ALONG):04d}")]
    if not ctx.supports(Feature.SENSOR_SELECT):
        ctx.findings.deviate(
            FindingCode.SENSOR_SELECT_UNSUPPORTED,
            f"choosing the media sensor: {ctx.requirement(Feature.SENSOR_SELECT)}",
            fallback="the printer keeps its configured sensor",
        )
        return []
    letter = "e" if label.media is MediaSensor.GAP else "r"
    return [_system("c0000"), _system(letter)]


def _present_distance_is_a_stop(ctx: RenderContext) -> bool:
    """Return True when this model can send a present distance only as a stop position."""
    return not ctx.supports(Feature.PRESENT_DISTANCE) and ctx.supports(Feature.STOP_POSITION)


def _label_sets_stop(label: Label, ctx: RenderContext) -> bool:
    """Return True when the label stops itself: its ``stop_position``, or a present distance sent as one."""
    return label.stop_position is not None or (label.present_distance is not None and _present_distance_is_a_stop(ctx))


def stops_at_tear_off(label: Label, ctx: RenderContext) -> bool:
    """Return True when the job stops at the configured tear-off position.

    The label's own stop wins over any configured one (a present distance this model can only send
    as a stop position counts as the label's stop), and a job that cuts stops where the cutter
    needs it (``cutter_stop_position_mm``), never at the tear-off position.
    """
    return not _label_sets_stop(label, ctx) and label.cut is None


def _stop_command(hundredths: int, ctx: RenderContext) -> list[bytes]:
    if hundredths > MAX_STOP_POSITION:
        ctx.findings.refuse(
            FindingCode.STOP_POSITION_TOO_LARGE, f"stop position takes at most {MAX_STOP_POSITION}/100 in"
        )
        return []
    return [_system(f"f{hundredths:03d}")]


def _label_stop(value: float, ctx: RenderContext) -> list[bytes]:
    """Return the command for the label's own stop position.

    It is a length on the label, so it prints through the printer's correction like every other
    length, and a model without the command deviates from what the label asks for.
    """
    if ctx.supports(Feature.STOP_POSITION):
        return _stop_command(ctx.inch_hundredths(value, axis=Axis.ALONG), ctx)
    ctx.findings.deviate(
        FindingCode.STOP_POSITION_UNSUPPORTED,
        f"stop position: {ctx.requirement(Feature.STOP_POSITION)}",
        fallback="labels stop at the printer's default position",
    )
    return []


def _configured_stop(label: Label, ctx: RenderContext) -> list[bytes]:
    """Return the command for the configured stop position this job uses, if any.

    A configured stop position (an option, the config file, or the model's default) is a distance
    found by trying stop values on the printer, so it is sent exactly as configured, without the
    printer's scale or offset: the Allegro 2's 67.4 mm is what ``<STX>f265`` stopped at, and a
    scaled conversion would move that stop. It is set once for every job, not asked for by this
    label, so a model without the command only reports that it was not sent.
    """
    if _label_sets_stop(label, ctx):
        # The present distance goes out as the stop position (_present_distance); a second
        # stop command beside it would only be overwritten by it.
        return []
    configured = ctx.stop_position_mm if stops_at_tear_off(label, ctx) else ctx.cutter_stop_position_mm
    if configured is None:
        return []
    if not ctx.supports(Feature.STOP_POSITION):
        ctx.findings.info(
            FindingCode.CONFIGURED_STOP_POSITION_UNSUPPORTED,
            f"configured stop position {configured:g} mm not sent: "
            f"no stop-position command ({ctx.requirement(Feature.STOP_POSITION)})",
        )
        return []
    return _stop_command(ctx.nominal_inch_hundredths(configured), ctx)


def _present_commands(label: Label, ctx: RenderContext) -> list[bytes]:
    if label.stop_position is not None:
        commands = _label_stop(label.stop_position, ctx)
    else:
        commands = _configured_stop(label, ctx)
    if label.present_distance is not None:
        commands += _present_distance(label.present_distance, ctx)
    return commands


def _present_distance(value: float, ctx: RenderContext) -> list[bytes]:
    if ctx.supports(Feature.PRESENT_DISTANCE):
        return [_system(f"Kf{ctx.inch_hundredths(value, axis=Axis.ALONG):04d}")]
    if _present_distance_is_a_stop(ctx):
        ctx.findings.deviate(
            FindingCode.PRESENT_DISTANCE_UNSUPPORTED,
            f"present distance: {ctx.requirement(Feature.PRESENT_DISTANCE)}",
            fallback="sent as stop position (measured from the sensor, not from the next label)",
        )
        return _stop_command(ctx.inch_hundredths(value, axis=Axis.ALONG), ctx)
    ctx.findings.deviate(
        FindingCode.PRESENT_DISTANCE_UNSUPPORTED,
        f"present distance: {ctx.requirement(Feature.PRESENT_DISTANCE)}",
        fallback="ignored",
    )
    return []


def _pause_command(label: Label, ctx: RenderContext) -> list[bytes]:
    if not label.pause_each_label:
        return []
    if ctx.supports(Feature.PAUSE_EACH_LABEL):
        ctx.findings.info(FindingCode.PAUSE_PERSISTS, "pause after each label stays on until the printer is reset")
        return [_system("J")]
    ctx.findings.deviate(
        FindingCode.PAUSE_EACH_LABEL_UNSUPPORTED,
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
            FindingCode.SPEED_UNSUPPORTED,
            f"{ctx.profile.name} has no setting for '{prefix}'",
            fallback="printer default kept",
        )
        return []
    if not allowed.contains(wanted):
        clamped = allowed.clamp(wanted)
        ctx.findings.deviate(
            FindingCode.SPEED_OUT_OF_RANGE,
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
            FindingCode.HEAT_OUT_OF_RANGE,
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
        lines.append(
            line(f"C{ctx.distance(label.column_offset, axis=Axis.ACROSS, element=None, what='column offset'):04d}")
        )
    if label.row_offset is not None:
        lines.append(line(f"R{ctx.distance(label.row_offset, axis=Axis.ALONG, element=None, what='row offset'):04d}"))
    return lines


def _look_lines(label: Label, ctx: RenderContext) -> list[bytes]:
    lines: list[bytes] = []
    if label.attribute is not None and ctx.supports(Feature.ATTRIBUTE):
        lines.append(line(f"A{label.attribute.value}"))
    elif label.attribute is not None:
        ctx.findings.deviate(
            FindingCode.ATTRIBUTE_UNSUPPORTED,
            f"label attribute: {ctx.requirement(Feature.ATTRIBUTE)}",
            fallback="printed with the default attribute",
        )
    if label.mirror and ctx.supports(Feature.MIRROR):
        lines.append(line("M"))
    elif label.mirror:
        ctx.findings.refuse(FindingCode.MIRROR_UNSUPPORTED, f"mirror: {ctx.requirement(Feature.MIRROR)}")
    return lines


def _symbol_set_line(label: Label, ctx: RenderContext) -> list[bytes]:
    if label.symbol_set is None:
        return []
    if ctx.supports(Feature.SCALABLE_FONTS):
        return [line(f"yS{label.symbol_set}")]
    ctx.findings.deviate(
        FindingCode.SYMBOL_SET_UNSUPPORTED,
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
            FindingCode.CUT_BY_NOT_BELOW_QUANTITY,
            f"cut every {cut.every} needs more than {cut.every} labels; the printer's own cut setting applies",
        )
        return []
    if ctx.supports(Feature.CUT_BY):
        return [line(f":{cut.every:04d}")]
    ctx.findings.deviate(
        FindingCode.CUT_BY_UNSUPPORTED,
        f"cutting every {cut.every} labels: {ctx.requirement(Feature.CUT_BY)}",
        fallback="the printer cuts after every label",
    )
    return []


def format_header(label: Label, ctx: RenderContext) -> list[bytes]:
    """Return ``<STX>L`` and the format settings that precede the records."""
    unit_line = [line("m" if ctx.dpl_unit is DplUnit.MM_TENTHS else "n")] if ctx.supports(Feature.FORMAT_METRIC) else []
    settings = (
        unit_line
        + _dot_size_lines(label, ctx)
        + _heat_line(label, ctx)
        + _speed_lines(label, ctx)
        + _offset_lines(label, ctx)
        + _look_lines(label, ctx)
        + _symbol_set_line(label, ctx)
        + _cut_lines(label, ctx)
    )
    extras = ([line("z")] if label.plain_zero else []) + ([line(f"^{label.count_by:02d}")] if label.count_by else [])
    return [line(STX + b"L"), *settings, *extras]


def _dot_size_lines(label: Label, ctx: RenderContext) -> list[bytes]:
    """Return the ``Dwh`` line: the label's own dot size, else ``D11`` where the default is larger.

    Everything the library measures in dots (images, barcode modules, preview, validation) counts
    one dot as one printhead dot, which holds only at ``D11``; most models default to ``D22`` and
    print every dot as 2x2 (measured on an Allegro 2).
    """
    if label.dot_size:
        return [line(f"D{label.dot_size.width}{label.dot_size.height}")]
    if ctx.profile.default_dot_size != (1, 1):
        return [line("D11")]
    return []


def _check_cutter(label: Label, ctx: RenderContext) -> None:
    if label.cut is None:
        return
    if ctx.cutter_installed is False:
        ctx.findings.refuse(
            FindingCode.NO_CUTTER, "the label asks for cutting but the printer is configured without a cutter"
        )
    elif ctx.cutter_installed is None:
        ctx.findings.info(
            FindingCode.CUTTER_ASSUMED, "cut commands assume the printer's cutter is installed and enabled"
        )


def _rest_cut(label: Label, ctx: RenderContext) -> list[bytes]:
    cut = label.cut
    if cut is None or not cut.at_end or cut.every == 1 or label.quantity % cut.every == 0:
        return []
    if ctx.supports(Feature.CUT_NOW):
        return [CUT_NOW]
    ctx.findings.deviate(
        FindingCode.CUT_NOW_UNSUPPORTED,
        f"cutting the last group now: {ctx.requirement(Feature.CUT_NOW)}",
        fallback="the last group is cut when the next job starts",
    )
    return []


def format_footer(label: Label, ctx: RenderContext) -> list[bytes]:
    """Return the quantity and end-of-format lines; queue a final cut where it is needed."""
    _check_cutter(label, ctx)
    ctx.system_suffix.extend(_rest_cut(label, ctx))
    return [line(f"Q{label.quantity:04d}"), line("E")]


__all__ = [
    "CUT_NOW",
    "INCH_MODE",
    "MAX_STOP_POSITION",
    "MAX_STOP_POSITION_MM",
    "format_footer",
    "format_header",
    "stops_at_tear_off",
    "system_prefix",
]
