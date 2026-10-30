"""Emulate a transparent label on a model without the ``A`` (attribute) command.

Such a model prints every field XOR: a dot two fields share prints white. When a label asks for
``attribute`` 2 (transparent) there, the fields that share dots (:mod:`~datamax_dpl.domain.overlap`)
are drawn OR into one downloaded image at the group's frame and placed by one image record; the
group's own records are not sent. Every other field is encoded as before. Unverified on paper.

What is found is reported, each finding on the element indices of the label:

* ``transparency_emulated`` (deviation) per merged group, naming its elements, the image's size and
  the bytes it sends; image data is slow over a serial link (about 1 KB/s at 9600 baud).
* ``transparency_not_emulated`` (deviation) per text, barcode or stored image whose rectangle meets
  another field: the library does not know the printer's glyphs or bars, so those still print XOR.
* ``transparency_not_needed`` (info) when nothing overlaps: the label prints the same either way.

Only ``attribute`` 2 is emulated. XOR (1) is what such a model prints anyway, and opaque (3) and
inverse (5) keep the ``attribute_unsupported`` deviation.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .encode_graphics import encode_box, encode_circle, encode_image, encode_line, encode_polygon, encode_qr
from .enums import Attribute, FallbackPolicy, FindingCode, Severity
from .findings import FindingCollector
from .label import Box, Circle, Image, Line, Polygon, Qr
from .overlap import find_overlaps, printed_dots
from .profiles import Feature
from .raster import Bitmap
from .records import image_record, line
from .render_context import ImageEdge

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .label import Element, Label
    from .overlap import PrintedDots
    from .render_context import PlacedImage, RenderContext


def emulates_transparency(label: Label, ctx: RenderContext) -> bool:
    """Return whether this job draws overlapping fields into one image: transparent asked, no ``A`` command."""
    return label.attribute is Attribute.TRANSPARENT and not ctx.supports(Feature.ATTRIBUTE)


@dataclass(frozen=True)
class Emulation:
    """What the pre-pass did to the label's fields.

    Attributes:
        placed: The format lines of each merged group, keyed by the group's first element, which
            is where they go among the records.
        merged: Every element drawn into a group's image; none of them is encoded on its own.
    """

    placed: dict[int, list[bytes]] = field(default_factory=dict[int, list[bytes]])
    merged: frozenset[int] = frozenset()


#: Deviations a member's own encoder records that hold for it inside the merged image too: it is
#: placed where its own record would be, so a position clamped on the label edge stays clamped.
_KEPT_DEVIATIONS = frozenset({FindingCode.MODEL_OFFSET_CLAMPED})
#: Refusals that say a member does not fit the print width; the merged image holding it then gets
#: no second finding for its own right edge.
_PRINT_WIDTH_REFUSALS = frozenset(
    {FindingCode.OUTSIDE_PRINT_WIDTH, FindingCode.IMAGE_PAST_PRINT_WIDTH, FindingCode.QR_PAST_PRINT_WIDTH}
)


def _member_findings(elements: Sequence[Element], group: tuple[int, ...], ctx: RenderContext) -> frozenset[FindingCode]:
    """Record what each member's own encoder refuses, and where it clamps the member's position.

    The member is encoded on a scratch copy of the context and its lines and downloads are thrown
    away; refusals (past the print width, a QR Code that fits no version) and a clamped position
    carry over, while every other fallback the encoder would apply is replaced by the merge.
    The copy never halves: the merged image is halved as a whole, so a member's own magnification
    never has to double.

    Returns:
        The codes of the refusals carried over.
    """
    scratch = dataclasses.replace(
        ctx,
        findings=FindingCollector(FallbackPolicy.DEGRADE),
        downloads=list(ctx.downloads),
        system_suffix=[],
        halve_images=False,
        halved_elements=[],
        emulated_groups=[],
    )
    for index in group:
        _encode_member(elements[index], index=index, ctx=scratch)
    refused: set[FindingCode] = set()
    for finding in scratch.findings.findings():
        if finding.severity is Severity.ERROR:
            refused.add(finding.code)
            ctx.findings.refuse(finding.code, finding.message, element=finding.element)
        elif finding.code in _KEPT_DEVIATIONS and finding.fallback is not None:
            ctx.findings.deviate(finding.code, finding.message, fallback=finding.fallback, element=finding.element)
    return frozenset(refused)


def _encode_member(element: Element, *, index: int, ctx: RenderContext) -> None:
    if isinstance(element, Line):
        encode_line(element, index=index, ctx=ctx)
    elif isinstance(element, Box):
        encode_box(element, index=index, ctx=ctx)
    elif isinstance(element, Circle):
        encode_circle(element, index=index, ctx=ctx)
    elif isinstance(element, Polygon):
        encode_polygon(element, index=index, ctx=ctx)
    elif isinstance(element, Image):
        encode_image(element, index=index, ctx=ctx)
    elif isinstance(element, Qr):
        encode_qr(element, index=index, ctx=ctx)


@dataclass(frozen=True)
class _Merged:
    """A group's dots drawn into one picture, and the DPL column and row its record is written at.

    The members' own record positions are kept as they are: the picture starts at the smallest
    column and row any member is written at, so each member's dots land where its own record
    would print them.
    """

    bitmap: Bitmap
    column: int
    row: int


def _merge(members: list[PrintedDots]) -> _Merged:
    """OR every member's dots into one picture of the group's frame (row 0 its top row)."""
    left = min(member.area.left for member in members)
    bottom = min(member.area.bottom for member in members)
    right = max(member.area.right for member in members)
    top = max(member.area.top for member in members)
    bitmap = Bitmap(right - left + 1, top - bottom + 1)
    for member in members:
        bitmap.blit(member.bitmap, x=member.area.left - left, y=top - member.area.top)
    column = min(member.column for member in members)
    row = min(member.row for member in members)
    return _Merged(bitmap=bitmap, column=column, row=row)


def _keep_named_downloads(label: Label, group: tuple[int, ...], ctx: RenderContext) -> None:
    """Still download a merged image under its own name when a field outside the group places that name."""
    for index in group:
        element = label.elements[index]
        if not isinstance(element, Image) or element.bitmap is None:
            continue
        if any(
            isinstance(other, Image) and other.name == element.name
            for position, other in enumerate(label.elements)
            if position not in group
        ):
            encode_image(element, index=index, ctx=ctx)


def _bytes_sent(placed: PlacedImage, ctx: RenderContext) -> int:
    return next(download.data_bytes for download in ctx.downloads if download.name == placed.name)


def _emulate_group(label: Label, group: tuple[int, ...], ctx: RenderContext) -> list[bytes]:
    """Draw ``group`` into one downloaded image and return its placing line; nothing when it is refused.

    The merged image is a drawn image like a raster fallback: one that ends past the print width
    is refused (``image_past_print_width``, also when it holds a QR Code), unless a member is
    already refused for the print width, which says why.
    """
    refused = _member_findings(label.elements, group, ctx)
    members = [printed_dots(label.elements[index], ctx) for index in group]
    merged = _merge([member for member in members if member is not None])
    first = group[0]
    # A QR Code's image holds one dot per module, so halving the group would merge its modules.
    halvable = not any(isinstance(label.elements[index], Qr) for index in group)
    edge = ImageEdge(
        column=None if refused & _PRINT_WIDTH_REFUSALS else merged.column,
        what=f"merged image of elements {', '.join(map(str, group))}",
    )
    placed = ctx.download_drawn_image(merged.bitmap, element=first, edge=edge, halvable=halvable)
    _keep_named_downloads(label, group, ctx)
    if placed is None:
        return []
    if placed.magnification != 1:
        ctx.halved_elements.extend(group[1:])
    ctx.emulated_groups.append(group)
    width, height = placed.size or (merged.bitmap.width, merged.bitmap.height)
    ctx.findings.deviate(
        FindingCode.TRANSPARENCY_EMULATED,
        f"elements {', '.join(map(str, group))} overlap; {ctx.profile.name} has no attribute command "
        "and would print their common dots white (XOR)",
        fallback=(
            f"drawn together into downloaded image {placed.name} ({width}x{height} dots, "
            f"{_bytes_sent(placed, ctx)} bytes); unverified on paper"
        ),
        element=first,
    )
    return [line(image_record(row=merged.row, column=merged.column, name=placed.name, magnification=placed.dots))]


def _report_inexact(label: Label, inexact: tuple[int, ...], ctx: RenderContext) -> None:
    for index in inexact:
        ctx.findings.deviate(
            FindingCode.TRANSPARENCY_NOT_EMULATED,
            f"the {label.elements[index].type} element overlaps another field; its dots come from the printer, "
            f"so those fields print XOR on {ctx.profile.name}",
            fallback="printed XOR",
            element=index,
        )


def emulate_transparency(label: Label, ctx: RenderContext) -> Emulation:
    """Merge the label's overlapping fields into images when the job emulates transparency.

    Args:
        label: The label, placeholders already filled.
        ctx: The render context; receives the downloads, the findings and the merged groups.

    Returns:
        What to send for each merged group and which elements are merged; empty when the job
        does not emulate transparency (:func:`emulates_transparency`).
    """
    if not emulates_transparency(label, ctx):
        return Emulation()
    report = find_overlaps(label.elements, ctx)
    if not report.groups and not report.inexact:
        ctx.findings.info(
            FindingCode.TRANSPARENCY_NOT_NEEDED,
            f"attribute not sent: {ctx.requirement(Feature.ATTRIBUTE)}; "
            "no fields overlap, so the label prints the same",
        )
        return Emulation()
    placed = {group[0]: _emulate_group(label, group, ctx) for group in report.groups}
    _report_inexact(label, report.inexact, ctx)
    return Emulation(placed=placed, merged=frozenset(index for group in report.groups for index in group))


__all__ = ["Emulation", "emulate_transparency", "emulates_transparency"]
