"""Turn a :class:`~datamax_dpl.domain.label.Label` into the DPL bytes for one printer model.

:func:`build_job` always returns a :class:`RenderResult` with every finding; :func:`render` does
the same but raises :class:`~datamax_dpl.domain.errors.LabelRejectedError` when any finding is an
error, so a caller that only wants bytes cannot print a label that broke a rule.

Byte layout of a job: system commands (unit mode, media, stop/present position, image
downloads), ``<STX>L``, format settings, one line per record, ``Q``, ``E``, then a final cut when
one is needed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from .encode_barcode import encode_barcode
from .encode_graphics import encode_box, encode_circle, encode_image, encode_line, encode_polygon
from .encode_text import encode_scalable_text, encode_text
from .enums import Attribute, Severity, Unit
from .errors import LabelRejectedError
from .findings import Finding, FindingCollector
from .job import format_footer, format_header, system_prefix
from .placeholders import label_placeholders
from .profiles import Feature, get_profile, parse_firmware
from .render_context import RenderContext, RenderOptions
from .units import DplUnit

if TYPE_CHECKING:
    from .label import Element, Label

_RECORD_PREFIXES = tuple("1234")

Encoder = Callable[..., list[bytes]]

_ENCODERS: dict[str, Encoder] = {
    "text": encode_text,
    "scalable_text": encode_scalable_text,
    "barcode": encode_barcode,
    "line": encode_line,
    "box": encode_box,
    "circle": encode_circle,
    "polygon": encode_polygon,
    "image": encode_image,
}


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

    model_config = ConfigDict(frozen=True, extra="forbid")

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


def _context(label: Label, options: RenderOptions) -> RenderContext:
    profile = get_profile(options.model)
    findings = FindingCollector(options.policy)
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
    )
    if label.unit is Unit.MM and probe.supports(Feature.FORMAT_METRIC):
        probe.dpl_unit = DplUnit.MM_TENTHS
    elif label.unit is Unit.MM:
        findings.info("inch_units", f"{profile.name} formats take inches; millimetres are rounded to 1/100 in")
    return probe


def _check_label_level(label: Label, ctx: RenderContext) -> None:
    unfilled = label_placeholders(label)
    if unfilled:
        ctx.findings.warn("placeholders_unfilled", f"placeholders without data: {', '.join(unfilled)}")
    if label.width is not None and ctx.inch_hundredths(label.width) > round(ctx.profile.print_width_in * 100):
        ctx.findings.warn(
            "label_wider_than_print_width",
            f"label is wider than the {ctx.profile.print_width_in} in printable width of {ctx.profile.name}",
        )


def _encode_elements(elements: list[Element], ctx: RenderContext) -> list[bytes]:
    lines: list[bytes] = []
    for index, element in enumerate(elements):
        lines.extend(_ENCODERS[element.type](element, index=index, ctx=ctx))
    return lines


def _count_records(lines: list[bytes]) -> int:
    return sum(1 for item in lines if item[:1].decode("ascii", "replace") in _RECORD_PREFIXES)


def build_job(label: Label, options: RenderOptions | None = None) -> RenderResult:
    """Render ``label`` for ``options.model`` and report every finding; never raises for a finding.

    Raises:
        UnknownModelError: ``options.model`` names no profile.
        ValueError: ``options.firmware`` is malformed.
    """
    options = options or RenderOptions()
    ctx = _context(label, options)
    _check_label_level(label, ctx)
    prefix = system_prefix(label, ctx)
    body = _encode_elements(label.elements, ctx)
    record_count = _count_records(body)
    if record_count > ctx.profile.max_fields:
        ctx.findings.refuse(
            "too_many_fields", f"{record_count} fields; {ctx.profile.name} takes at most {ctx.profile.max_fields}"
        )
    head = format_header(label, ctx)
    foot = format_footer(label, ctx)
    downloads = list(ctx.system_prefix)
    data = b"".join([*prefix, *downloads, *head, *body, *foot, *ctx.system_suffix])
    return RenderResult(
        data=data,
        findings=ctx.findings.findings(),
        model=ctx.profile.id,
        dpl_unit=ctx.dpl_unit,
        record_count=record_count,
        downloads=tuple(_download_name(cmd) for cmd in downloads),
    )


def _download_name(command: bytes) -> str:
    """Name in an ``<STX>IAAF<name><CR>`` download command."""
    header_end = command.index(b"\r")
    return command[5:header_end].decode("ascii")


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
