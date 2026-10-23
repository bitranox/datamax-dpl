"""Helpers every element encoder shares: positions, field length, attributes and counters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .enums import Attribute, Unit
from .label import MAX_FIELD_DATA, Counter
from .profiles import Feature
from .records import line
from .units import DplUnit, to_dpl

if TYPE_CHECKING:
    from .render_context import RenderContext


def column_of(x: float, *, index: int, ctx: RenderContext) -> int:
    """Convert ``x`` to a DPL column, refusing positions past the model's printable width."""
    column = ctx.distance(x, element=index, what="column")
    limit = to_dpl(ctx.profile.max_column / 100, Unit.INCH, target=ctx.dpl_unit, dpi=ctx.profile.dpi).value
    if column > limit:
        unit = "1/100 in" if ctx.dpl_unit is DplUnit.INCH_HUNDREDTHS else "1/10 mm"
        ctx.findings.refuse(
            "outside_print_width",
            f"column {column} {unit} is past the printable width of {ctx.profile.name} ({limit} {unit})",
            element=index,
        )
    return column


def row_of(y: float, *, index: int, ctx: RenderContext) -> int:
    """Convert ``y`` to a DPL row."""
    return ctx.distance(y, element=index, what="row")


def check_field_length(length: int, *, limit: int = MAX_FIELD_DATA, index: int, ctx: RenderContext) -> None:
    """Refuse field data longer than the record limit or the model's per-field limit."""
    allowed = min(limit, ctx.profile.max_field_chars)
    if length > allowed:
        ctx.findings.refuse(
            "field_too_long", f"field data has {length} characters; at most {allowed} fit", element=index
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
        ctx.findings.refuse("attribute_unsupported", message, element=index)
    else:
        ctx.findings.deviate(
            "attribute_unsupported", message, fallback="printed with the default attribute", element=index
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


__all__ = ["attribute_lines", "attribute_reset", "check_field_length", "column_of", "counter_lines", "row_of"]
