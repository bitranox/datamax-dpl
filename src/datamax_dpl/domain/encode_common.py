"""Helpers every element encoder shares: positions, field length, attributes and counters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .enums import Attribute, FindingCode, Unit
from .label import MAX_FIELD_DATA, Counter
from .profiles import Feature
from .records import line
from .render_context import ImageEdge
from .units import Axis, DplUnit, to_dpl

if TYPE_CHECKING:
    from .render_context import RenderContext

_TOLERANCE = 1e-6


def last_column(ctx: RenderContext) -> int:
    """Return the model's largest column in this job's DPL unit; a field starting past it is refused."""
    return to_dpl(ctx.profile.max_column / 100, Unit.INCH, target=ctx.dpl_unit, dpi=ctx.profile.dpi).value


def column_of(x: float, *, index: int, ctx: RenderContext) -> int:
    """Convert ``x``, moved by the printer's offset, to a DPL column, refusing it past the printable width."""
    column = ctx.distance(
        _shifted(x, Axis.ACROSS, index=index, ctx=ctx), axis=Axis.ACROSS, element=index, what="column"
    )
    limit = last_column(ctx)
    if column > limit:
        unit = "1/100 in" if ctx.dpl_unit is DplUnit.INCH_HUNDREDTHS else "1/10 mm"
        ctx.findings.refuse(
            FindingCode.OUTSIDE_PRINT_WIDTH,
            f"column {column} {unit} is past the printable width of {ctx.profile.name} ({limit} {unit})",
            element=index,
        )
    return column


def image_edge(column: int, *, what: str, ctx: RenderContext) -> ImageEdge:
    """Return the print-width check of an image whose placing record writes ``column``.

    A column past the last one is already refused by :func:`column_of` (``outside_print_width``),
    so that image's right edge is not checked again.

    Args:
        column: The record's column, as :func:`column_of` returned it.
        what: What the element is, naming the image in a refusal after its position, e.g. ``image LOGO``.
        ctx: The render context.
    """
    return ImageEdge(column=column if column <= last_column(ctx) else None, what=what)


def row_of(y: float, *, index: int, ctx: RenderContext) -> int:
    """Convert ``y``, moved by the printer's offset, to a DPL row."""
    return ctx.distance(_shifted(y, Axis.ALONG, index=index, ctx=ctx), axis=Axis.ALONG, element=index, what="row")


def _shifted(value: float, axis: Axis, *, index: int, ctx: RenderContext) -> float:
    """Move a position by the printer's offset; one pushed past the label's 0 edge lands on it.

    An offset the caller set and that pushes a field off the label is refused: the caller measured
    that printer and must move the field. An offset that is only the model's default is a
    deviation instead: without the correction the printer already put such a field on the edge
    row, so placing it there prints exactly what the uncorrected label printed.
    """
    shifted = ctx.shifted(value, axis=axis)
    # Below a micrometre is float noise from the subtraction, not a field off the label.
    if shifted < -_TOLERANCE:
        _report_past_edge(shifted, axis, index=index, ctx=ctx)
    return max(shifted, 0.0)


def position_units(value: float, *, axis: Axis, ctx: RenderContext) -> int:
    """Return the DPL row or column a field at ``value`` is written at, recording nothing.

    The same arithmetic as :func:`row_of` and :func:`column_of`: moved by the printer's offset,
    kept on the label's 0 edge, converted to this job's unit. Their checks and findings are left
    to them; this is for a step that must know where a field prints without encoding it.

    Example:
        >>> from datamax_dpl.domain.label import Label, Text
        >>> from datamax_dpl.domain.render import job_context
        >>> from datamax_dpl.domain.render_context import RenderOptions
        >>> ctx = job_context(Label(elements=[Text(x=0, y=0, text="x")]), RenderOptions(model="allegro-2"))
        >>> # 0 mm moved down by the Allegro 2's -0.25 mm along offset stays on the edge.
        >>> position_units(0.0, axis=Axis.ALONG, ctx=ctx), position_units(10.0, axis=Axis.ACROSS, ctx=ctx)
        (0, 40)
    """
    return ctx.dpl_units(max(ctx.shifted(value, axis=axis), 0.0), axis=axis)


def _report_past_edge(shifted: float, axis: Axis, *, index: int, ctx: RenderContext) -> None:
    """Record a field the offset moves past the label's 0 edge: refused, or clamped for a model default."""
    offset = ctx.offset_mm(axis)
    past = f"{_readable(-shifted, ctx.unit)} {ctx.unit.value} past the label edge"
    if axis in ctx.model_offset_axes:
        ctx.findings.deviate(
            FindingCode.MODEL_OFFSET_CLAMPED,
            f"the {ctx.profile.name} default {axis.value} offset of {offset:g} mm would move this field {past}",
            fallback=(
                f"placed on the label edge, since the model's default {axis.value} offset of {offset:g} mm "
                f"would move it past; set offset_{axis.value} = 0 to print without that default"
            ),
            element=index,
        )
    else:
        ctx.findings.refuse(
            FindingCode.OFFSET_PAST_LABEL_EDGE,
            f"the printer's {axis.value} offset of {offset:g} mm moves this field {past}",
            element=index,
        )


def _readable(distance: float, unit: Unit) -> str:
    """Return ``distance`` at the precision a label in ``unit`` is written in.

    The millimetre offset converted to dots or inches carries float noise (0.25 mm is 1.998 Allegro 2
    dots), which a message should not show.

    Example:
        >>> _readable(1.99803, Unit.DOTS), _readable(0.00984252, Unit.INCH), _readable(0.5, Unit.MM)
        ('2', '0.01', '0.5')
    """
    return str(round(distance)) if unit is Unit.DOTS else f"{round(distance, 2):g}"


def check_field_length(length: int, *, limit: int = MAX_FIELD_DATA, index: int, ctx: RenderContext) -> None:
    """Refuse field data longer than the record limit or the model's per-field limit."""
    allowed = min(limit, ctx.profile.max_field_chars)
    if length > allowed:
        ctx.findings.refuse(
            FindingCode.FIELD_TOO_LONG, f"field data has {length} characters; at most {allowed} fit", element=index
        )


def attribute_lines(attribute: Attribute | None, *, index: int, ctx: RenderContext, text: bool) -> list[bytes]:
    """Return the ``An`` line that switches to ``attribute`` before a field, or nothing.

    Inverse text has no fallback on a model without attributes; other attributes are dropped there.
    """
    if attribute is None or attribute is ctx.default_attribute:
        return []
    if ctx.supports(Feature.ATTRIBUTE):
        return [line(f"A{attribute.value}")]
    message = f"attribute {attribute.name.lower()}: {ctx.requirement(Feature.ATTRIBUTE)}"
    if text and attribute is Attribute.INVERSE:
        ctx.findings.refuse(FindingCode.ATTRIBUTE_UNSUPPORTED, message, element=index)
    else:
        ctx.findings.deviate(
            FindingCode.ATTRIBUTE_UNSUPPORTED, message, fallback="printed with the default attribute", element=index
        )
    return []


def attribute_reset(attribute: Attribute | None, *, ctx: RenderContext) -> list[bytes]:
    """Return the line that restores the format's default attribute after a field that changed it."""
    if attribute is None or attribute is ctx.default_attribute or not ctx.supports(Feature.ATTRIBUTE):
        return []
    return [line(f"A{ctx.default_attribute.value}")]


def counter_lines(counter: Counter | None) -> list[bytes]:
    """Return the increment/decrement line that makes the previous field count.

    Example:
        >>> counter_lines(Counter(step=1))
        [b'+001\\r']
        >>> counter_lines(Counter(step=5, decrement=True, alphanumeric=True, fill=" "))
        [b'< 05\\r']
    """
    if counter is None:
        return []
    sign = ("<" if counter.decrement else ">") if counter.alphanumeric else "-" if counter.decrement else "+"
    return [line(f"{sign}{counter.fill}{counter.step:02d}")]


__all__ = [
    "attribute_lines",
    "attribute_reset",
    "check_field_length",
    "column_of",
    "counter_lines",
    "image_edge",
    "last_column",
    "position_units",
    "row_of",
]
