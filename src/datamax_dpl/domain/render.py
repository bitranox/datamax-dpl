"""Turn a :class:`~datamax_dpl.domain.label.Label` into the DPL bytes for one printer model.

:func:`build_job` always returns a :class:`RenderResult` with every finding; :func:`render` does
the same but raises :class:`~datamax_dpl.domain.errors.LabelRejectedError` when any finding is an
error, so a caller that only wants bytes cannot print a label that broke a rule.

Byte layout of a job: system commands (unit mode, media, stop/present position, image
downloads), ``<STX>L``, format settings, one line per record, ``Q``, ``E``, then a final cut when
one is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

from .encode_barcode import encode_barcode
from .encode_graphics import encode_box, encode_circle, encode_image, encode_line, encode_polygon, encode_qr
from .encode_text import encode_scalable_text, encode_text
from .enums import Attribute, FindingCode, Severity, Unit
from .errors import LabelRejectedError
from .findings import Finding, FindingCollector
from .image_budget import ImageBudget
from .job import PRINTER_STOP, format_footer, format_header, stops_at_tear_off, system_prefix
from .label import Barcode, Box, Circle, Image, Line, Polygon, Qr, ScalableText, Text
from .placeholders import label_placeholders
from .preview import SentAs
from .profiles import CalibrationDefaults, Feature, get_profile, parse_firmware
from .render_context import RenderContext, RenderOptions, resolved_image_module
from .transparency import Emulation, emulate_transparency
from .units import Axis, Correction, DplUnit, to_inches

if TYPE_CHECKING:
    from .label import Element, Label

_RECORD_PREFIXES = tuple("1234")
_Given = TypeVar("_Given")
_Default = TypeVar("_Default")


class RenderResult(BaseModel):
    """The bytes for one label job and everything noticed while producing them.

    Attributes:
        data: The complete DPL job, ready to send.
        findings: Every finding, in order.
        model: Profile id the job was built for.
        dpl_unit: Distance unit the format uses.
        record_count: Number of field records in the format.
        downloads: Names of the images downloaded before the format.
        image_bytes: Bytes of dot data the downloads hold, what the image memory budget counts
            (see :mod:`~datamax_dpl.domain.image_budget`).
        halved_elements: Elements whose image is sent at half resolution and placed at double
            magnification because the job was past its image memory budget.
        emulated_groups: Groups of overlapping elements drawn together into one downloaded image,
            because the label asks for transparency on a model without the attribute command
            (see :mod:`~datamax_dpl.domain.transparency`).

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
    image_bytes: int
    halved_elements: tuple[int, ...]
    emulated_groups: tuple[tuple[int, ...], ...]

    @property
    def sent_as(self) -> SentAs:
        """How the job sends fields where that changes their picture, for the preview."""
        return SentAs(halved=frozenset(self.halved_elements), merged=self.emulated_groups)

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


def _pick(explicit: _Given | None, default: _Default) -> _Given | _Default:
    return default if explicit is None else explicit


def effective_calibration(options: RenderOptions) -> CalibrationDefaults:
    """Return the calibration a job for ``options`` renders with.

    Each value the options set wins, a neutral one (scale 1.0, offset 0.0) included; a value they
    leave unset (None) is the model's measured default, nominal for most models. The CLI asks this
    too, so the factor ``calibrate`` reports and starts from is the one the label printed with.

    Raises:
        UnknownModelError: No profile has the options' model id.

    Example:
        >>> effective_calibration(RenderOptions(model="allegro-2")).scale_along
        0.976
        >>> effective_calibration(RenderOptions(model="allegro-2", scale_along=1.0)).scale_along
        1.0
        >>> effective_calibration(RenderOptions(model="allegro-2", image_memory_bytes=9000)).image_memory_bytes
        9000
    """
    return _resolve(options, get_profile(options.model).calibration)


def _resolve(options: RenderOptions, model: CalibrationDefaults) -> CalibrationDefaults:
    """Fill each value ``options`` leave unset from ``model``, for a caller that already has the profile."""
    return CalibrationDefaults(
        scale_across=_pick(options.scale_across, model.scale_across),
        scale_along=_pick(options.scale_along, model.scale_along),
        offset_across=_pick(options.offset_across, model.offset_across),
        offset_along=_pick(options.offset_along, model.offset_along),
        stop_position_mm=_tear_off_stop(options.stop_position_mm, model.stop_position_mm),
        provenance=model.provenance,
        image_memory_bytes=_pick(options.image_memory_bytes, model.image_memory_bytes),
        image_memory_provenance=model.image_memory_provenance,
    )


def _tear_off_stop(explicit: float | Literal["printer"] | None, default: float | None) -> float | None:
    """Return the tear-off stop position a job sends: none for ``"printer"``, else as :func:`_pick`."""
    if explicit == PRINTER_STOP:
        return None
    return _pick(explicit, default)


def _model_defaults_used(options: RenderOptions, calibration: CalibrationDefaults) -> dict[str, str]:
    """Return each value of ``calibration`` the options did not set and that is not neutral.

    Keyed by option name (``offset_along``), with its text for the report. A value counts as the
    model's when the job's value is not the one the options hold, so this follows whatever rule
    resolved ``calibration``. A neutral default prints exactly what no calibration prints, so
    naming it would tell the reader nothing.
    """
    neutral = CalibrationDefaults()
    # The given value is "printer" for a stop position that keeps the printer's own: the job's
    # value is then None, so the model's default is not named.
    candidates: list[tuple[str, float | str | None, float | None, float | None, str]] = [
        ("scale_across", options.scale_across, calibration.scale_across, neutral.scale_across, ""),
        ("scale_along", options.scale_along, calibration.scale_along, neutral.scale_along, ""),
        ("offset_across", options.offset_across, calibration.offset_across, neutral.offset_across, " mm"),
        ("offset_along", options.offset_along, calibration.offset_along, neutral.offset_along, " mm"),
        ("stop_position", options.stop_position_mm, calibration.stop_position_mm, neutral.stop_position_mm, " mm"),
    ]
    return {
        name: f"{name} {value:g}{unit}"
        for name, given, value, nominal, unit in candidates
        if value is not None and value not in (given, nominal)
    }


def _report_model_defaults(label: Label, ctx: RenderContext, *, provenance: str, from_model: dict[str, str]) -> None:
    """Record which model defaults this job uses, naming the stop position only where it is sent."""
    if not stops_at_tear_off(label, ctx):
        # The label's own or the cutter's stop position replaces the model's: it is never sent.
        from_model = {name: text for name, text in from_model.items() if name != "stop_position"}
    if from_model:
        ctx.findings.info(
            FindingCode.MODEL_DEFAULTS_APPLIED, f"model defaults ({provenance}): {', '.join(from_model.values())}"
        )


def _bitmap_rows(label: Label) -> dict[str, tuple[str, ...]]:
    """Return the bitmap of each image name the label downloads, the first element's when two do."""
    rows: dict[str, tuple[str, ...]] = {}
    for element in label.elements:
        if isinstance(element, Image) and element.bitmap is not None:
            rows.setdefault(element.name, tuple(element.bitmap))
    return rows


def _context(label: Label, options: RenderOptions, *, halve: bool) -> RenderContext:
    profile = get_profile(options.model)
    findings = FindingCollector(options.policy)
    calibration = _resolve(options, profile.calibration)
    from_model = _model_defaults_used(options, calibration)
    probe = RenderContext(
        profile=profile,
        firmware=parse_firmware(options.firmware),
        options=options.options,
        unit=label.unit,
        dpl_unit=DplUnit.INCH_HUNDREDTHS,
        findings=findings,
        image_module=resolved_image_module(options, profile),
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
        reserved_image_names=frozenset(e.name for e in label.elements if isinstance(e, Image)),
        bitmap_rows=_bitmap_rows(label),
        halve_images=halve,
    )
    _report_model_defaults(label, probe, provenance=calibration.provenance, from_model=from_model)
    if label.unit is Unit.MM and probe.supports(Feature.FORMAT_METRIC):
        probe.dpl_unit = DplUnit.MM_TENTHS
    elif label.unit is Unit.MM:
        findings.info(
            FindingCode.INCH_UNITS, f"{profile.name} formats take inches; millimetres are rounded to 1/100 in"
        )
    return probe


def job_context(label: Label, options: RenderOptions | None = None) -> RenderContext:
    """Return the context a job for ``label`` and ``options`` encodes with, before any field is encoded.

    Its findings hold what preparing it noticed (the model defaults used, inch units).

    Raises:
        UnknownModelError: ``options.model`` names no profile.
        ValueError: ``options.firmware`` is malformed.

    Example:
        >>> from datamax_dpl.domain.label import Label, Text
        >>> job_context(Label(elements=[Text(x=5, y=5, text="Hi")]), RenderOptions(model="allegro-2")).dots_per_unit
        2.0
    """
    return _context(label, options or RenderOptions(), halve=False)


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


def _check_qr_dot_size(label: Label, ctx: RenderContext) -> None:
    """Warn for each QR Code on a label whose dot size prints every image dot larger than one dot."""
    size = label.dot_size
    if size is None or (size.width, size.height) == (1, 1):
        return
    for index, element in enumerate(label.elements):
        if isinstance(element, Qr):
            ctx.findings.warn(
                FindingCode.QR_DOT_SIZE_NOT_ONE,
                f"dot size {size.width}x{size.height} prints the QR modules {size.width}x{size.height} times "
                "larger than module_dots; modules that are not square may not scan",
                element=index,
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
        case Qr():
            lines = encode_qr(element, index=index, ctx=ctx)
    return lines


def _encode_elements(elements: list[Element], ctx: RenderContext, emulation: Emulation) -> list[bytes]:
    """Encode each element, putting a merged group's image where its first element stands."""
    lines: list[bytes] = []
    for index, element in enumerate(elements):
        if index in emulation.placed:
            lines.extend(emulation.placed[index])
        elif index not in emulation.merged:
            lines.extend(_encode_element(element, index=index, ctx=ctx))
    return lines


def _count_records(lines: list[bytes]) -> int:
    return sum(1 for item in lines if item[:1].decode("ascii", "replace") in _RECORD_PREFIXES)


@dataclass
class _Job:
    """One encoding of a label: its context and the lines built before the format header."""

    ctx: RenderContext
    prefix: list[bytes]
    body: list[bytes]
    record_count: int

    @property
    def image_bytes(self) -> int:
        """Bytes of dot data the job's downloads hold."""
        return sum(download.data_bytes for download in self.ctx.downloads)


def _encode(label: Label, options: RenderOptions, *, filled: bool, halve: bool) -> _Job:
    """Encode everything up to the format header, halving the job's images when ``halve``."""
    ctx = _context(label, options, halve=halve)
    _check_label_level(label, ctx, filled=filled)
    _check_qr_dot_size(label, ctx)
    prefix = system_prefix(label, ctx)
    body = _encode_elements(label.elements, ctx, emulate_transparency(label, ctx))
    record_count = _count_records(body)
    if record_count > ctx.profile.max_fields:
        ctx.findings.refuse(
            FindingCode.TOO_MANY_FIELDS,
            f"{record_count} fields; {ctx.profile.name} takes at most {ctx.profile.max_fields}",
        )
    return _Job(ctx=ctx, prefix=prefix, body=body, record_count=record_count)


def _image_budget(options: RenderOptions) -> ImageBudget | None:
    """Return the image memory budget a job for ``options`` keeps to, or None when it has none."""
    calibration = effective_calibration(options)
    if calibration.image_memory_bytes is None:
        return None
    if options.image_memory_bytes is not None:
        return ImageBudget(limit=calibration.image_memory_bytes, source="configured")
    return ImageBudget(
        limit=calibration.image_memory_bytes,
        source=f"{options.model} default",
        provenance=calibration.image_memory_provenance,
    )


def _fit_budget(label: Label, options: RenderOptions, *, filled: bool, job: _Job, budget: ImageBudget) -> _Job:
    """Fit a job past its image memory budget: refuse it under strict, else halve its images once.

    Halving happens once: a job still past the budget afterwards is refused, not halved again.
    """
    held = f"the job's image downloads hold {job.image_bytes} bytes; {budget.describe()}"
    if not job.ctx.findings.degrading:
        job.ctx.findings.refuse(FindingCode.IMAGE_MEMORY_EXCEEDED, held)
        return job
    halved = _encode(label, options, filled=filled, halve=True)
    elements = sorted(set(halved.ctx.halved_elements))
    if elements:
        halved.ctx.findings.deviate(
            FindingCode.IMAGE_HALVED,
            held,
            fallback=(
                f"elements {', '.join(map(str, elements))} sent at half resolution and placed at double "
                f"magnification ({halved.image_bytes} bytes); unverified on paper"
            ),
        )
    if halved.image_bytes > budget.limit:
        halved.ctx.findings.refuse(
            FindingCode.IMAGE_MEMORY_EXCEEDED,
            f"the job's image downloads hold {halved.image_bytes} bytes after halving every image that can "
            f"be halved (QR Codes never are); {budget.describe()}",
        )
    return halved


def build_job(label: Label, options: RenderOptions | None = None, *, filled: bool = False) -> RenderResult:
    """Render ``label`` for ``options.model`` and report every finding; never raises for a finding.

    A job whose image downloads hold more than its image memory budget is encoded a second time
    with its images halved (degrade) or refused (strict); see :mod:`~datamax_dpl.domain.image_budget`.

    Raises:
        UnknownModelError: ``options.model`` names no profile.
        ValueError: ``options.firmware`` is malformed.
    """
    options = options or RenderOptions()
    job = _encode(label, options, filled=filled, halve=False)
    budget = _image_budget(options)
    if budget is not None and job.image_bytes > budget.limit:
        job = _fit_budget(label, options, filled=filled, job=job, budget=budget)
    ctx = job.ctx
    head = format_header(label, ctx)
    foot = format_footer(label, ctx)
    downloads = [download.command for download in ctx.downloads]
    data = b"".join([*job.prefix, *downloads, *head, *job.body, *foot, *ctx.system_suffix])
    return RenderResult(
        data=data,
        findings=ctx.findings.findings(),
        model=ctx.profile.id,
        dpl_unit=ctx.dpl_unit,
        record_count=job.record_count,
        downloads=tuple(download.name for download in ctx.downloads),
        image_bytes=job.image_bytes,
        halved_elements=tuple(sorted(set(ctx.halved_elements))),
        emulated_groups=tuple(ctx.emulated_groups),
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


__all__ = ["RenderResult", "build_job", "effective_calibration", "job_context", "render"]
