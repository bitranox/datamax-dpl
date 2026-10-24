"""``{name}`` placeholders in text and barcode data, filled from one data record per label.

A record is a flat mapping of field names to strings (one CSV row, one JSON object). ``{{`` and
``}}`` stand for literal braces. Filling never touches anything but the text of ``text``,
``scalable_text`` and ``barcode`` elements, so coordinates and settings cannot be injected.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .label import Barcode, Element, Label, ScalableText, Text

if TYPE_CHECKING:
    from collections.abc import Mapping

_TOKEN = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")


class MissingFieldError(LookupError):
    """A placeholder names a field the data record does not have."""


def placeholders_in(text: str) -> list[str]:
    """Return the placeholder names in ``text``, in order.

    Example:
        >>> placeholders_in("Lot {lot} / {{literal}} {qty}")
        ['lot', 'qty']
    """
    return [match[1] for match in _TOKEN.finditer(text) if match[1] is not None]


def fill_text(text: str, record: Mapping[str, str]) -> str:
    """Replace every ``{name}`` in ``text`` by ``record[name]``.

    Raises:
        MissingFieldError: A placeholder has no value in ``record``.

    Example:
        >>> fill_text("Lot {lot} {{x}}", {"lot": "A7"})
        'Lot A7 {x}'
    """

    def substitute(match: re.Match[str]) -> str:
        name = match[1]
        if name is None:
            return match[0][0]
        if name not in record:
            msg = f"data record has no field {name!r}"
            raise MissingFieldError(msg)
        return str(record[name])

    return _TOKEN.sub(substitute, text)


def _fill_element(element: Element, record: Mapping[str, str]) -> Element:
    if isinstance(element, (Text, ScalableText)):
        return element.model_copy(update={"text": fill_text(element.text, record)})
    if isinstance(element, Barcode):
        return element.model_copy(update={"data": fill_text(element.data, record)})
    return element


def fill_label(label: Label, record: Mapping[str, str]) -> Label:
    """Return a copy of ``label`` with every placeholder filled from ``record``.

    The copy is validated again, so a value that makes a field too long is refused here.

    Raises:
        MissingFieldError: A placeholder has no value in ``record``.
        pydantic.ValidationError: A filled field breaks the label model's limits.
    """
    # Every element is filled before anything is validated, so a missing field is reported
    # ahead of a field that filling made too long, whichever element comes first. The label
    # models set revalidate_instances, which is what makes this model_validate re-check the copy.
    elements = [_fill_element(element, record) for element in label.elements]
    return Label.model_validate(label.model_copy(update={"elements": elements}))


def label_placeholders(label: Label) -> list[str]:
    """Return every distinct placeholder name the label uses, in first-seen order."""
    names: list[str] = []
    for element in label.elements:
        if isinstance(element, Barcode):
            text = element.data
        elif isinstance(element, (Text, ScalableText)):
            text = element.text
        else:
            continue
        names.extend(n for n in placeholders_in(text) if n not in names)
    return names


__all__ = ["MissingFieldError", "fill_label", "fill_text", "label_placeholders", "placeholders_in"]
