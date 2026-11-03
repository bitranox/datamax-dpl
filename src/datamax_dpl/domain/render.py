"""Turn a :class:`~datamax_dpl.domain.label.Label` into the DPL bytes for one printer model.

:func:`build_job` always returns a :class:`RenderResult` with every finding; :func:`render` does
the same but raises :class:`~datamax_dpl.domain.errors.LabelRejectedError` when any finding is an
error, so a caller that only wants bytes cannot print a label that broke a rule.

Byte layout of a job: system commands (unit mode, then the persistent printer settings: media,
start of print, maximum label length, feed rate, software switches, stop/present position), the
clear of the image module, the image downloads, ``<STX>L``, format settings, one line per record,
``Q``, ``E``, a final cut when one is needed, and the clear after the label.

A label that no clear precedes leaves out the download of every image name the printer holds
(:class:`~datamax_dpl.domain.held_images.HeldImages`, read by the caller). Nothing carries from one
label of a batch to the next: what the printer holds is what a read of it says, before each label.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ConfigDict

from .encode_barcode import encode_barcode
from .encode_graphics import encode_box, encode_circle, encode_image, encode_line, encode_polygon, encode_qr
from .encode_text import encode_scalable_text, encode_text
from .enums import Attribute, FindingCode, ImageModule, PrinterSetting, PrinterStop, Rotation, Severity, Unit
from .errors import LabelRejectedError
from .findings import Finding, FindingCollector
from .held_images import HeldImages
from .image_budget import ImageBudget
from .job import (
    closing_clears,
    format_footer,
    format_header,
    image_downloads,
    retry_clear,
    retry_clear_may_go,
    stops_at_tear_off,
    system_prefix,
)
from .label import Barcode, Box, Circle, Image, Line, Polygon, Qr, ScalableText, Text
from .overlap import inexact_rectangle, printed_dots
from .placeholders import label_placeholders
from .preview import SentAs
from .profiles import CalibrationDefaults, CalibrationKey, Feature, get_profile
from .render_context import RenderContext, RenderOptions, resolved_image_module, resolved_printer_state
from .transparency import Emulation, emulate_transparency
from .units import MM_PER_INCH, Axis, Correction, DplUnit, to_inches

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from .label import Element, Label
    from .profiles import PrinterProfile

#: The first character of a record line: its rotation digit.
_RECORD_PREFIXES = tuple(str(rotation.dpl_digit) for rotation in Rotation)
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
        closing: The bytes that end ``data``: the clears after the label. A batch that cuts
            across records puts its cut before them, so they stay the last of each label.
        image_module: The memory module the downloads go to, None where the job has none.
        uses_held_names: True when no clear runs before the label's downloads, so what it sends
            depends on the names the printer holds: each one it lists is left out.
        names_unread: True when the label uses held names and they were not known, whether it
            says so (``image_names_unread``) or they are to be read right before its downloads
            (``HeldImages.read_at_send``): reading them (``<STX>W``) and rendering again leaves
            out each held one.
        upload_end: Where the job's image downloads end in ``data``: everything before it (the
            printer settings, the clear before and the downloads) is the upload a caller can send
            and check (``<STX>W``) before the format that places the images goes out.
        retry_clear: The clear a caller sends before it uploads the images again because the
            printer did not list them: ``<STX>q`` for a model's built-in RAM module, the plain
            Prodigy's ``<STX>R`` for each name the job downloads; empty where no clear may go
            (a plug-in module, which may be flash, or a model that can clear nothing). Sent
            whatever the clear keys say.
        retry_clear_empties: True when ``retry_clear`` empties the whole module (``<STX>q``), so
            nothing the printer held there survives it.
        retry_clear_may_go: True where a clear may go before an upload again (``<STX>q`` to the
            model's built-in RAM, the Prodigy's ``<STX>R``), whatever the job downloads:
            ``retry_clear`` is empty on the Prodigy for a job that downloads nothing, while a clear
            still may go there.

    Example:
        >>> from datamax_dpl.domain.label import Label, Text
        >>> result = build_job(Label(elements=[Text(x=5, y=5, text="Hi")]), RenderOptions(model="dmx-600"))
        >>> result.ok, result.data[:8], result.closing
        (True, b'\\x02n\\x02c0000', b'\\x02qE')
    """

    # A job is not UTF-8 (charset-mapped text, image records): pydantic's default JSON form for
    # bytes is a UTF-8 string and raises on the first umlaut, so the bytes travel as base64.
    # A batch spool reads one back per record: pydantic's default caches every short string it
    # parses, which keeps each record's image names (up to 16384 of them), so only keys are cached.
    model_config = ConfigDict(
        frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64", cache_strings="keys"
    )

    data: bytes
    findings: tuple[Finding, ...]
    model: str
    dpl_unit: DplUnit
    record_count: int
    downloads: tuple[str, ...]
    image_bytes: int
    halved_elements: tuple[int, ...]
    emulated_groups: tuple[tuple[int, ...], ...]
    closing: bytes
    image_module: ImageModule | None = None
    uses_held_names: bool = False
    names_unread: bool = False
    upload_end: int = 0
    retry_clear: bytes = b""
    retry_clear_empties: bool = False
    retry_clear_may_go: bool = False

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


def _tear_off_stop(explicit: float | PrinterStop | None, default: float | None) -> float | None:
    """Return the tear-off stop position a job sends: none for ``"printer"``, else as :func:`_pick`."""
    if explicit is PrinterStop.PRINTER:
        return None
    return _pick(explicit, default)


@dataclass(frozen=True, slots=True)
class _ModelDefault:
    """One value of the model's calibration a job uses, as the report names it.

    Attributes:
        text: The option and its value, e.g. ``offset_along -0.25 mm``.
        offset_axis: The axis of an offset; None for a scale factor or the stop position.
        stop_position: True for the stop position, which is named only where the job sends one.
    """

    text: str
    offset_axis: Axis | None = None
    stop_position: bool = False


class _DefaultOption(str, Enum):
    """An option the model's calibration may supply, as the model defaults report names it.

    The scale factors and offsets go by their configuration keys; the stop position by the
    ``[printer]`` key that sets it.
    """

    SCALE_ACROSS = CalibrationKey.SCALE_ACROSS.value
    SCALE_ALONG = CalibrationKey.SCALE_ALONG.value
    OFFSET_ACROSS = CalibrationKey.OFFSET_ACROSS.value
    OFFSET_ALONG = CalibrationKey.OFFSET_ALONG.value
    STOP_POSITION = "stop_position"

    @property
    def unit(self) -> str:
        """What the report writes after the value: nothing after a scale factor, else millimetres."""
        return "" if self in (_DefaultOption.SCALE_ACROSS, _DefaultOption.SCALE_ALONG) else " mm"


@dataclass(frozen=True, slots=True, kw_only=True)
class _Candidate:
    """A value the model's calibration may supply: as the options give it, as resolved, as neutral."""

    option: _DefaultOption
    given: float | PrinterStop | None
    value: float | None
    nominal: float | None
    offset_axis: Axis | None = None


def _model_defaults_used(options: RenderOptions, calibration: CalibrationDefaults) -> tuple[_ModelDefault, ...]:
    """Return each value of ``calibration`` the options did not set and that is not neutral.

    A value counts as the model's when the job's value is not the one the options hold, so this
    follows whatever rule resolved ``calibration``. A neutral default prints exactly what no
    calibration prints, so naming it would tell the reader nothing.
    """
    neutral = CalibrationDefaults()
    candidates = (
        _Candidate(
            option=_DefaultOption.SCALE_ACROSS,
            given=options.scale_across,
            value=calibration.scale_across,
            nominal=neutral.scale_across,
        ),
        _Candidate(
            option=_DefaultOption.SCALE_ALONG,
            given=options.scale_along,
            value=calibration.scale_along,
            nominal=neutral.scale_along,
        ),
        _Candidate(
            option=_DefaultOption.OFFSET_ACROSS,
            given=options.offset_across,
            value=calibration.offset_across,
            nominal=neutral.offset_across,
            offset_axis=Axis.ACROSS,
        ),
        _Candidate(
            option=_DefaultOption.OFFSET_ALONG,
            given=options.offset_along,
            value=calibration.offset_along,
            nominal=neutral.offset_along,
            offset_axis=Axis.ALONG,
        ),
        # The given value is "printer" for a stop position that keeps the printer's own: the job's
        # value is then None, so the model's default is not named.
        _Candidate(
            option=_DefaultOption.STOP_POSITION,
            given=options.stop_position_mm,
            value=calibration.stop_position_mm,
            nominal=neutral.stop_position_mm,
        ),
    )
    return tuple(
        _ModelDefault(
            text=f"{c.option.value} {c.value:g}{c.option.unit}",
            offset_axis=c.offset_axis,
            stop_position=c.option is _DefaultOption.STOP_POSITION,
        )
        for c in candidates
        if c.value is not None and c.value not in (c.given, c.nominal)
    )


def _report_model_defaults(
    label: Label, ctx: RenderContext, *, provenance: str, from_model: tuple[_ModelDefault, ...]
) -> None:
    """Record which model defaults this job uses, naming the stop position only where it is sent."""
    if not stops_at_tear_off(label, ctx) or PrinterSetting.STOP not in ctx.printer_state.send:
        # The label's own or the cutter's stop position replaces the model's, and a job that sends
        # no stop position sends none: either way the model's is never sent.
        from_model = tuple(default for default in from_model if not default.stop_position)
    if from_model:
        named = ", ".join(default.text for default in from_model)
        ctx.findings.info(FindingCode.MODEL_DEFAULTS_APPLIED, f"model defaults ({provenance}): {named}")


def _bitmap_rows(label: Label) -> dict[str, tuple[str, ...]]:
    """Return the bitmap of each image name the label downloads, the first element's when two do."""
    rows: dict[str, tuple[str, ...]] = {}
    for element in label.elements:
        if isinstance(element, Image) and element.bitmap is not None:
            rows.setdefault(element.name, tuple(element.bitmap))
    return rows


def _stored_names(label: Label, bitmap_rows: dict[str, tuple[str, ...]]) -> frozenset[str]:
    """Return the names of the label's images only the printer holds: no element of it downloads them."""
    return frozenset(
        element.name
        for element in label.elements
        if isinstance(element, Image) and element.bitmap is None and element.name not in bitmap_rows
    )


def _context(label: Label, options: RenderOptions, *, halve: bool, held: HeldImages) -> RenderContext:
    profile = get_profile(options.model)
    findings = FindingCollector(options.policy)
    calibration = _resolve(options, profile.calibration)
    from_model = _model_defaults_used(options, calibration)
    bitmap_rows = _bitmap_rows(label)
    probe = RenderContext(
        profile=profile,
        firmware=options.firmware,
        options=options.options,
        unit=label.unit,
        dpl_unit=DplUnit.INCH_HUNDREDTHS,
        findings=findings,
        image_module=resolved_image_module(options, profile),
        printer_state=resolved_printer_state(options, profile, label_media=label.media),
        cutter_installed=options.cutter_installed,
        symbol_set=label.symbol_set,
        default_attribute=label.attribute or Attribute.XOR,
        across=Correction(scale=calibration.scale_across, unit_dots=profile.inch_unit_dots),
        along=Correction(scale=calibration.scale_along, unit_dots=profile.inch_unit_dots),
        offset_across=calibration.offset_across,
        offset_along=calibration.offset_along,
        model_offset_axes=frozenset(default.offset_axis for default in from_model if default.offset_axis is not None),
        stop_position_mm=calibration.stop_position_mm,
        cutter_stop_position_mm=options.cutter_stop_position_mm,
        reserved_image_names=_stored_names(label, bitmap_rows),
        bitmap_rows=bitmap_rows,
        halve_images=halve,
        held_images=held,
        held_image_severity=options.held_image_severity,
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

    Example:
        >>> from datamax_dpl.domain.label import Label, Text
        >>> job_context(Label(elements=[Text(x=5, y=5, text="Hi")]), RenderOptions(model="allegro-2")).dots_per_unit
        2.0
    """
    return _context(label, options or RenderOptions(), halve=False, held=HeldImages())


def _check_label_level(label: Label, ctx: RenderContext) -> None:
    # A filled record's remaining braces are literal text that came from {{ }}, not placeholders.
    unfilled = [] if label.filled else label_placeholders(label)
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


def _reach(element: Element, ctx: RenderContext) -> int | None:
    """Return the highest dot row a field reaches along the feed, or None when it prints nothing.

    The row is the one the encoder commands (``overlap`` places every field at its record's
    position, scale and offset included); text and barcodes count by the rectangle the preview
    draws for them. An image only the printer holds counts by its reference row alone: its size
    is unknown here, and the preview's stand-in size would refuse labels that print.
    """
    dots = printed_dots(element, ctx)
    if dots is not None:
        return dots.area.top
    area = inexact_rectangle(element, ctx)
    if area is None:
        return None
    return area.bottom if isinstance(element, Image) and ctx.image_rows(element) is None else area.top


def format_row_limit(options: RenderOptions) -> int | None:
    """Return the dot rows one format reaches for ``options``, at dot height 1, or None for no limit.

    A configured ``max_format_rows`` wins; else the model's limit, which most models do not have.

    Args:
        options: Render options of the job; their model and ``max_format_rows`` decide the limit.

    Returns:
        The highest dot row along the feed one format may reach, or None when nothing limits it.

    Raises:
        UnknownModelError: No profile has the options' model id.

    Example:
        >>> format_row_limit(RenderOptions(model="allegro-2"))
        1648
        >>> format_row_limit(RenderOptions(model="dmx-600")) is None
        True
    """
    return _row_limit(options, get_profile(options.model))


def _row_limit(options: RenderOptions, profile: PrinterProfile) -> int | None:
    return profile.max_format_rows if options.max_format_rows is None else options.max_format_rows


def _check_format_length(label: Label, ctx: RenderContext, *, max_rows: int | None) -> None:
    """Refuse a label whose highest field passes the rows one format reaches, under either policy.

    ``max_rows`` counts commanded rows at dot height 1, so a label dot_size of height h allows h
    times as many; the limit itself is a row the label may still reach. None means no limit.
    """
    if max_rows is None:
        return
    height = label.dot_size.height if label.dot_size else 1
    limit = max_rows * height
    reaches = [
        (row, index) for index, element in enumerate(label.elements) if (row := _reach(element, ctx)) is not None
    ]
    row, index = max(reaches, key=lambda reach: (reach[0], -reach[1]), default=(0, 0))
    if row <= limit:
        return
    # Commanded rows are nominal dots, which this printer prints scale_along times as long.
    mm = limit / ctx.profile.dpi * MM_PER_INCH * ctx.along.scale
    escape = "a label dot_size of height 2 doubles it, and " if height == 1 else ""
    ctx.findings.refuse(
        FindingCode.FORMAT_PAST_MAX_LENGTH,
        f"the field reaches dot row {row} along the feed, past row {limit} (about {mm:.1f} mm), beyond which the "
        f"{ctx.profile.name} may cut a format at dot_size height {height}; {escape}max_format_rows sets another limit",
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
    """Encode each element, putting a merged group's image where its first element stands.

    Stops once the records pass the model's limit: the job is refused whatever follows, and a label
    with thousands of elements would otherwise cost seconds and hundreds of MB first. The limit
    counts records, not elements, because a merged group sends one record for many elements.
    """
    lines: list[bytes] = []
    records = 0
    for index, element in enumerate(elements):
        if index in emulation.placed:
            encoded = emulation.placed[index]
        elif index in emulation.merged:
            continue
        else:
            encoded = _encode_element(element, index=index, ctx=ctx)
        lines.extend(encoded)
        records += _count_records(encoded)
        if records > ctx.profile.max_fields:
            break
    return lines


def _count_records(lines: Sequence[bytes]) -> int:
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
        """Bytes of dot data the job's downloads hold; a download left out for a held name costs none."""
        return sum(download.data_bytes for download in self.ctx.sent_downloads)


def _encode(label: Label, options: RenderOptions, *, halve: bool, held: HeldImages) -> _Job:
    """Encode everything up to the format header, halving the job's images when ``halve``."""
    ctx = _context(label, options, halve=halve, held=held)
    _check_label_level(label, ctx)
    _check_qr_dot_size(label, ctx)
    prefix = system_prefix(label, ctx)
    body = _encode_elements(label.elements, ctx, emulate_transparency(label, ctx))
    record_count = _count_records(body)
    if record_count > ctx.profile.max_fields:
        ctx.findings.refuse(
            FindingCode.TOO_MANY_FIELDS,
            f"at least {record_count} fields; {ctx.profile.name} takes at most {ctx.profile.max_fields}",
        )
    else:
        # Reading a QR Code's reach encodes it again, and the stop above saved exactly that work: a
        # label of 2048 large codes would still take minutes to reach a refusal it already has.
        _check_format_length(label, ctx, max_rows=_row_limit(options, ctx.profile))
    return _Job(ctx=ctx, prefix=prefix, body=body, record_count=record_count)


def _image_budget(options: RenderOptions) -> ImageBudget | None:
    """Return the image memory budget a job for ``options`` keeps to, or None when it has none.

    A configured budget always applies; the model's own only where ``model_image_budget`` says so.
    """
    if options.image_memory_bytes is not None:
        return ImageBudget(limit=options.image_memory_bytes)
    if not options.model_image_budget:
        return None
    return _model_budget(options)


def _model_budget(options: RenderOptions) -> ImageBudget | None:
    """Return the model's own image memory budget, or None for a model that has none."""
    calibration = effective_calibration(options)
    if calibration.image_memory_bytes is None:
        return None
    return ImageBudget(
        limit=calibration.image_memory_bytes,
        model=options.model,
        provenance=calibration.image_memory_provenance,
    )


def _fit_budget(label: Label, options: RenderOptions, *, job: _Job, budget: ImageBudget) -> _Job:
    """Fit a job past its image memory budget: refuse it under strict, else halve its images once.

    Halving happens once: a job still past the budget afterwards is refused, not halved again.
    """
    over = f"the job's image downloads hold {job.image_bytes} bytes; {budget.describe()}"
    if not job.ctx.findings.degrading:
        job.ctx.findings.refuse(FindingCode.IMAGE_MEMORY_EXCEEDED, over)
        return job
    halved = _encode(label, options, halve=True, held=job.ctx.held_images)
    elements = sorted(set(halved.ctx.halved_elements))
    if elements:
        halved.ctx.findings.deviate(
            FindingCode.IMAGE_HALVED,
            over,
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


def build_job(label: Label, options: RenderOptions | None = None, *, held: HeldImages | None = None) -> RenderResult:
    """Render ``label`` for ``options.model`` and report every finding; never raises for a finding.

    A job whose image downloads hold more than its image memory budget is encoded a second time
    with its images halved (degrade) or refused (strict); see :mod:`~datamax_dpl.domain.image_budget`.

    Args:
        label: The label.
        options: How to render; None renders for ``generic``.
        held: The image names the printer holds in the job's module; None when nobody asked it.
            Only a label no clear precedes uses them.

    Returns:
        The job and every finding; check ``ok`` before sending ``data``.

    Raises:
        UnknownModelError: ``options.model`` names no profile.
    """
    return _encoded_result(label, options or RenderOptions(), held=held or HeldImages())


def build_jobs(
    labels: Iterable[Label],
    options: RenderOptions | None = None,
    *,
    held: HeldImages | None = None,
) -> tuple[RenderResult, ...]:
    """Render each label of a batch in turn, as :func:`build_job` does, each with the same ``held``.

    Nothing carries from one label to the next: a label no clear precedes downloads every name
    ``held`` does not list, one an earlier label of the batch downloaded included, since what the
    printer holds by then is for a read of it to say, not for this batch to remember.

    Args:
        labels: The labels, in the order they are sent.
        options: How to render; None renders for ``generic``.
        held: The image names the printer holds, for every label; None when nobody asked it.

    Raises:
        UnknownModelError: ``options.model`` names no profile.

    Example:
        >>> from datamax_dpl.domain.label import Label, Qr
        >>> no_clear = {"clear_graphics_before_label": False, "clear_graphics_after_label": False}
        >>> options = RenderOptions(model="allegro-2", **no_clear)
        >>> jobs = build_jobs([Label(elements=[Qr(x=5, y=5, data="SAME")])] * 2, options)
        >>> [len(job.downloads) for job in jobs]
        [1, 1]
    """
    return tuple(iter_jobs(labels, options, held=held))


def iter_jobs(
    labels: Iterable[Label],
    options: RenderOptions | None = None,
    *,
    held: HeldImages | None = None,
) -> Iterator[RenderResult]:
    """Render the labels of a batch one at a time, as :func:`build_jobs` does, for a caller that may stop early.

    Args:
        labels: The labels, in the order they are sent; each is taken only when its turn comes.
        options: How to render; None renders for ``generic``.
        held: The image names the printer holds, for every label; None when nobody asked it.

    Yields:
        Each label's result in turn.
    """
    options = options or RenderOptions()
    given = held or HeldImages()
    for label in labels:
        yield _encoded_result(label, options, held=given)


def _halved(label: Label, options: RenderOptions, *, held: HeldImages) -> _Job:
    """Encode the job with its images halved, as the budget would, for a printer that did not store them whole.

    No budget is weighed: the caller found that the printer's module did not take the images at
    full size, which says more than a count of dot data can.
    """
    halved = _encode(label, options, halve=True, held=held)
    elements = sorted(set(halved.ctx.halved_elements))
    if elements:
        halved.ctx.findings.deviate(
            FindingCode.IMAGE_HALVED,
            "the printer's image module did not store the job's images at full size",
            fallback=(
                f"elements {', '.join(map(str, elements))} sent at half resolution and placed at double "
                f"magnification ({halved.image_bytes} bytes); unverified on paper"
            ),
        )
    return halved


def _note_skipped_budget(job: _Job, options: RenderOptions) -> None:
    """Say so when the model's budget, left out for a two-way print, would have halved the job's images.

    ``validate`` and a one-way print keep to that budget, so the same label reads ``image_halved``
    there and goes at full size here; this finding names the difference.
    """
    if options.model_image_budget:
        return
    model = _model_budget(options)
    if model is None or job.image_bytes <= model.limit:
        return
    job.ctx.findings.info(
        FindingCode.IMAGE_BUDGET_SKIPPED,
        f"the job's image downloads hold {job.image_bytes} bytes; {model.describe()}, and validate, render and "
        "a one-way print halve images past it; here they go at full size and the upload check reads whether "
        "the printer stored them",
    )


def _uses_held_names(ctx: RenderContext) -> bool:
    """Whether what the label sends depends on the names the printer holds: no clear goes before its downloads."""
    return bool(ctx.downloads) and ctx.image_module is not None and not ctx.clear_runs_before


def _encoded_result(label: Label, options: RenderOptions, *, held: HeldImages, halve: bool = False) -> RenderResult:
    """Render ``label``; ``halve`` sends every image that can be halved at half resolution whatever the budget says."""
    if halve:
        job = _halved(label, options, held=held)
    else:
        job = _encode(label, options, halve=False, held=held)
        budget = _image_budget(options)
        if budget is not None and job.image_bytes > budget.limit:
            job = _fit_budget(label, options, job=job, budget=budget)
        elif budget is None:
            _note_skipped_budget(job, options)
    ctx = job.ctx
    head = format_header(label, ctx)
    foot = format_footer(label, ctx)
    downloads = image_downloads(ctx)
    closing = closing_clears(ctx)
    upload = b"".join([*job.prefix, *downloads])
    data = b"".join([upload, *head, *job.body, *foot, *ctx.system_suffix, *closing])
    uses_held = _uses_held_names(ctx)
    return RenderResult(
        data=data,
        findings=ctx.findings.findings(),
        model=ctx.profile.id,
        dpl_unit=ctx.dpl_unit,
        record_count=job.record_count,
        downloads=tuple(download.name for download in ctx.sent_downloads),
        image_bytes=job.image_bytes,
        halved_elements=tuple(sorted(set(ctx.halved_elements))),
        emulated_groups=tuple(ctx.emulated_groups),
        closing=b"".join(closing),
        image_module=ctx.image_module,
        uses_held_names=uses_held,
        names_unread=uses_held and held.listed is None,
        upload_end=len(upload),
        retry_clear=b"".join(retry_clear(ctx)),
        retry_clear_empties=ctx.clears_module,
        retry_clear_may_go=retry_clear_may_go(ctx),
    )


def build_record(
    label: Label,
    options: RenderOptions | None = None,
    *,
    held: HeldImages | None = None,
    halve: bool = False,
) -> RenderResult:
    """Render one label of a batch as :func:`build_job` does, its images halved when ``halve``.

    For a caller that sends a batch label by label and may render a label again on the way: with
    what a read of the printer's directory listed, with the module known empty after a clear, or
    with its images halved.

    Args:
        label: The label.
        options: How to render; None renders for ``generic``.
        held: The image names the printer holds before this label; None when nobody asked it.
        halve: Send every image that can be halved (every one but a QR Code) at half resolution
            and double magnification, with ``image_halved``, whatever the image memory budget
            says: for a printer whose module did not store the images at full size.

    Returns:
        The result.

    Raises:
        UnknownModelError: ``options.model`` names no profile.

    Example:
        >>> from datamax_dpl.domain.label import Image, Label
        >>> label = Label(elements=[Image(x=1, y=1, name="LOGO", bitmap=["##..", "..##"])])
        >>> full = build_record(label, RenderOptions(model="allegro-2"))
        >>> halved = build_record(label, RenderOptions(model="allegro-2"), halve=True)
        >>> full.halved_elements, halved.halved_elements, full.downloads == halved.downloads
        ((), (0,), False)
    """
    return _encoded_result(label, options or RenderOptions(), held=held or HeldImages(), halve=halve)


def render(label: Label, options: RenderOptions | None = None) -> RenderResult:
    """Render ``label`` and refuse it when any finding is an error.

    Args:
        label: The label to render.
        options: The model, fallback policy and job settings; None renders for the generic model.

    Returns:
        The job's bytes and its findings, none of them an error.

    Raises:
        LabelRejectedError: At least one finding is an error; ``findings`` holds them all, up to
            the element that passed the model's field limit when that is the refusal.
        UnknownModelError: ``options.model`` names no profile.

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


__all__ = [
    "RenderResult",
    "build_job",
    "build_jobs",
    "build_record",
    "effective_calibration",
    "format_row_limit",
    "iter_jobs",
    "job_context",
    "render",
]
