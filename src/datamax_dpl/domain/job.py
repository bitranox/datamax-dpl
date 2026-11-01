"""Job-level DPL: system commands before the format, format settings, quantity, cutting.

System commands that carry a distance are written in inch mode, preceded by ``<STX>n``, so the
job does not depend on whatever unit mode the printer was left in. For the same reason every
label sets the persistent printer state it relies on (media, start of print, maximum label
length, feed rate, software switches, stop position) and clears the image module around itself:
another program or the front panel may have changed any of them since the last job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from .charset import DEFAULT_SYMBOL_SET
from .enums import FindingCode, MediaSensor, PrinterSetting, SpeedCode
from .held_images import EMPTY_MODULE, HeldImages
from .profiles import Feature, SpeedRange
from .records import STX, line
from .transparency import emulates_transparency
from .units import MM_PER_INCH, Axis, DplUnit, hundredths_to_mm, to_inches

if TYPE_CHECKING:
    from .label import Label
    from .render_context import ImageDownload, RenderContext

INCH_MODE = STX + b"n"
CUT_NOW = STX + b"o"
#: Largest stop position ``<STX>f`` takes: three digits of hundredths of an inch.
MAX_STOP_POSITION = 999
#: The same limit in millimetres (253.746), for a stop position configured in mm.
MAX_STOP_POSITION_MM = hundredths_to_mm(MAX_STOP_POSITION)
#: The stop position that sends no ``<STX>f`` and keeps the printer's own setting, in the label
#: and in the configuration alike.
PRINTER_STOP: Final = "printer"
#: Largest present distance ``<STX>Kf`` takes: four digits (manual page 53), hundredths of an inch
#: because the job's system commands go out in inch mode.
MAX_PRESENT_DISTANCE = 9999


def _system(command: str) -> bytes:
    return STX + command.encode("ascii")


def _sends(setting: PrinterSetting, ctx: RenderContext) -> bool:
    return setting in ctx.printer_state.send


def _media_commands(label: Label, ctx: RenderContext) -> list[bytes]:
    if not _sends(PrinterSetting.MEDIA, ctx):
        return []
    media = ctx.printer_state.media
    if media is MediaSensor.CONTINUOUS:
        length = label.continuous_length or label.height
        if length is None:
            ctx.findings.refuse(
                FindingCode.CONTINUOUS_LENGTH_MISSING, "continuous media needs continuous_length or height"
            )
            return []
        return [_system(f"c{ctx.inch_hundredths(length, axis=Axis.ALONG):04d}")]
    # Every other label relies on the printer finding label edges, which a continuous length
    # another program left behind would stop: c0000 ends continuous mode whether or not a sensor
    # is named, and on a model that cannot choose its sensor.
    commands = [_system("c0000")]
    if media is None:
        return commands
    if not ctx.supports(Feature.SENSOR_SELECT):
        _report_sensor_unsupported(media, ctx)
        return commands
    letter = "e" if media is MediaSensor.GAP else "r"
    return [*commands, _system(letter)]


def _report_sensor_unsupported(media: MediaSensor, ctx: RenderContext) -> None:
    """Report a media sensor this model cannot choose: a deviation for the label's own, else info.

    A configured sensor belongs to the printer, not to what this label asks for, so it is only
    reported and a strict job still prints, as for a configured stop position.
    """
    requirement = ctx.requirement(Feature.SENSOR_SELECT)
    if ctx.printer_state.media_from_label:
        ctx.findings.deviate(
            FindingCode.SENSOR_SELECT_UNSUPPORTED,
            f"choosing the media sensor: {requirement}",
            fallback="the printer keeps its configured sensor",
        )
        return
    ctx.findings.info(
        FindingCode.CONFIGURED_SENSOR_SELECT_UNSUPPORTED,
        f"configured media {media.value} not selected: choosing the media sensor: {requirement}; "
        "the printer keeps its configured sensor",
    )


def _start_of_print(ctx: RenderContext) -> list[bytes]:
    """Return ``<STX>O``, sent exactly as configured, without the printer's scale.

    The default is the manual's own inch figure, and a configured value is what a test on the
    printer settled on.
    """
    mm = ctx.printer_state.start_of_print_mm
    if not _sends(PrinterSetting.START_OF_PRINT, ctx) or mm is None:
        return []
    return [_system(f"O{ctx.nominal_inch_hundredths(mm):04d}")]


#: How far below the maximum label length a label must end: within 0.1 in (2.54 mm) the printer
#: declares a paper fault before it finds the next label edge (manual p.30).
MAX_LABEL_LENGTH_MARGIN_MM = 2.54


def _max_label_length(label: Label, ctx: RenderContext) -> list[bytes]:
    """Return ``<STX>M``, sent as configured, unless the label is too long for it.

    A label on gap or reflective media whose height reaches within the margin of the value would
    paper-fault on every label, so the job sends none and the printer keeps its own value. The
    height is compared with the length the four digits send, not the configured millimetres they
    round from, and in thousandths of a millimetre, the precision the manual's figures carry, so a
    label exactly the margin below faults as the manual says.
    """
    state = ctx.printer_state
    mm = state.max_label_length_mm
    if not _sends(PrinterSetting.MAX_LABEL_LENGTH, ctx) or mm is None:
        return []
    hundredths = ctx.nominal_inch_hundredths(mm)
    sent = hundredths_to_mm(hundredths)
    height = None if label.height is None else to_inches(label.height, label.unit, dpi=ctx.profile.dpi) * MM_PER_INCH
    if state.media is MediaSensor.CONTINUOUS or height is None or round(height + MAX_LABEL_LENGTH_MARGIN_MM, 3) < sent:
        return [_system(f"M{hundredths:04d}")]
    source = (
        "configured max_label_length_mm"
        if PrinterSetting.MAX_LABEL_LENGTH in state.configured
        else f"the {ctx.profile.name} model default"
    )
    ctx.findings.deviate(
        FindingCode.MAX_LABEL_LENGTH_TOO_SHORT,
        f"the label height {height:g} mm is not at least {MAX_LABEL_LENGTH_MARGIN_MM:g} mm below the maximum "
        f"label length {sent:g} mm (M{hundredths:04d}, {source}); the printer would declare a paper fault "
        "(manual p.30)",
        fallback="no <STX>M sent; the printer keeps its own value",
    )
    return []


def _feed_rate(ctx: RenderContext) -> list[bytes]:
    """Return ``<STX>S``: the feed button's speed, kept within the model's slew range.

    A value outside the range is reported as info and replaced by the model's default: the feed
    rate belongs to the printer, not to what the label asks for, so a strict job still prints.
    """
    wanted = ctx.printer_state.feed_rate
    if not _sends(PrinterSetting.FEED_RATE, ctx) or wanted is None:
        return []
    allowed = ctx.profile.slew_speed
    if allowed.contains(wanted):
        return [_system(f"S{wanted.value}")]
    # The model's own default, not the nearest letter: the feed rate has no label-level meaning
    # to stay close to, and the default is what the printer would use with no setting at all.
    default = ctx.profile.feed_rate
    # profiles keeps a model's default inside its slew range, or None (the DMX 400).
    sent = [] if default is None else [_system(f"S{default.value}")]
    fallback = "no feed rate sent" if default is None else f"feed rate {default.value} sent"
    ctx.findings.info(
        FindingCode.FEED_RATE_OUT_OF_RANGE,
        f"feed rate {wanted.value} is outside {allowed.low.value}-{allowed.high.value} on {ctx.profile.name}; "
        f"{fallback}",
    )
    return sent


def _switch_value(ctx: RenderContext) -> tuple[int | None, str]:
    """Return the ``<STX>V`` value this job can build, or None and why it cannot.

    ``<STX>V`` overrides every bit until the next power-up (manual p.41), so it is built only when
    each bit of the model's layout has a known value: a guessed bit would switch an option off.
    Internal batch stays 0: datamax-dpl sends its formats over the interface and never uses the
    printer's internal batch mode.
    """
    bits = ctx.profile.software_switches
    if bits is None:
        return None, f"{ctx.profile.name} has no known bit layout (manual p.42)"
    if not bits.buildable:
        undescribed = ", ".join(f"bit {bit} {option}" for bit, option in bits.undescribed)
        return None, f"{ctx.profile.name} has bits no setting describes ({undescribed})"
    if ctx.cutter_installed is None:
        return None, "whether the cutter is installed is unknown"
    value = int(ctx.cutter_installed) << bits.cutter
    if bits.label_present is None:
        return value, ""
    present = ctx.printer_state.label_present
    if present is None:
        return None, "whether the label-present sensor is on is unknown"
    return value | int(present) << bits.label_present, ""


def _software_switches(ctx: RenderContext) -> list[bytes]:
    if not _sends(PrinterSetting.SOFTWARE_SWITCHES, ctx):
        return []
    value, reason = _switch_value(ctx)
    if value is None:
        ctx.findings.info(FindingCode.SOFTWARE_SWITCHES_NOT_SENT, f"software switches (<STX>V) not sent: {reason}")
        return []
    return [_system(f"V{value:X}")]


#: The settings whose value can be unknown for a model, each with the option that sets it.
_SETTING_KEYS: tuple[tuple[PrinterSetting, str, str], ...] = (
    (PrinterSetting.START_OF_PRINT, "start of print", "start_of_print_mm"),
    (PrinterSetting.MAX_LABEL_LENGTH, "maximum label length", "max_label_length_mm"),
    (PrinterSetting.FEED_RATE, "feed rate", "feed_rate"),
)


def _report_unknown_values(ctx: RenderContext) -> None:
    """Name, once, the settings the job would send but has no value for, and the key that sets each.

    The model's default is unknown there (a DIP switch decides the Prodigy Max's start of print;
    the synthetic models share none), so the printer keeps whatever another program left.
    """
    state = ctx.printer_state
    values = {
        PrinterSetting.START_OF_PRINT: state.start_of_print_mm,
        PrinterSetting.MAX_LABEL_LENGTH: state.max_label_length_mm,
        PrinterSetting.FEED_RATE: state.feed_rate,
    }
    unknown = [
        f"{what} (set {key})"
        for setting, what, key in _SETTING_KEYS
        if _sends(setting, ctx) and values[setting] is None
    ]
    if unknown:
        ctx.findings.info(
            FindingCode.PRINTER_SETTINGS_UNKNOWN,
            f"not sent, {ctx.profile.name} has no known value: {', '.join(unknown)}; the printer keeps its own",
        )


def _report_label_settings_left_out(label: Label, ctx: RenderContext) -> None:
    """Report each setting the label asks for that ``send_settings`` keeps the job from sending."""
    asked: list[tuple[PrinterSetting, str, object]] = [
        (PrinterSetting.MEDIA, "media", label.media.value if label.media is not None else None),
        (PrinterSetting.STOP, "stop_position", None if label.stop_position == PRINTER_STOP else label.stop_position),
        (PrinterSetting.STOP, "present_distance", label.present_distance),
    ]
    for setting, name, value in asked:
        if value is not None and not _sends(setting, ctx):
            ctx.findings.info(
                FindingCode.LABEL_SETTING_NOT_SENT,
                f"the label's {name} {value} is not sent: send_settings leaves out {setting.value}",
            )


def _settings(label: Label, ctx: RenderContext) -> list[bytes]:
    """Return the persistent printer settings this label's job sends, each only where it is in ``send``."""
    _report_label_settings_left_out(label, ctx)
    _report_unknown_values(ctx)
    stop = _present_commands(label, ctx) if _sends(PrinterSetting.STOP, ctx) else []
    return (
        _media_commands(label, ctx)
        + _start_of_print(ctx)
        + _max_label_length(label, ctx)
        + _feed_rate(ctx)
        + _software_switches(ctx)
        + stop
    )


def _present_distance_is_a_stop(ctx: RenderContext) -> bool:
    """Return True when this model can send a present distance only as a stop position."""
    return not ctx.supports(Feature.PRESENT_DISTANCE) and ctx.supports(Feature.STOP_POSITION)


def _label_sets_stop(label: Label, ctx: RenderContext) -> bool:
    """Return True when the label stops itself: its ``stop_position``, or a present distance sent as one."""
    return label.stop_position is not None or (label.present_distance is not None and _present_distance_is_a_stop(ctx))


def stops_at_tear_off(label: Label, ctx: RenderContext) -> bool:
    """Return True when the job stops at the configured tear-off position.

    The label's own stop wins over any configured one (its ``"printer"`` too, which sends none, and
    a present distance this model can only send as a stop position), and a job that cuts stops
    where the cutter needs it (``cutter_stop_position_mm``), never at the tear-off position.
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
    printer's scale or offset: the Allegro 2's 66.8 mm is what ``<STX>f263`` stopped at, and a
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
    if label.stop_position == PRINTER_STOP:
        # The label keeps the printer's own stop, so nothing is sent: not the configured stop it
        # wins over, and no deviation on a model without the command, which does exactly that.
        commands: list[bytes] = []
    elif label.stop_position is not None:
        commands = _label_stop(label.stop_position, ctx)
    else:
        commands = _configured_stop(label, ctx)
    if label.present_distance is not None:
        commands += _present_distance(label.present_distance, ctx)
    return commands


def _present_distance(value: float, ctx: RenderContext) -> list[bytes]:
    if ctx.supports(Feature.PRESENT_DISTANCE):
        hundredths = ctx.inch_hundredths(value, axis=Axis.ALONG)
        if hundredths > MAX_PRESENT_DISTANCE:
            ctx.findings.refuse(
                FindingCode.PRESENT_DISTANCE_TOO_LARGE,
                f"present distance takes at most {MAX_PRESENT_DISTANCE}/100 in",
            )
            return []
        return [_system(f"Kf{hundredths:04d}")]
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
    """Return the system commands that go before ``<STX>L`` (without the clears and image downloads)."""
    commands = _settings(label, ctx) + _pause_command(label, ctx)
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
    # A transparent label is emulated where it can be, and reported per overlap instead.
    elif label.attribute is not None and not emulates_transparency(label, ctx):
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
    """Return the ``yS`` line: the label's symbol set, else the one its scalable text was encoded for.

    A symbol set stays selected until another is (manual p.48), so scalable text encoded for the
    assumed default (``PC``, see :mod:`~datamax_dpl.domain.charset`) states it: a set another
    program left behind would otherwise print other characters for the same bytes.
    """
    if label.symbol_set is None and ctx.supports(Feature.SCALABLE_FONTS):
        scalable = any(element.type == "scalable_text" for element in label.elements)
        return [line(f"yS{DEFAULT_SYMBOL_SET}")] if scalable else []
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


def _clear_module_command(ctx: RenderContext) -> bytes | None:
    """Return ``<STX>q`` for the module this job's images go to, or None where it is not sent.

    It is not sent where the model has no ``<STX>q``, the job has no module, or the module is not
    one of the model's built-in RAM modules (:attr:`RenderContext.clears_module`).
    """
    if not ctx.clears_module or ctx.image_module is None:
        return None
    return _system(f"q{ctx.image_module}")


def _report_skipped_clear(ctx: RenderContext) -> None:
    """Say once that the clears skip a module that may be flash, on a label that downloads an image.

    Each ``<STX>q`` wears a flash module (manual p.35), and a plug-in module may be one (Appendix
    L), so a model that has ``<STX>q`` sends it only to its built-in RAM, whatever the clear keys
    say. A label without downloads gets no finding, as for a model that can clear nothing.
    """
    state = ctx.printer_state
    if not ctx.downloads or not (state.clear_graphics_before_label or state.clear_graphics_after_label):
        return
    if not ctx.supports(Feature.CLEAR_MODULE) or ctx.image_module is None or ctx.clears_module:
        return
    ctx.findings.info(
        FindingCode.GRAPHICS_CLEAR_SKIPPED,
        f"image module {ctx.image_module} not cleared: it is not a built-in RAM module of {ctx.profile.name} "
        "(manual, Appendix L) and may be flash, which every clear (<STX>q) wears; no image name the printer is "
        "known to hold there is downloaded again",
    )


def _remove_image(name: str) -> bytes:
    # The one system command that ends in <CR>: the name is 1-8 characters (manual p.36). It is
    # always a content name (D and seven hex digits), so it is never Y or N, whose <STX>RY and
    # <STX>RN switch the ribbon saver instead (same page).
    return line(_system(f"R{name}"))


def _report_no_clear(ctx: RenderContext) -> None:
    """Say once that this model can clear no stored image, where that can matter.

    Only a label that downloads an image can be dropped for a name the printer already holds
    (the Allegro 2 prints nothing then, measured), so a label without downloads gets no finding.
    """
    state = ctx.printer_state
    if not ctx.downloads or not (state.clear_graphics_before_label or state.clear_graphics_after_label):
        return
    ctx.findings.info(
        FindingCode.GRAPHICS_CLEAR_UNSUPPORTED,
        f"image module not cleared: {ctx.profile.name} has neither clear module (<STX>q, which removes every "
        "file in the module: images, formats, fonts) nor remove image (<STX>R); a printer that already holds "
        "an image name this label downloads may not print it",
    )


def _report_held_names(ctx: RenderContext) -> None:
    """Report the downloads a label leaves out for a held name, and names that could not be read.

    Called only when no clear runs before the downloads. A name the printer listed is left out
    with ``image_name_held``: the format places the image the printer holds, which is what the
    measured Allegro 2 prints (a second download of a held name prints nothing). A name an earlier
    label of the batch downloaded is left out silently: the job sent that very picture itself,
    since a different one is named with the next free salt instead. With the printer's names
    unknown every other download is kept, and one ``image_names_unread`` says what a held name
    would do. A name the printer lists only in another letter case is another name
    (:meth:`HeldImages.holds`) and is downloaded with no finding. Every finding takes the
    configured severity, which the policy leaves alone.
    """
    held = ctx.held_images
    severity = ctx.held_image_severity
    module = ctx.image_module
    unread = False
    first: dict[str, ImageDownload] = {}
    for download in ctx.downloads:
        first.setdefault(download.name, download)
    for download in first.values():
        if download.name in held.sent:
            continue
        if held.listed is None:
            unread = True
        elif download.name in held.listed:
            ctx.findings.at(
                severity,
                FindingCode.IMAGE_NAME_HELD,
                f"{download.described} is not downloaded: the printer already holds that name in module {module}, "
                "and a second download of it prints nothing; the label prints the stored image",
                element=download.element,
            )
    if unread:
        ctx.findings.at(
            severity,
            FindingCode.IMAGE_NAMES_UNREAD,
            f"image names stored in module {module} were not read ({held.unread_because}): every image is "
            "downloaded, and a name the printer already holds prints nothing",
        )


def image_downloads(ctx: RenderContext) -> list[bytes]:
    """Return the label's clear and its image downloads, in the order they go before ``<STX>L``.

    A model with ``<STX>q`` clears the whole module the images go to on every label, images or
    not, so no image another program stored can be held under a name this job reuses; it removes
    EVERY file in that module, formats and fonts included, not only images. It clears only its
    built-in RAM (:func:`_report_skipped_clear`). The plain Prodigy has no ``<STX>q`` and removes
    each name it downloads right before that download. Where no clear runs before the downloads,
    the download of a name the printer holds is left out (:func:`_report_held_names`).

    Args:
        ctx: The job's context, after every field is encoded, so its downloads are complete.

    Returns:
        The commands between the printer settings and ``<STX>L``.
    """
    before = ctx.printer_state.clear_graphics_before_label
    clear = _clear_module_command(ctx)
    _report_skipped_clear(ctx)
    if clear is not None and before:
        return [clear] + [download.command for download in ctx.downloads]
    removes = ctx.supports(Feature.REMOVE_IMAGE)
    if not removes and not ctx.supports(Feature.CLEAR_MODULE):
        _report_no_clear(ctx)
    if removes and before:
        return [part for download in ctx.downloads for part in (_remove_image(download.name), download.command)]
    if ctx.downloads:
        _report_held_names(ctx)
    return [download.command for download in ctx.sent_downloads]


def closing_clears(ctx: RenderContext) -> list[bytes]:
    """Return the clear after the label: the last of its bytes, after the format and any cut.

    The next job then starts from an empty module whatever program sends it: ``<STX>q`` removes
    every file in it, images, formats and fonts alike. The plain Prodigy removes every name this
    label downloaded, and only those: it has no way to clear the rest.

    Args:
        ctx: The job's context, after every field is encoded, so its downloads are complete.

    Returns:
        The commands that end the label's bytes; empty when the clear is off or impossible.
    """
    if not ctx.printer_state.clear_graphics_after_label:
        return []
    clear = _clear_module_command(ctx)
    if clear is not None:
        return [clear]
    if not ctx.supports(Feature.REMOVE_IMAGE):
        return []
    return [_remove_image(name) for name in _removed_after(ctx)]


def _removed_after(ctx: RenderContext) -> list[str]:
    """The names the Prodigy's clear after removes: each one the label's job downloads, and only those.

    A held name the job left out is the printer's own image, which this label did not put there.
    """
    return list(dict.fromkeys(download.name for download in ctx.sent_downloads))


def held_after(ctx: RenderContext) -> HeldImages:
    """Return what the printer holds in the image module once this label's job has run.

    For the next label of a batch: a clear (``<STX>q``) after the label empties the module; one
    before it leaves exactly the label's downloads; the Prodigy's removes take out the names the
    label downloaded; with no clear the label's downloads join what was held.

    Args:
        ctx: The job's context, after :func:`image_downloads` and :func:`closing_clears`.

    Returns:
        The names known to be held after the job, and whether the printer's own were read.
    """
    state = ctx.printer_state
    if ctx.clears_module and state.clear_graphics_after_label:
        return EMPTY_MODULE
    sent = {download.name: download.command for download in ctx.sent_downloads}
    if ctx.module_cleared_before:
        return HeldImages(listed=frozenset(), sent=sent)
    held = ctx.held_images
    if not (ctx.supports(Feature.REMOVE_IMAGE) and state.clear_graphics_after_label):
        return HeldImages(listed=held.listed, unread_because=held.unread_because, sent={**held.sent, **sent})
    # The removes name only what the job downloaded. That leaves out every listed name, except
    # where the Prodigy's remove before runs: it downloads a listed name again, and its remove
    # after takes it out, so ``listed`` may still name it. No later label of the batch reads
    # ``listed`` then, since each one removes and downloads its own names itself.
    removed = frozenset(_removed_after(ctx))
    kept = {name: command for name, command in {**held.sent, **sent}.items() if name not in removed}
    return HeldImages(listed=held.listed, unread_because=held.unread_because, sent=kept)


__all__ = [
    "CUT_NOW",
    "INCH_MODE",
    "MAX_LABEL_LENGTH_MARGIN_MM",
    "MAX_PRESENT_DISTANCE",
    "MAX_STOP_POSITION",
    "MAX_STOP_POSITION_MM",
    "PRINTER_STOP",
    "closing_clears",
    "format_footer",
    "format_header",
    "held_after",
    "image_downloads",
    "stops_at_tear_off",
    "system_prefix",
]
