"""Filling ``{name}`` placeholders from data records."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from datamax_dpl.domain.label import Barcode, Label, Text
from datamax_dpl.domain.placeholders import MissingFieldError, fill_label, fill_text, label_placeholders

TEMPLATE = Label.model_validate(
    {
        "elements": [
            {"type": "text", "x": 1, "y": 1, "text": "Lot {lot} - {qty} pcs"},
            {"type": "barcode", "x": 1, "y": 10, "symbology": "code128", "data": "{sku}"},
            {"type": "box", "x": 0, "y": 0, "width": 50, "height": 20, "thickness": 0.5},
        ]
    }
)


@pytest.mark.os_agnostic
def test_placeholders_are_listed_once_in_order() -> None:
    assert label_placeholders(TEMPLATE) == ["lot", "qty", "sku"]


@pytest.mark.os_agnostic
def test_fill_label_replaces_text_and_barcode_data_only() -> None:
    filled = fill_label(TEMPLATE, {"lot": "A7", "qty": "12", "sku": "SKU-1", "x": "99"})
    text, barcode = filled.elements[0], filled.elements[1]
    assert isinstance(text, Text) and text.text == "Lot A7 - 12 pcs"
    assert isinstance(barcode, Barcode) and barcode.data == "SKU-1"
    assert filled.elements[2] == TEMPLATE.elements[2]
    assert label_placeholders(filled) == []


@pytest.mark.os_agnostic
def test_a_missing_field_is_named() -> None:
    with pytest.raises(MissingFieldError, match="has no field 'sku'"):
        fill_label(TEMPLATE, {"lot": "A7", "qty": "1"})


@pytest.mark.os_agnostic
def test_doubled_braces_stay_literal_and_values_are_not_reinterpreted() -> None:
    assert fill_text("{{a}} {a}", {"a": "{b}"}) == "{a} {b}"


@pytest.mark.os_agnostic
def test_a_backslash_in_a_value_is_copied_verbatim() -> None:
    assert fill_text("{p}", {"p": "C:\\temp\\1"}) == "C:\\temp\\1"


@pytest.mark.os_agnostic
def test_a_value_that_breaks_a_field_limit_is_refused() -> None:
    with pytest.raises(ValidationError):
        fill_label(TEMPLATE, {"lot": "x" * 300, "qty": "1", "sku": "s"})


@pytest.mark.os_agnostic
def test_every_field_filling_breaks_is_named_as_label_validation_names_it() -> None:
    with pytest.raises(ValidationError) as caught:
        fill_label(TEMPLATE, {"lot": "x" * 300, "qty": "1", "sku": "s" * 4000})
    assert caught.value.title == "Label"
    assert [(e["loc"], e["type"]) for e in caught.value.errors()] == [
        (("elements", 0, "text", "text"), "string_too_long"),
        (("elements", 1, "barcode", "data"), "string_too_long"),
    ]


@pytest.mark.os_agnostic
def test_a_missing_field_is_reported_before_a_field_filling_made_too_long() -> None:
    with pytest.raises(MissingFieldError, match="has no field 'sku'"):
        fill_label(TEMPLATE, {"lot": "x" * 300, "qty": "1"})


@pytest.mark.os_agnostic
def test_filling_leaves_the_template_unchanged() -> None:
    fill_label(TEMPLATE, {"lot": "A7", "qty": "12", "sku": "SKU-1"})
    text = TEMPLATE.elements[0]
    assert isinstance(text, Text) and text.text == "Lot {lot} - {qty} pcs"
