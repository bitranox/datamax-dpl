"""Turn a :class:`~datamax_dpl.domain.label.Label` into the DPL bytes for one printer model.

:func:`build_job` always returns a :class:`RenderResult` with every finding; :func:`render` does
the same but raises :class:`~datamax_dpl.domain.errors.LabelRejectedError` when any finding is an
error, so a caller that only wants bytes cannot print a label that broke a rule.

Byte layout of a job: system commands (unit mode, media, stop/present position, image
downloads), ``<STX>L``, format settings, one line per record, ``Q``, ``E``, then a final cut when
one is needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ConfigDict

from .encode_barcode import encode_barcode
from .encode_graphics import encode_box, encode_circle, encode_image, encode_line, encode_polygon
from .encode_text import encode_scalable_text, encode_text
from .enums import Attribute, FindingCode, Severity, Unit
from .errors import LabelRejectedError
from .findings import Finding, FindingCollector
from .job import format_footer, format_header, system_prefix
from .label import Barcode, Box, Circle, Image, Line, Polygon, ScalableText, Text
from .placeholders import label_placeholders
from .profiles import CalibrationDefaults, Feature, get_profile, parse_firmware
from .render_context import RenderContext, RenderOptions
from .units import Axis, Correction, DplUnit, to_inches

if TYPE_CHECKING:
    from .label import Element, Label

_RECORD_PREFIXES = tuple("1234")
_Default = TypeVar("_Default", float, "float | None")


class RenderResult(BaseModel):
    """The bytes for one label job and everything noticed while producing them.

    Attributes:
        data: The complete DPL job, ready to send.
        findings: Every finding, in order.
        model: Profile id the job was built for.
        dpl_unit: Distance unit the format uses.
        record_count: Number of field records in the format.
        downloads: Names of the images downloaded before the format.

    Example:
        >>> from datamax_dpl.domain.label import Label, Text
        >>> result = build_job(Label(elements=[Text(x=5, y=5, text="Hi")]), RenderOptions(model="dmx-600"))
        >>> result.ok, result.data.startswith(b"\\x02L")
        (True, True)
    """

    # A job is not UTF-8 (charset-mapped text, image records): pydantic's default JSON form for
    # bytes is a UTF-8 string and raises on the first umlaut, so the bytes travel as base64.
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64")

    data: bytes
    findings: tuple[Finding, ...]
    model: str
    dpl_unit: DplUnit
    record_count: int
    downloads: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """True when no finding is an error."""
        return not self.errors

    @property
    def errors(self) -> tuple[Finding, ...]:
        """The error findings."""
        return tuple(f for f in self.findings if f.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        """The warning findings."""
        return tuple(f for f in self.findings if f.severity is Severity.WARNING)


def _pick(explicit: float | None, default: _Default) -> float | _Default:
    return default if explicit is None else explicit


def _calibration(options: RenderOptions, model: CalibrationDefaults) -> tuple[CalibrationDefaults, dict[str, str]]:
    """Fill every calibration value the options leave unset from the model's measured defaults.

    Returns the values to render with and each value taken from the model that is not neutral,
    keyed by option name (``offset_along``) with its text for the report: a neutral default prints
    exactly what no calibration prints, so naming it would tell the reader nothing.
    """
    neutral = CalibrationDefaults()
    candidates: list[tuple[str, float | None, float | None, float | None, str]] = [
        ("scale_across", options.scale_across, model.scale_across, neutral.scale_across, ""),
        ("scale_along", options.scale_along, model.scale_along, neutral.scale_along, ""),
        ("offset_across", options.offset_across, model.offset_across, neutral.offset_across, " mm"),
        ("offset_along", options.offset_along, model.offset_along, neutral.offset_along, " mm"),
        ("stop_position", options.stop_position_mm, model.stop_position_mm, neutral.stop_position_mm, " mm"),
    ]
    from_model = {
        name: f"{name} {value:g}{unit}"
        for name, explicit, value, nominal, unit in candidates
        if explicit is None and value is not None and value != nominal
    }
    resolved = CalibrationDefaults(
        scale_across=_pick(options.scale_across, model.scale_across),
        scale_along=_pick(options.scale_along, model.scale_along),
        offset_across=_pick(options.offset_across, model.offset_across),
        offset_along=_pick(options.offset_along, model.offset_along),
        stop_position_mm=_pick(options.stop_position_mm, model.stop_position_mm),
        provenance=model.provenance,
    )
    return resolved, from_model


def _context(label: Label, options: RenderOptions) -> RenderContext:
    profile = get_profile(options.model)
    findings = FindingCollector(options.policy)
    calibration, from_model = _calibration(options, profile.calibration)
    if from_model:
        findings.info(
            FindingCode.MODEL_DEFAULTS_APPLIED,
            f"model defaults ({calibration.provenance}): {', '.join(from_model.values())}",
        )
    probe = RenderContext(
        profile=profile,
        firmware=parse_firmware(options.firmware),
        options=options.options,
        unit=label.unit,
        dpl_unit=DplUnit.INCH_HUNDREDTHS,
        findings=findings,
        cutter_installed=options.cutter_installed,
        symbol_set=label.symbol_set,
        default_attribute=label.attribute or Attribute.XOR,
        across=Correction(scale=calibration.scale_across, unit_dots=profile.inch_unit_dots),
        along=Correction(scale=calibration.scale_along, unit_dots=profile.inch_unit_dots),
        offset_across=calibration.offset_across,
        offset_along=calibration.offset_along,
        model_offset_axes=frozenset(axis for axis in Axis if f"offset_{axis.value}" in from_model),
        stop_position_mm=calibration.stop_position_mm,
        cutter_stop_position_mm=options.cutter_stop_position_mm,
    )
    if label.unit is Unit.MM and probe.supports(Feature.FORMAT_METRIC):
        probe.dpl_unit = DplUnit.MM_TENTHS
    elif label.unit is Unit.MM:
        findings.info(
            FindingCode.INCH_UNITS, f"{profile.name} formats take inches; millimetres are rounded to 1/100 in"
        )
    return probe


def _check_label_level(label: Label, ctx: RenderContext, *, filled: bool) -> None:
    # A filled record's remaining braces are literal text that came from {{ }}, not placeholders.
    unfilled = [] if filled else label_placeholders(label)
    if unfilled:
        ctx.findings.warn(FindingCode.PLACEHOLDERS_UNFILLED, f"placeholders without data: {', '.join(unfilled)}")
    # Nominal on purpose: this compares the label with the printhead, not what one printer prints.
    width_in = None if label.width is None else to_inches(label.width, label.unit, dpi=ctx.profile.dpi)
    if width_in is not None and round(width_in * 100) > round(ctx.profile.print_width_in * 100):
        ctx.findings.warn(
            FindingCode.LABEL_WIDER_THAN_PRINT_WIDTH,
            f"label is wider than the {ctx.profile.print_width_in} in printable width of {ctx.profile.name}",
        )


def _encode_element(element: Element, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Dispatch ``element`` to its encoder.

    A match on the class rather than a table keyed by ``element.type``: pyright narrows the union
    in each case, so every encoder is checked against its own element type, and ``lines`` reads
    as possibly unbound the moment a new element type has no case.
    """
    match element:
        case Text():
            lines = encode_text(element, index=index, ctx=ctx)
        case ScalableText():
            lines = encode_scalable_text(element, index=index, ctx=ctx)
        case Barcode():
            lines = encode_barcode(element, index=index, ctx=ctx)
        case Line():
            lines = encode_line(element, index=index, ctx=ctx)
        case Box():
            lines = encode_box(element, index=index, ctx=ctx)
        case Circle():
            lines = encode_circle(element, index=index, ctx=ctx)
        case Polygon():
            lines = encode_polygon(element, index=index, ctx=ctx)
        case Image():
            lines = encode_image(element, index=index, ctx=ctx)
    return lines


def _encode_elements(elements: list[Element], ctx: RenderContext) -> list[bytes]:
    lines: list[bytes] = []
    for index, element in enumerate(elements):
        lines.extend(_encode_element(element, index=index, ctx=ctx))
    return lines


def _count_records(lines: list[bytes]) -> int:
    return sum(1 for item in lines if item[:1].decode("ascii", "replace") in _RECORD_PREFIXES)


def build_job(label: Label, options: RenderOptions | None = None, *, filled: bool = False) -> RenderResult:
    """Render ``label`` for ``options.model`` and report every finding; never raises for a finding.

    Raises:
        UnknownModelError: ``options.model`` names no profile.
        ValueError: ``options.firmware`` is malformed.
    """
    options = options or RenderOptions()
    ctx = _context(label, options)
    _check_label_level(label, ctx, filled=filled)
    prefix = system_prefix(label, ctx)
    body = _encode_elements(label.elements, ctx)
    record_count = _count_records(body)
    if record_count > ctx.profile.max_fields:
        ctx.findings.refuse(
            FindingCode.TOO_MANY_FIELDS,
            f"{record_count} fields; {ctx.profile.name} takes at most {ctx.profile.max_fields}",
        )
    head = format_header(label, ctx)
    foot = format_footer(label, ctx)
    downloads = [download.command for download in ctx.downloads]
    data = b"".join([*prefix, *downloads, *head, *body, *foot, *ctx.system_suffix])
    return RenderResult(
        data=data,
        findings=ctx.findings.findings(),
        model=ctx.profile.id,
        dpl_unit=ctx.dpl_unit,
        record_count=record_count,
        downloads=tuple(download.name for download in ctx.downloads),
    )


def render(label: Label, options: RenderOptions | None = None) -> RenderResult:
    """Render ``label`` and refuse it when any finding is an error.

    Raises:
        LabelRejectedError: At least one finding is an error; ``findings`` holds them all.

    Example:
        >>> from datamax_dpl.domain.label import Label, Text
        >>> render(Label(elements=[Text(x=5, y=5, text="Hi")])).model
        'generic'
    """
    result = build_job(label, options)
    if not result.ok:
        reasons = "; ".join(f.message for f in result.errors)
        raise LabelRejectedError(f"label rejected: {reasons}", result.findings)
    return result


__all__ = ["RenderResult", "build_job", "render"]
