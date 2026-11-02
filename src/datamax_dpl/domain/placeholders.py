"""``{name}`` placeholders in text, barcode and QR data, filled from one data record per label.

A record is a flat mapping of field names to strings (one CSV row, one JSON object). ``{{`` and
``}}`` stand for literal braces. Filling never touches anything but the text of ``text``,
``scalable_text``, ``barcode`` and ``qr`` elements, so coordinates and settings cannot be injected.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, TypedDict

from ._invariants import require_update_fields
from .errors import DatamaxDplError
from .label import MAX_FIELD_DATA, MAX_PDF417_DATA, MAX_QR_DATA, Barcode, Element, Label, Qr, ScalableText, Text

if TYPE_CHECKING:
    from collections.abc import Mapping

_TOKEN = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")


class _TextUpdate(TypedDict):
    """The field filling sets on a text or scalable text element."""

    text: str


class _DataUpdate(TypedDict):
    """The field filling sets on a barcode or QR element."""

    data: str


class _ElementsUpdate(TypedDict):
    """The field filling sets on the label: its filled elements."""

    elements: list[Element]


require_update_fields(_TextUpdate, Text, ScalableText)
require_update_fields(_DataUpdate, Barcode, Qr)
require_update_fields(_ElementsUpdate, Label)


class MissingFieldError(DatamaxDplError, LookupError):
    """A placeholder names a field the data record does not have."""


def placeholders_in(text: str) -> list[str]:
    """Return the placeholder names in ``text``, in order.

    Example:
        >>> placeholders_in("Lot {lot} / {{literal}} {qty}")
        ['lot', 'qty']
    """
    return [match[1] for match in _TOKEN.finditer(text) if match[1] is not None]


def fill_text(text: str, record: Mapping[str, str], *, limit: int | None = None) -> str:
    """Replace every ``{name}`` in ``text`` by ``record[name]``.

    Args:
        text: The text holding the placeholders.
        record: The value of each field name.
        limit: Stop building the result once it is longer than this and return its first
            ``limit + 1`` characters. A field that cannot hold it is refused anyway, and a short
            template repeating a large value would otherwise take gigabytes to learn that. None
            builds the whole text.

    Returns:
        The filled text; longer than ``limit`` exactly when the whole text would be.

    Raises:
        MissingFieldError: A placeholder has no value in ``record``; checked past ``limit`` too.

    Example:
        >>> fill_text("Lot {lot} {{x}}", {"lot": "A7"})
        'Lot A7 {x}'
        >>> fill_text("{a}{a}{a}", {"a": "xyz"}, limit=4)
        'xyzxy'
    """
    pieces: list[str] = []
    length = 0
    end = 0
    for match in _TOKEN.finditer(text):
        value = _token_value(match, record)
        if limit is None or length <= limit:
            pieces += (text[end : match.start()], value)
            length += match.start() - end + len(value)
        end = match.end()
    pieces.append(text[end:])
    return "".join(pieces) if limit is None else "".join(pieces)[: limit + 1]


def _token_value(match: re.Match[str], record: Mapping[str, str]) -> str:
    """What one token stands for: a literal brace, or the value its field holds."""
    name = match[1]
    if name is None:
        return match[0][0]
    if name not in record:
        msg = f"data record has no field {name!r}"
        raise MissingFieldError(msg)
    return str(record[name])


def _fill_element(element: Element, record: Mapping[str, str]) -> Element:
    """Fill one element, building no more than its own field's limit can hold plus one character."""
    if isinstance(element, (Text, ScalableText)):
        return element.model_copy(update=_TextUpdate(text=fill_text(element.text, record, limit=MAX_FIELD_DATA)))
    if isinstance(element, Barcode):
        return element.model_copy(update=_DataUpdate(data=fill_text(element.data, record, limit=MAX_PDF417_DATA)))
    if isinstance(element, Qr):
        return element.model_copy(update=_DataUpdate(data=fill_text(element.data, record, limit=MAX_QR_DATA)))
    return element


def fill_label(label: Label, record: Mapping[str, str]) -> Label:
    """Return a copy of ``label`` with every placeholder filled from ``record``.

    The copy is validated again, so a value that makes a field too long is refused here.

    Args:
        label: The label holding the placeholders.
        record: The value of each field name.

    Returns:
        The filled copy; ``label`` is unchanged.

    Raises:
        MissingFieldError: A placeholder has no value in ``record``.
        pydantic.ValidationError: A filled field breaks the label model's limits.
    """
    # Every element is filled before anything is validated, so a missing field is reported
    # ahead of a field that filling made too long, whichever element comes first. The label
    # models set revalidate_instances, which is what makes this model_validate re-check the copy.
    elements = [_fill_element(element, record) for element in label.elements]
    return Label.model_validate(label.model_copy(update=_ElementsUpdate(elements=elements))).marked_filled()


def label_placeholders(label: Label) -> list[str]:
    """Return every distinct placeholder name the label uses, in first-seen order."""
    names: list[str] = []
    for element in label.elements:
        if isinstance(element, (Barcode, Qr)):
            text = element.data
        elif isinstance(element, (Text, ScalableText)):
            text = element.text
        else:
            continue
        names.extend(n for n in placeholders_in(text) if n not in names)
    return names


__all__ = ["MissingFieldError", "fill_label", "fill_text", "label_placeholders", "placeholders_in"]
