"""Encode barcode fields: symbology gates, data checks, bar widths and 2-D parameters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .barcodes import check_barcode, datamatrix_data_field, pdf417_data_field
from .encode_common import check_field_length, column_of, counter_lines, row_of
from .enums import FindingCode, Rotation
from .label import MAX_FIELD_DATA, MAX_MULTIPLIER, MAX_PDF417_DATA, Barcode, DataMatrixOptions, Pdf417Options
from .profiles import Feature
from .records import MAX_SHORT_FIELD, Header, line
from .symbology import SYMBOLOGY_CODES, Symbology
from .units import Axis

if TYPE_CHECKING:
    from .render_context import RenderContext

#: Symbologies the manual describes as wide:narrow ratio codes; the others are module codes.
RATIO_SYMBOLOGIES = frozenset(
    {
        Symbology.CODE39,
        Symbology.I2OF5,
        Symbology.I2OF5_MOD10,
        Symbology.I2OF5_BEARER,
        Symbology.HIBC,
        Symbology.CODABAR,
        Symbology.PLESSEY,
    }
)

#: Symbologies that need the extended barcode set (missing on Allegro and the Prodigy family).
_EXTENDED = frozenset({Symbology.UCC_EAN128_RANDOM_WEIGHT, Symbology.TELEPEN, Symbology.MAXICODE, Symbology.FIM})

#: Bar widths used when the model cannot take ``00`` (printer default): narrow 2 dots, ratio 3:1.
FALLBACK_NARROW = 2
FALLBACK_RATIO = 3


def _gate(element: Barcode, *, index: int, ctx: RenderContext) -> bool:
    """Return False (after a refusal) when the model cannot print this symbology at all."""
    feature = None
    if element.symbology in _EXTENDED:
        feature = Feature.EXTENDED_BARCODES
    elif element.symbology is Symbology.PDF417:
        feature = Feature.PDF417
    if feature is not None and not ctx.supports(feature):
        ctx.findings.refuse(
            FindingCode.SYMBOLOGY_UNSUPPORTED,
            f"{element.symbology.value}: {ctx.requirement(feature)}",
            element=index,
        )
        return False
    if element.symbology is Symbology.DATAMATRIX and not ctx.supports(Feature.DATAMATRIX):
        ctx.findings.deviate(
            FindingCode.DATAMATRIX_OPTION_UNCONFIRMED,
            "DataMatrix prints only on printers with the DataMatrix option",
            fallback="sent anyway; declare the option to silence this",
            element=index,
        )
    return True


def _data_field(element: Barcode, *, index: int, ctx: RenderContext) -> str:
    check = check_barcode(element.symbology, element.data, pdf417=element.pdf417, datamatrix=element.datamatrix)
    for problem in check.problems:
        ctx.findings.refuse(problem.code.finding_code, problem.message, element=index)
    if check.computed_check_digit is not None:
        ctx.findings.info(
            FindingCode.CHECK_DIGIT_ADDED,
            f"the printer adds check digit {check.computed_check_digit}",
            element=index,
        )
    if element.symbology is Symbology.PDF417:
        return pdf417_data_field(check.data, element.pdf417 or Pdf417Options())
    if element.symbology is Symbology.DATAMATRIX:
        return datamatrix_data_field(check.data, element.datamatrix or DataMatrixOptions())
    return check.data


@dataclass(frozen=True)
class BarWidths:
    """The bar widths a barcode record's header writes, in dots.

    Attributes:
        wide: The header's ``c`` digit: the wide bar of a ratio code, else the module width.
        narrow: The header's ``d`` digit: the narrow bar of a ratio code, else the module width.
    """

    wide: int
    narrow: int


#: Both widths zero: the printer's own default bar widths.
_PRINTER_DEFAULT_WIDTHS = BarWidths(wide=0, narrow=0)


def _ratio_widths(*, narrow_dots: int, wide_dots: int) -> BarWidths:
    narrow = narrow_dots or max(1, wide_dots // FALLBACK_RATIO)
    wide = wide_dots or min(MAX_MULTIPLIER, narrow * FALLBACK_RATIO)
    return BarWidths(wide=wide, narrow=narrow)


def bar_widths(element: Barcode, *, index: int, ctx: RenderContext) -> BarWidths:
    """Return the header's ``c`` and ``d`` digits for ``element``.

    Ratio codes take the wide and the narrow bar; module codes and 2-D codes take the module
    width twice. Both zero asks for the printer default where the model allows it.
    """
    narrow_dots, wide_dots = element.narrow_dots, element.wide_dots
    wants_default = narrow_dots == 0 and wide_dots == 0
    if wants_default and ctx.supports(Feature.DEFAULT_BAR_WIDTHS):
        return _PRINTER_DEFAULT_WIDTHS
    if wants_default:
        ctx.findings.deviate(
            FindingCode.DEFAULT_BAR_WIDTHS_UNSUPPORTED,
            f"{ctx.profile.name} has no default bar widths",
            fallback=f"narrow bar {FALLBACK_NARROW} dots, ratio {FALLBACK_RATIO}:1",
            element=index,
        )
        narrow_dots = FALLBACK_NARROW
    if element.symbology in RATIO_SYMBOLOGIES:
        return _ratio_widths(narrow_dots=narrow_dots, wide_dots=wide_dots)
    module = narrow_dots or wide_dots
    return BarWidths(wide=module, narrow=module)


def _height_field(element: Barcode, *, index: int, ctx: RenderContext) -> str:
    if element.height is None:
        return "000"
    # The height runs along the feed unless the field is turned a quarter turn.
    axis = Axis.ACROSS if element.rotation in (Rotation.DEG_90, Rotation.DEG_270) else Axis.ALONG
    height = ctx.distance(element.height, axis=axis, element=index, what="barcode height")
    if not 1 <= height <= MAX_SHORT_FIELD:
        ctx.findings.refuse(
            FindingCode.BARCODE_HEIGHT_OUT_OF_RANGE,
            f"barcode height is {height} units; DPL takes 1-{MAX_SHORT_FIELD}",
            element=index,
        )
        height = min(max(height, 1), MAX_SHORT_FIELD)
    return f"{height:03d}"


def encode_barcode(element: Barcode, *, index: int, ctx: RenderContext) -> list[bytes]:
    """Return the format lines of a barcode field."""
    if not _gate(element, index=index, ctx=ctx):
        return []
    code = SYMBOLOGY_CODES[element.symbology]
    if element.human_readable and code.with_text is None:
        ctx.findings.info(
            FindingCode.NO_HUMAN_READABLE, f"{element.symbology.value} prints no text line", element=index
        )
    data = _data_field(element, index=index, ctx=ctx)
    limit = MAX_PDF417_DATA if element.symbology is Symbology.PDF417 else MAX_FIELD_DATA
    check_field_length(len(data), limit=limit, index=index, ctx=ctx)
    widths = bar_widths(element, index=index, ctx=ctx)
    head = Header(
        rotation=element.rotation,
        kind=code.identifier(human_readable=element.human_readable),
        width=widths.wide,
        height=widths.narrow,
        size=_height_field(element, index=index, ctx=ctx),
        row=row_of(element.y, index=index, ctx=ctx),
        column=column_of(element.x, index=index, ctx=ctx),
    ).encode()
    return [line(head + data.encode("latin-1")), *counter_lines(element.counter)]


__all__ = ["FALLBACK_NARROW", "FALLBACK_RATIO", "RATIO_SYMBOLOGIES", "BarWidths", "bar_widths", "encode_barcode"]
