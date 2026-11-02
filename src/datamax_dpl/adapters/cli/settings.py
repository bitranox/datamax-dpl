"""Printer and camera settings: the ``[printer]`` and ``[camera]`` sections plus command-line overrides.

Command-line flags win over every configuration layer. The models refuse unknown values here, at
the boundary, so a typo in a config file is reported as a configuration error with the key named.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any, TypedDict, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from ...application.upload_check import MAX_UPLOAD_CHECK_ROUNDS, MIN_UPLOAD_CHECK_ROUNDS, UPLOAD_CHECK_ROUNDS
from ...application.use_cases import RETRY_INTERVAL_SECONDS, STATUS_WAIT_SECONDS, STORED_NAMES_WAIT_SECONDS
from ...application.verify import ONE_WAY_SETTLE_SECONDS, POLL_SECONDS
from ...domain._invariants import require_update_fields
from ...domain.camera import CameraSettings, refuse_boolean
from ...domain.enums import (
    CutterPresence,
    FallbackPolicy,
    HardwareOption,
    ImageModule,
    PrinterStop,
    Severity,
    ValidationErrorType,
)
from ...domain.errors import ConfigurationError
from ...domain.job import MAX_STOP_POSITION_MM
from ...domain.label import MAX_LENGTH, stop_position_or_printer
from ...domain.preview import DEFAULT_PREVIEW_MAX_LENGTH_MM
from ...domain.profiles import Firmware, ParsedFirmware
from ...domain.render_context import RenderOptions
from ...domain.reset_label import (
    CALIBRATE_AFTER_RESET,
    CALIBRATION_LABEL_TEXT,
    RESET_CHECK_WAIT_SECONDS,
    characters_replaced,
    check_label_text,
)
from ...domain.timing import MAX_SEND_CHUNK_BYTES, MAX_TIMEOUT_SECONDS, SEND_CHUNK_BYTES, TransportTuning
from ...domain.units import MAX_OFFSET_MM, MAX_SCALE, MIN_SCALE
from ..config.sections import (
    CAMERA_SECTION,
    PREVIEW_SECTION,
    PRINTER_SECTION,
    ConfigSection,
    SectionPath,
    printer_section,
)
from ..transport.uri import is_printer_name, is_printer_uri

if TYPE_CHECKING:
    from lib_layered_config import Config
    from pydantic_core import ErrorDetails

    from ...domain.profiles import CalibrationKey

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_BoundedStop = Annotated[float, Field(gt=0, le=MAX_STOP_POSITION_MM)]
_ConfiguredStop = Annotated[_BoundedStop | PrinterStop, stop_position_or_printer(TypeAdapter(_BoundedStop))]


#: Default of ``[printer] max_batch_records``.
MAX_BATCH_RECORDS = 100_000
#: Largest ``max_batch_records`` taken: more records than a 16 MiB file can hold, so "no limit".
MAX_BATCH_RECORDS_LIMIT = 10_000_000
#: Default of ``[printer] max_batch_job_bytes``, 1 GiB: 100000 records of a one-field label come to
#: about 430 MB and must keep working at the default record limit.
MAX_BATCH_JOB_BYTES = 1_073_741_824
#: Largest ``max_batch_job_bytes`` taken, 1 TiB: past what any host here renders, so "no limit".
MAX_BATCH_JOB_BYTES_LIMIT = 1_099_511_627_776


class PrinterSettings(BaseModel):
    """The ``[printer]`` section, merged with a ``[printers.<name>]`` section when one is chosen.

    ``name`` is the chosen printer's name, empty when none was; ``label_width`` and
    ``label_height`` are the loaded labels' size in millimetres, when known. A scale, offset,
    stop position, image memory budget, image module or format length left unset (None) takes
    the model's default when the job renders.

    Example:
        >>> PrinterSettings().model
        'generic'
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = "generic"
    # Parsed here, so a malformed version is refused naming its key before any job is built.
    firmware: ParsedFirmware = None
    uri: str = ""
    policy: FallbackPolicy = FallbackPolicy.DEGRADE
    timeout: float = Field(default=10.0, gt=0, le=MAX_TIMEOUT_SECONDS)
    # How long status and memory keep trying for the first answer while a print server is still
    # busy with an earlier job (and verify waits for the printer to finish), and the pause between
    # two of their connections.
    # Bounded above too, so an infinite or oversized value is refused naming its key here rather
    # than as a nameless "wait" when status runs.
    status_wait: float = Field(default=STATUS_WAIT_SECONDS, gt=0, le=MAX_TIMEOUT_SECONDS)
    status_retry_interval: float = Field(default=RETRY_INTERVAL_SECONDS, gt=0, le=MAX_TIMEOUT_SECONDS)
    # How long print and verify keep trying to read the stored image names before a job no clear
    # precedes; its own key, so a link that never answers can wait less without shortening status.
    stored_names_wait: float = Field(default=STORED_NAMES_WAIT_SECONDS, gt=0, le=MAX_TIMEOUT_SECONDS)
    # How many rounds print and verify run before refusing a label whose images the printer did not
    # list after the upload: 1 reads once, 2 adds a clear and a second upload, 3 the images halved.
    upload_check_rounds: int = Field(
        default=UPLOAD_CHECK_ROUNDS, ge=MIN_UPLOAD_CHECK_ROUNDS, le=MAX_UPLOAD_CHECK_ROUNDS
    )
    # How long print and verify wait for the one status read before a job that asks for a reset
    # report: it runs before every job, so it has its own short wait and a printer that stays
    # silent costs only this. What a report triggers, and what that label says.
    reset_check_wait: float = Field(default=RESET_CHECK_WAIT_SECONDS, gt=0, le=MAX_TIMEOUT_SECONDS)
    calibrate_after_reset: bool = CALIBRATE_AFTER_RESET
    calibration_label_text: str = CALIBRATION_LABEL_TEXT
    # Most records one --data batch may hold: a batch is rendered whole before anything is sent.
    max_batch_records: int = Field(default=MAX_BATCH_RECORDS, gt=0, le=MAX_BATCH_RECORDS_LIMIT)
    # Most bytes the records' jobs may come to: the record limit bounds one factor, and a label of
    # hundreds of large fields multiplies it by more than a megabyte a record.
    max_batch_job_bytes: int = Field(default=MAX_BATCH_JOB_BYTES, gt=0, le=MAX_BATCH_JOB_BYTES_LIMIT)
    # verify waits status_wait in all for the printer to finish its job, polling at this interval;
    # a one-way channel cannot poll, so verify waits one_way_settle instead (0 waits not at all).
    verify_poll_interval: float = Field(default=POLL_SECONDS, gt=0, le=MAX_TIMEOUT_SECONDS)
    one_way_settle: float = Field(default=ONE_WAY_SETTLE_SECONDS, ge=0, le=MAX_TIMEOUT_SECONDS)
    # The largest piece of a job one write call takes: timeout bounds each piece (TransportTuning).
    # Bounded above as there, so a larger value is refused naming its key here.
    send_chunk_bytes: int = Field(default=SEND_CHUNK_BYTES, gt=0, le=MAX_SEND_CHUNK_BYTES)
    cutter: CutterPresence = CutterPresence.UNKNOWN
    options: frozenset[HardwareOption] = Field(default_factory=frozenset[HardwareOption])
    # Same range as RenderOptions, so a bad value is refused here with its config key named. None
    # is not 1.0 or 0.0: a neutral value set here wins over the model's measured default.
    scale_across: float | None = Field(default=None, ge=MIN_SCALE, le=MAX_SCALE)
    scale_along: float | None = Field(default=None, ge=MIN_SCALE, le=MAX_SCALE)
    offset_across: float | None = Field(default=None, ge=-MAX_OFFSET_MM, le=MAX_OFFSET_MM)
    offset_along: float | None = Field(default=None, ge=-MAX_OFFSET_MM, le=MAX_OFFSET_MM)
    # Bounded here, unlike RenderOptions, so a config typo is refused with its key named before any
    # job is built, rather than as a STOP_POSITION_TOO_LARGE finding on every label. "printer"
    # sends no stop and keeps the printer's own; the cutter stop has no such value, since unset
    # already sends none for a job that cuts.
    stop_position: _ConfiguredStop | None = None
    cutter_stop_position: float | None = Field(default=None, gt=0, le=MAX_STOP_POSITION_MM)
    # None is not "no budget": it takes the model's, which most models do not have.
    image_memory_bytes: int | None = Field(default=None, gt=0)
    # None is not module A: it takes the model's built-in module, E on a DMX 600/800 and none on a
    # Prodigy or Prodigy Plus, which refuses a job that downloads an image until this is set.
    image_module: ImageModule | None = None
    # None is not "no limit": it takes the model's, which only the Allegro 2 has so far.
    max_format_rows: int | None = Field(default=None, gt=0)
    # A label no clear precedes: a download left out for a held name, or names that could not be read.
    held_image_severity: Severity = Severity.WARNING
    name: str = ""
    label_width: float | None = Field(default=None, gt=0, le=MAX_LENGTH)
    label_height: float | None = Field(default=None, gt=0, le=MAX_LENGTH)

    _numbers_are_no_booleans = field_validator(
        "timeout",
        "status_wait",
        "status_retry_interval",
        "stored_names_wait",
        "reset_check_wait",
        "upload_check_rounds",
        "max_batch_records",
        "max_batch_job_bytes",
        "verify_poll_interval",
        "one_way_settle",
        "send_chunk_bytes",
        "scale_across",
        "scale_along",
        "offset_across",
        "offset_along",
        "stop_position",
        "cutter_stop_position",
        "image_memory_bytes",
        "max_format_rows",
        "label_width",
        "label_height",
        mode="before",
    )(refuse_boolean)

    @field_validator("calibration_label_text")
    @classmethod
    def _calibration_text_prints(cls, value: str) -> str:
        """Refuse a value no text field could hold here, naming the key rather than at a reset."""
        return check_label_text(value)

    @field_validator("firmware", mode="before")
    @classmethod
    def _firmware_as_text(cls, value: object) -> object:
        """Accept 2.01 as the layered config delivers an unquoted value: a number, read as its text.

        A float loses trailing zeros (2.10 arrives as 2.1), which ``parse_firmware`` reads the
        same way, so the version is unaffected. Runs before the field's own parse.
        """
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return value


class PrinterOverrides(BaseModel):
    """Command-line values that replace configured ones when given (None means not given)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str | None = None
    firmware: Firmware | None = None
    printer: str | None = None
    policy: FallbackPolicy | None = None
    timeout: float | None = None
    cutter: CutterPresence | None = None
    options: frozenset[HardwareOption] = Field(default_factory=frozenset[HardwareOption])


class _CommandLineValues(TypedDict):
    """The settings the command line can replace, each already merged with its configured value."""

    model: str
    firmware: Firmware | None
    uri: str
    policy: FallbackPolicy
    timeout: float
    status_wait: float
    cutter: CutterPresence
    options: frozenset[HardwareOption]


require_update_fields(_CommandLineValues, PrinterSettings)


def _table(section: object, *, path: str) -> Mapping[str, Any]:
    """Return ``section`` as a table, refusing any other value: dropping it would hide every key meant."""
    if isinstance(section, Mapping):
        return cast("Mapping[str, Any]", section)
    raise ConfigurationError(f"{path}: needs a table of settings, got {type(section).__name__}")


def _section(config: Config, name: ConfigSection) -> Mapping[str, Any]:
    return _table(config.get(name.value, {}), path=name.value)


def _describe(exc: ValidationError, *, section: SectionPath) -> str:
    return "; ".join(
        f"{section.key_path('.'.join(str(p) for p in error['loc']))}: {_reason(error)}" for error in exc.errors()
    )


def _reason(error: ErrorDetails) -> str:
    """The reason as its validator wrote it, without the ``Value error, `` pydantic puts in front."""
    cause = error.get("ctx", {}).get("error")
    if error["type"] == ValidationErrorType.VALUE_ERROR and isinstance(cause, ValueError):
        return str(cause)
    return error["msg"]


def printer_settings(config: Config, overrides: PrinterOverrides) -> PrinterSettings:
    """Merge the ``[printer]`` section with command-line overrides.

    Raises:
        ConfigurationError: A configured value is invalid; the message names the key.
    """
    name = overrides.printer if overrides.printer and not is_printer_uri(overrides.printer) else ""
    section = printer_section(name)
    try:
        base = PrinterSettings.model_validate(
            {**_section(config, ConfigSection.PRINTER), **_named(config, name), "name": name}
        )
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc, section=section)) from None
    _warn_of_replaced_characters(base, section=section)
    uri = overrides.printer if overrides.printer and is_printer_uri(overrides.printer) else None
    # model_copy, not a new PrinterSettings: the overrides are already typed, and re-validating
    # would move the refusal of a bad --timeout away from the transport, which names its range.
    return base.model_copy(
        update=_CommandLineValues(
            model=_given(overrides.model, base.model),
            firmware=_given(overrides.firmware, base.firmware),
            uri=_given(uri, base.uri),
            policy=_given(overrides.policy, base.policy),
            timeout=_given(overrides.timeout, base.timeout),
            # --timeout is the whole wait where one applies: status, memory and verify wait for it.
            status_wait=_given(overrides.timeout, base.status_wait),
            cutter=_given(overrides.cutter, base.cutter),
            # Options add up instead of replacing: --option names one more installed option.
            options=base.options | overrides.options,
        )
    )


def _warn_of_replaced_characters(settings: PrinterSettings, *, section: SectionPath) -> None:
    """Warn once the settings are read when the calibration text would not print as written.

    At settings time, since the label itself may not come out for months: a reset is rare, and its
    findings are the wrong place to first learn that a configured text prints as ``?``.
    """
    replaced = characters_replaced(settings.calibration_label_text)
    if replaced:
        logger.warning(
            "%s: %s print as '?', as in a text field in that font",
            section.key_path("calibration_label_text"),
            " ".join(f"U+{ord(char):04X}" for char in replaced),
        )


def check_printer_values(config: Config, *, printer: str, values: Mapping[CalibrationKey, float]) -> None:
    """Refuse values the next load of the configuration would refuse, before they are written.

    Args:
        config: The configuration the values join.
        printer: The printer's name for ``[printers.<name>]``, or "" for ``[printer]``.
        values: The values about to be written, each under the key it names.

    Raises:
        ConfigurationError: The section with these values in it does not validate; the message names the key.
    """
    written = {key.value: value for key, value in values.items()}
    try:
        PrinterSettings.model_validate(
            {**_section(config, ConfigSection.PRINTER), **_named(config, printer), **written, "name": printer}
        )
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc, section=printer_section(printer))) from None


def _named(config: Config, name: str) -> Mapping[str, Any]:
    """Return the ``[printers.<name>]`` section, or nothing when no name was chosen.

    Raises:
        ConfigurationError: A configured section has an invalid name, or none is called ``name``.
    """
    printers = _section(config, ConfigSection.PRINTERS)
    invalid = sorted(key for key in printers if not is_printer_name(key))
    if invalid:
        msg = (
            f"{SectionPath(ConfigSection.PRINTERS, invalid[0]).dotted}: a printer name takes letters, digits,"
            " '_' and '-' (not first), no ':'"
        )
        raise ConfigurationError(msg)
    if not name:
        return {}
    if name not in printers:
        configured = ", ".join(sorted(printers)) or "none"
        msg = f"no printer named '{name}' in {SectionPath(ConfigSection.PRINTERS).header}; configured: {configured}"
        raise ConfigurationError(msg)
    return _table(printers[name], path=SectionPath(ConfigSection.PRINTERS, name).dotted)


def _given(override: _T | None, configured: _T) -> _T:
    """Return the command-line value when one was given, else the configured one."""
    return configured if override is None else override


def camera_settings(config: Config) -> CameraSettings:
    """Read the ``[camera]`` section.

    Raises:
        ConfigurationError: A configured value is invalid; the message names the key.
    """
    try:
        return CameraSettings.model_validate(dict(_section(config, ConfigSection.CAMERA)))
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc, section=CAMERA_SECTION)) from None


class PreviewSettings(BaseModel):
    """The ``[preview]`` section.

    Example:
        >>> PreviewSettings().max_length_mm
        2000.0
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Finite and no longer than a label can be: a larger figure would read as "no limit", which is
    # what the key exists to prevent, and 1e308 overflows on its way to dots.
    max_length_mm: float = Field(default=DEFAULT_PREVIEW_MAX_LENGTH_MM, gt=0, le=MAX_LENGTH, allow_inf_nan=False)

    _numbers_are_no_booleans = field_validator("max_length_mm", mode="before")(refuse_boolean)


def preview_settings(config: Config) -> PreviewSettings:
    """Read the ``[preview]`` section.

    Raises:
        ConfigurationError: A configured value is invalid; the message names the key.
    """
    try:
        return PreviewSettings.model_validate(dict(_section(config, ConfigSection.PREVIEW)))
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc, section=PREVIEW_SECTION)) from None


_CUTTER: Mapping[CutterPresence, bool | None] = MappingProxyType(
    {
        CutterPresence.YES: True,
        CutterPresence.NO: False,
        CutterPresence.UNKNOWN: None,
    }
)


def render_options(settings: PrinterSettings, *, model_image_budget: bool = True) -> RenderOptions:
    """The render options a printer configuration implies.

    Args:
        settings: The printer configuration.
        model_image_budget: Whether the model's own image memory budget applies (see
            :class:`~datamax_dpl.domain.render_context.RenderOptions`).

    Example:
        >>> render_options(PrinterSettings(model="dmx-600", cutter=CutterPresence.YES)).cutter_installed
        True
    """
    return RenderOptions(
        model=settings.model,
        firmware=settings.firmware,
        policy=settings.policy,
        cutter_installed=_CUTTER[settings.cutter],
        options=settings.options,
        scale_across=settings.scale_across,
        scale_along=settings.scale_along,
        offset_across=settings.offset_across,
        offset_along=settings.offset_along,
        stop_position_mm=settings.stop_position,
        cutter_stop_position_mm=settings.cutter_stop_position,
        image_memory_bytes=settings.image_memory_bytes,
        image_module=settings.image_module,
        max_format_rows=settings.max_format_rows,
        held_image_severity=settings.held_image_severity,
        model_image_budget=model_image_budget,
    )


def transport_tuning(settings: PrinterSettings) -> TransportTuning:
    """How the transports pace their writes for this printer configuration.

    Example:
        >>> transport_tuning(PrinterSettings(send_chunk_bytes=512)).send_chunk_bytes
        512
    """
    return TransportTuning(send_chunk_bytes=settings.send_chunk_bytes)


def require_uri(settings: PrinterSettings) -> str:
    """Return the printer URI or raise when none is configured or given.

    Raises:
        ConfigurationError: Neither ``--printer`` nor a configured ``uri`` names a printer's address;
            the message names the section in effect.
    """
    if settings.uri:
        return settings.uri
    uri_key = PRINTER_SECTION.setting("uri")
    if settings.name:
        # A printer was given; a named section that sets no uri falls back to the [printer] one.
        own_key = printer_section(settings.name).setting("uri")
        raise ConfigurationError(f"printer {settings.name!r} has no uri: set {own_key}, or {uri_key} for every printer")
    raise ConfigurationError(f"no printer given: pass --printer NAME or URI, or set {uri_key}")


__all__ = [
    "PreviewSettings",
    "PrinterOverrides",
    "PrinterSettings",
    "camera_settings",
    "check_printer_values",
    "preview_settings",
    "printer_settings",
    "render_options",
    "require_uri",
    "transport_tuning",
]
