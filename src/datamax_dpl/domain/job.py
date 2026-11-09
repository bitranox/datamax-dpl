"""Job-level DPL: system commands before the format, format settings, quantity, cutting.

System commands that carry a distance are written in inch mode, preceded by ``<STX>n``, so the
job does not depend on whatever unit mode the printer was left in. For the same reason every
label sets the persistent printer state it relies on (media, start of print, maximum label
length, feed rate, software switches, stop position) and clears the image module around itself:
another program or the front panel may have changed any of them since the last job.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Generic, TypeVar

from .charset import DEFAULT_SYMBOL_SET
from .enums import FindingCode, MediaSensor, PrinterSetting, PrinterStop, SpeedCode
from .label import DotSize, ScalableText
from .profiles import Feature, SpeedRange
from .records import STX, line
from .render_context import option_name
from .transparency import emulates_transparency
from .units import MM_PER_INCH, Axis, DplUnit, hundredths_to_mm, to_inches

if TYPE_CHECKING:
    from .label import Label
    from .render_context import ImageDownload, PrinterState, RenderContext

INCH_MODE = STX + b"n"
CUT_NOW = STX + b"o"
#: Largest stop position ``<STX>f`` takes: three digits of hundredths of an inch.
MAX_STOP_POSITION = 999
#: The same limit in millimetres (253.746), for a stop position configured in mm.
MAX_STOP_POSITION_MM = hundredths_to_mm(MAX_STOP_POSITION)
#: Largest present distance ``<STX>Kf`` takes: four digits (manual page 53), hundredths of an inch
#: because the job's system commands go out in inch mode.
MAX_PRESENT_DISTANCE = 9999


class _SpeedCommand(str, Enum):
    """The format command letter that sets one of the three speeds; a finding quotes it too."""

    PRINT = "P"
    SLEW = "S"
    BACKUP = "p"


_T = TypeVar("_T")


@dataclass(frozen=True)
class _Wanted(Generic[_T]):
    """A label setting a job sends: the label's own, or the one the options set where the label sets none.

    Attributes:
        value: The setting.
        source: Empty for the label's own; `` (RenderOptions.<field>)`` for a configured one, so a
            finding about it names the option (and on the command line, its key).
    """

    value: _T
    source: str


def _wanted(own: _T | None, configured: _T | None, *, option: str) -> _Wanted[_T] | None:
    """Return the label's own setting, else the configured one, else None: nothing is sent."""
    if own is not None:
        return _Wanted(own, "")
    if configured is not None:
        return _Wanted(configured, f" ({option_name(option)})")
    return None


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
        f"configured {option_name('max_label_length_mm')}"
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
        f"feed rate {wanted.value} ({option_name('feed_rate')}) is outside "
        f"{allowed.low.value}-{allowed.high.value} on {ctx.profile.name}; {fallback}",
    )
    return sent


@dataclass(frozen=True)
class _SwitchValue:
    """The ``<STX>V`` value a job can build, or None and why it cannot."""

    value: int | None
    reason: str = ""


def _switch_value(ctx: RenderContext, *, cutter_on: bool) -> _SwitchValue:
    """Return the ``<STX>V`` value this job can build, or None and why it cannot.

    ``<STX>V`` overrides every bit until the next power-up (manual p.41), so it is built only when
    each bit of the model's layout has a known value: a guessed bit would switch an option off.
    The cutter bit is on only for a label that cuts: an enabled cutter cuts every label, so a
    label without a cut turns it off (measured 2026-11-06 on one Allegro 2: V1 cut every label at
    the tear-off stop, V0 none). Internal batch stays 0: datamax-dpl sends its formats over the
    interface and never uses the printer's internal batch mode.
    """
    bits = ctx.profile.software_switches
    if bits is None:
        return _SwitchValue(None, f"{ctx.profile.name} has no known bit layout (manual p.42)")
    if not bits.buildable:
        undescribed = ", ".join(f"bit {bit.bit} {bit.option}" for bit in bits.undescribed)
        return _SwitchValue(None, f"{ctx.profile.name} has bits no setting describes ({undescribed})")
    if ctx.cutter_installed is None:
        return _SwitchValue(None, f"whether the cutter is installed is unknown (set {option_name('cutter_installed')})")
    value = int(ctx.cutter_installed and cutter_on) << bits.cutter
    if bits.label_present is None:
        return _SwitchValue(value)
    present = ctx.printer_state.label_present
    if present is None:
        return _SwitchValue(
            None, f"whether the label-present sensor is on is unknown (set {option_name('label_present')})"
        )
    return _SwitchValue(value | int(present) << bits.label_present)


def sends_cutter_bit(ctx: RenderContext) -> bool:
    """Return True when a job for ``ctx`` turns the cutter on or off itself with ``<STX>V``."""
    return _sends(PrinterSetting.SOFTWARE_SWITCHES, ctx) and _switch_value(ctx, cutter_on=False).value is not None


def cuts_after_print(ctx: RenderContext) -> bool:
    """Return True when a print for ``ctx`` is cut after its last label (``cut_after_print``).

    Only where the cutter is known to be installed and the job sets it itself: such a printer stops
    the stock at the cutter, where it cannot be torn off (user, 2026-11-09), and the job can turn
    the cutter on for the last label alone. Elsewhere the printer's own cutter setting stands.
    """
    return ctx.configured.cut_after_print and ctx.cutter_installed is True and sends_cutter_bit(ctx)


def report_end_cut_kept(ctx: RenderContext, *, what: str) -> None:
    """Report that a cut's ``at_end`` false is overridden: the end of the print ``what`` names is cut anyway."""
    ctx.findings.info(
        FindingCode.CUT_AT_END_KEPT,
        f"{what} is cut after its last label although its cut sets at_end false: "
        f"{option_name('cut_after_print')} cuts the end of every print on a printer with a cutter",
    )


def software_switches(label: Label, ctx: RenderContext) -> list[bytes]:
    """Return the ``<STX>V`` a job for ``label`` sends; none, and a finding saying why, where it cannot be built."""
    if not _sends(PrinterSetting.SOFTWARE_SWITCHES, ctx):
        return []
    switches = _switch_value(ctx, cutter_on=label.cut is not None and label.cut.cuts_this_label)
    if switches.value is None:
        ctx.findings.info(
            FindingCode.SOFTWARE_SWITCHES_NOT_SENT, f"software switches (<STX>V) not sent: {switches.reason}"
        )
        return []
    return [_system(f"V{switches.value:X}")]


@dataclass(frozen=True)
class _ValuedSetting:
    """A setting whose value can be unknown for a model, with the settings-file key that sets it.

    Attributes:
        setting: The setting.
        what: Its name in the finding.
        key: The render option that sets it, which a caller of the command line reads with the
            settings key of the printer section in use added (``with_setting_keys``).
        value: The value the job would send; None where the model has no known default.
    """

    setting: PrinterSetting
    what: str
    key: str
    value: float | SpeedCode | None


def _valued_settings(state: PrinterState) -> tuple[_ValuedSetting, ...]:
    """The settings whose value can be unknown for a model, each with its value in ``state``."""
    return (
        _ValuedSetting(
            PrinterSetting.START_OF_PRINT, "start of print", option_name("start_of_print_mm"), state.start_of_print_mm
        ),
        _ValuedSetting(
            PrinterSetting.MAX_LABEL_LENGTH,
            "maximum label length",
            option_name("max_label_length_mm"),
            state.max_label_length_mm,
        ),
        _ValuedSetting(PrinterSetting.FEED_RATE, "feed rate", option_name("feed_rate"), state.feed_rate),
    )


def _report_unknown_values(ctx: RenderContext) -> None:
    """Name, once, the settings the job would send but has no value for, and the key that sets each.

    The model's default is unknown there (a DIP switch decides the Prodigy Max's start of print;
    the synthetic models share none), so the printer keeps whatever another program left.
    """
    unknown = [
        f"{valued.what} (set {valued.key})"
        for valued in _valued_settings(ctx.printer_state)
        if _sends(valued.setting, ctx) and valued.value is None
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
        (
            PrinterSetting.STOP,
            "stop_position",
            None if label.stop_position is PrinterStop.PRINTER else label.stop_position,
        ),
        (PrinterSetting.STOP, "present_distance", label.present_distance),
    ]
    configured = ctx.configured.present_distance_mm
    if label.present_distance is None and configured is not None and not _sends(PrinterSetting.STOP, ctx):
        ctx.findings.info(
            FindingCode.LABEL_SETTING_NOT_SENT,
            f"the configured present distance {configured:g} mm ({option_name('present_distance_mm')}) is not "
            f"sent: {option_name('send_settings')} leaves out {PrinterSetting.STOP.value}",
        )
    for setting, name, value in asked:
        if value is not None and not _sends(setting, ctx):
            ctx.findings.info(
                FindingCode.LABEL_SETTING_NOT_SENT,
                f"the label's {name} {value} is not sent: {option_name('send_settings')} leaves out {setting.value}",
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
        + software_switches(label, ctx)
        + stop
    )


def _present_distance_is_a_stop(ctx: RenderContext) -> bool:
    """Return True when this model can send a present distance only as a stop position."""
    return not ctx.supports(Feature.PRESENT_DISTANCE) and ctx.supports(Feature.STOP_POSITION)


def _label_sets_stop(label: Label, ctx: RenderContext) -> bool:
    """Return True when the label stops itself: its ``stop_position``, or its present distance sent as one.

    Only the label's own present distance counts: a configured one goes out only as ``<STX>Kf``
    (see :func:`_present_hundredths`), so it never takes the place of a stop.
    """
    return label.stop_position is not None or (label.present_distance is not None and _present_distance_is_a_stop(ctx))


def stops_at_tear_off(label: Label, ctx: RenderContext) -> bool:
    """Return True when the job stops at the configured tear-off position.

    The label's own stop wins over any configured one (its ``"printer"`` too, which sends none, and
    a present distance this model can only send as a stop position), and a job that cuts stops
    where the cutter needs it (``cutter_stop_position_mm``), never at the tear-off position.
    """
    return not _label_sets_stop(label, ctx) and label.cut is None


def stops_at_cutter(label: Label, ctx: RenderContext) -> bool:
    """Return True when the job stops at the configured cutter position: it cuts, and the label sets no stop."""
    return not _label_sets_stop(label, ctx) and label.cut is not None


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
    if label.stop_position is PrinterStop.PRINTER:
        # The label keeps the printer's own stop, so nothing is sent: not the configured stop it
        # wins over, and no deviation on a model without the command, which does exactly that.
        commands: list[bytes] = []
    elif label.stop_position is not None:
        commands = _label_stop(label.stop_position, ctx)
    else:
        commands = _configured_stop(label, ctx)
    wanted = _present_hundredths(label, ctx)
    if wanted is not None:
        commands += _present_distance(wanted, ctx)
    return commands


def _present_hundredths(label: Label, ctx: RenderContext) -> _Wanted[int] | None:
    """Return the present distance the job sends in 1/100 in: the label's, else the configured mm.

    A configured one goes out only on a model with ``<STX>Kf``. Elsewhere a present distance can
    only be sent as the stop position, which would override the label's own stop and the cutter
    stop with a value set for every job, so it is not sent and an info finding names the keys that
    set that stop (user decision 2026-11-03).
    """
    if label.present_distance is not None:
        return _Wanted(ctx.inch_hundredths(label.present_distance, axis=Axis.ALONG), "")
    configured = ctx.configured.present_distance_mm
    if configured is None:
        return None
    if not ctx.supports(Feature.PRESENT_DISTANCE):
        _report_configured_present_distance(configured, ctx)
        return None
    hundredths = ctx.configured_inch_hundredths(configured, axis=Axis.ALONG)
    return _Wanted(hundredths, f" ({option_name('present_distance_mm')})")


def _report_configured_present_distance(configured: float, ctx: RenderContext) -> None:
    """Say that a configured present distance was not sent, and on a model with a stop, what sets it."""
    message = (
        f"configured present distance {configured:g} mm ({option_name('present_distance_mm')}) not sent: "
        f"{ctx.requirement(Feature.PRESENT_DISTANCE)}"
    )
    if ctx.supports(Feature.STOP_POSITION):
        message += (
            f"; the label stops where {option_name('stop_position_mm')} says, or "
            f"{option_name('cutter_stop_position_mm')} for a label that cuts"
        )
    ctx.findings.info(FindingCode.CONFIGURED_PRESENT_DISTANCE_UNSUPPORTED, message)


def _present_distance(wanted: _Wanted[int], ctx: RenderContext) -> list[bytes]:
    hundredths = wanted.value
    if ctx.supports(Feature.PRESENT_DISTANCE):
        if hundredths > MAX_PRESENT_DISTANCE:
            ctx.findings.refuse(
                FindingCode.PRESENT_DISTANCE_TOO_LARGE,
                f"present distance{wanted.source} takes at most {MAX_PRESENT_DISTANCE}/100 in",
            )
            return []
        return [_system(f"Kf{hundredths:04d}")]
    if _present_distance_is_a_stop(ctx):
        ctx.findings.deviate(
            FindingCode.PRESENT_DISTANCE_UNSUPPORTED,
            f"present distance{wanted.source}: {ctx.requirement(Feature.PRESENT_DISTANCE)}",
            fallback="sent as stop position (measured from the sensor, not from the next label)",
        )
        return _stop_command(hundredths, ctx)
    ctx.findings.deviate(
        FindingCode.PRESENT_DISTANCE_UNSUPPORTED,
        f"present distance{wanted.source}: {ctx.requirement(Feature.PRESENT_DISTANCE)}",
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


def feed_settings(label: Label, ctx: RenderContext) -> list[bytes]:
    """Return the media, software switch and stop commands a job for ``label`` sends, in inch mode.

    The same commands and findings a label's job gives for its media sensor, its cutter and its
    stop position, for a command that feeds a label of its own (``feed --media``): a label that
    does not cut turns an installed cutter off, so the fed label is not cut. The media command
    goes out only where ``ctx``'s printer state sends it, as in a job.
    """
    return [INCH_MODE, *_media_commands(label, ctx), *software_switches(label, ctx), *_present_commands(label, ctx)]


def system_prefix(label: Label, ctx: RenderContext) -> list[bytes]:
    """Return the system commands that go before ``<STX>L`` (without the clears and image downloads)."""
    commands = _settings(label, ctx) + _pause_command(label, ctx)
    needs_inch = bool(commands) or not ctx.supports(Feature.FORMAT_METRIC)
    return ([INCH_MODE] if needs_inch else []) + commands


def _speed_line(
    command: _SpeedCommand, wanted: _Wanted[SpeedCode] | None, allowed: SpeedRange | None, ctx: RenderContext
) -> list[bytes]:
    if wanted is None:
        return []
    if allowed is None:
        ctx.findings.deviate(
            FindingCode.SPEED_UNSUPPORTED,
            f"{ctx.profile.name} has no setting for '{command.value}'{wanted.source}",
            fallback="printer default kept",
        )
        return []
    speed = wanted.value
    if not allowed.contains(speed):
        clamped = allowed.clamp(speed)
        ctx.findings.deviate(
            FindingCode.SPEED_OUT_OF_RANGE,
            f"speed {speed.value}{wanted.source} is outside {allowed.low.value}-{allowed.high.value} "
            f"on {ctx.profile.name}",
            fallback=f"speed {clamped.value}",
        )
        speed = clamped
    return [line(f"{command.value}{speed.value}")]


def _heat_line(label: Label, ctx: RenderContext) -> list[bytes]:
    wanted = _wanted(label.heat, ctx.configured.heat, option="heat")
    if wanted is None:
        return []
    heat = wanted.value
    if heat > ctx.profile.heat_max:
        ctx.findings.deviate(
            FindingCode.HEAT_OUT_OF_RANGE,
            f"heat {heat}{wanted.source} is above the {ctx.profile.name} maximum {ctx.profile.heat_max}",
            fallback=f"heat {ctx.profile.heat_max}",
        )
        heat = ctx.profile.heat_max
    return [line(f"H{heat:02d}")]


def _speed_lines(label: Label, ctx: RenderContext) -> list[bytes]:
    backup = ctx.profile.backup_speed if ctx.supports(Feature.BACKUP_SPEED) else None
    configured = ctx.configured
    return (
        _speed_line(
            _SpeedCommand.PRINT,
            _wanted(label.print_speed, configured.print_speed, option="print_speed"),
            ctx.profile.print_speed,
            ctx,
        )
        + _speed_line(
            _SpeedCommand.SLEW,
            _wanted(label.slew_speed, configured.slew_speed, option="slew_speed"),
            ctx.profile.slew_speed,
            ctx,
        )
        + _speed_line(
            _SpeedCommand.BACKUP,
            _wanted(label.backup_speed, configured.backup_speed, option="backup_speed"),
            backup,
            ctx,
        )
    )


@dataclass(frozen=True)
class _Offset:
    """One format offset: its command letter, its axis, its name in a finding, and the option that sets it."""

    letter: str
    axis: Axis
    what: str
    option: str


_COLUMN = _Offset("C", Axis.ACROSS, "column offset", "column_offset_mm")
_ROW = _Offset("R", Axis.ALONG, "row offset", "row_offset_mm")


def _offset_line(offset: _Offset, *, own: float | None, configured: float | None, ctx: RenderContext) -> list[bytes]:
    """Return the offset's line: the label's own, in its unit; else the configured mm; else none."""
    if own is not None:
        return [line(f"{offset.letter}{ctx.distance(own, axis=offset.axis, element=None, what=offset.what):04d}")]
    if configured is None:
        return []
    what = f"{offset.what} ({option_name(offset.option)})"
    return [line(f"{offset.letter}{ctx.configured_distance(configured, axis=offset.axis, what=what):04d}")]


def _offset_lines(label: Label, ctx: RenderContext) -> list[bytes]:
    return _offset_line(
        _COLUMN, own=label.column_offset, configured=ctx.configured.column_offset_mm, ctx=ctx
    ) + _offset_line(_ROW, own=label.row_offset, configured=ctx.configured.row_offset_mm, ctx=ctx)


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
        scalable = any(isinstance(element, ScalableText) for element in label.elements)
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
    # The manual (p.64) asks for an amount smaller than the quantity, but an Allegro 2 cut once,
    # after the last copy, for ":0003" with Q0003 (2026-11-06); the user chose to send it on every
    # model with the command. Past the quantity it is the quantity: one cut after the last copy.
    amount = min(cut.every, label.quantity)
    if ctx.supports(Feature.CUT_BY):
        return [line(f":{amount:04d}")]
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
    if ctx.profile.default_dot_size != DotSize(width=1, height=1):
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
    if cut is None or cut.every == 1 or cut.every >= label.quantity:
        return []
    if label.quantity % cut.every == 0:
        return []
    if not cut.at_end:
        if not cuts_after_print(ctx):
            return []
        report_end_cut_kept(ctx, what="the label")
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
    return _system(f"q{ctx.image_module.value}")


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
        f"image module {ctx.image_module.value} not cleared: it is not a built-in RAM module of {ctx.profile.name} "
        "(manual, Appendix L) and may be flash, which every clear (<STX>q) wears; no image name the printer is "
        "known to hold there is downloaded again",
    )


def _remove_image(name: str) -> bytes:
    # The one system command that ends in <CR>: the name is 1-8 characters (manual p.36). It is
    # always a content name (D and seven base-62 digits), so it is never Y or N, whose <STX>RY and
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
    measured Allegro 2 prints (a second download of a held name prints nothing). That holds for a
    name an earlier label of the same batch downloaded too: nothing is remembered between labels,
    so the listing is all a label goes by. A name the label's own upload check sent in an earlier
    round (``HeldImages.uploaded``) is left out silently: it holds the label's own picture. With
    the printer's names unknown every download is
    kept, and one ``image_names_unread`` says what a held name would do; nothing is said where the
    names are read right before the downloads (``HeldImages.read_at_send``), since the label is
    rendered again with that read. A name the printer lists only in another letter case is
    another name (:meth:`HeldImages.holds`) and is downloaded with no finding. Every finding takes
    the configured severity, which the policy leaves alone.
    """
    module = ctx.image_module
    if module is None:
        # A job without a module queues no download (image_module_unknown refuses it): nothing to report.
        return
    held = ctx.held_images
    severity = ctx.held_image_severity
    unread = False
    first: dict[str, ImageDownload] = {}
    for download in ctx.downloads:
        first.setdefault(download.name, download)
    for download in first.values():
        if download.name in held.uploaded:
            continue
        if held.listed is None:
            unread = not held.read_at_send
        elif download.name in held.listed:
            ctx.findings.at(
                severity,
                FindingCode.IMAGE_NAME_HELD,
                f"{download.described} is not downloaded: the printer already holds that name in module "
                f"{module.value}, and a second download of it prints nothing; the label prints the stored image",
                element=download.element,
            )
    if unread:
        ctx.findings.at(
            severity,
            FindingCode.IMAGE_NAMES_UNREAD,
            f"image names stored in module {module.value} were not read ({held.unread_because}): every image is "
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


def retry_clear(ctx: RenderContext) -> list[bytes]:
    """Return the clear that goes before the label's images are uploaded again, where one may go.

    A caller that sent the downloads and found a name missing from the printer's directory (a
    download into a full module is lost with no error, one Allegro 2, 2026-10-31) clears the
    module before it uploads them again: ``<STX>q`` where the module is the model's built-in RAM,
    the plain Prodigy's ``<STX>R`` for each name the label downloads, nothing on a plug-in module
    (which may be flash, worn by every ``<STX>q``) or on a model that can clear nothing. It does
    not depend on the clear keys: the keys say what every label sends, this is a repair after a
    loss.

    Args:
        ctx: The job's context, after every field is encoded, so its downloads are complete.

    Returns:
        The clear's commands; empty where no clear may go.
    """
    clear = _clear_module_command(ctx)
    if clear is not None:
        return [clear]
    if not ctx.supports(Feature.REMOVE_IMAGE):
        return []
    return [_remove_image(name) for name in _removed_after(ctx)]


def retry_clear_may_go(ctx: RenderContext) -> bool:
    """Return True where a clear may go before the label's images are uploaded again.

    :func:`retry_clear` gives what that clear sends, and the plain Prodigy's removes name only the
    label's own downloads: rendered again with nothing to download, a label there sends no clear
    while one still may go. This says what the module allows, whatever the label downloads.

    Args:
        ctx: The job's context.

    Returns:
        True for ``<STX>q`` to the model's built-in RAM or the Prodigy's ``<STX>R``; False on a
        plug-in module or a model that can clear nothing.
    """
    return ctx.clear_may_go


def _removed_after(ctx: RenderContext) -> list[str]:
    """The names the Prodigy's clear after removes: each one the label's job downloads, and only those.

    A held name the job left out is the printer's own image, which this label did not put there.
    """
    return list(dict.fromkeys(download.name for download in ctx.sent_downloads))


__all__ = [
    "CUT_NOW",
    "INCH_MODE",
    "MAX_LABEL_LENGTH_MARGIN_MM",
    "MAX_PRESENT_DISTANCE",
    "MAX_STOP_POSITION",
    "MAX_STOP_POSITION_MM",
    "closing_clears",
    "cuts_after_print",
    "feed_settings",
    "format_footer",
    "format_header",
    "image_downloads",
    "report_end_cut_kept",
    "retry_clear",
    "retry_clear_may_go",
    "sends_cutter_bit",
    "software_switches",
    "stops_at_cutter",
    "stops_at_tear_off",
    "system_prefix",
]
