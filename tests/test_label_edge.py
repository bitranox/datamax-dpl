"""A field that ends past the label's own width or height is reported: a warning, an error under strict.

The user's decision of 2026-11-01 ([145]): a new finding ``element_past_label_edge`` names the element and by
how many mm it passes which edge, checked from each element's printed dot area; continuous media has no fixed
height, so only its width is checked.
"""

from __future__ import annotations

from typing import Any

import pytest

from datamax_dpl.domain.enums import FallbackPolicy, FindingCode, Severity
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions

pytestmark = pytest.mark.os_agnostic

_QR_TALLER = {"type": "qr", "x": 2, "y": 2, "data": "x" * 800, "module_dots": 4}
_BOX_WIDER = {"type": "box", "x": 60, "y": 5, "width": 30, "height": 10, "thickness": 0.5}
_TEXT_HIGHER = {"type": "text", "x": 5, "y": 52, "text": "HELLO", "font": 4}


def _label(element: dict[str, Any], **extra: Any) -> Label:
    return Label.model_validate({"unit": "mm", "width": 80, "height": 55, "elements": [element], **extra})


def _edge(label: Label, policy: FallbackPolicy = FallbackPolicy.DEGRADE, **options: Any) -> list[Any]:
    result = build_job(label, RenderOptions(model="allegro-2", policy=policy, **options))
    return [f for f in result.findings if f.code is FindingCode.ELEMENT_PAST_LABEL_EDGE]


@pytest.mark.parametrize(
    ("element", "edge"),
    [(_QR_TALLER, "height"), (_BOX_WIDER, "width"), (_TEXT_HIGHER, "height")],
)
def test_a_field_past_an_edge_is_a_warning_naming_the_edge_and_the_mm(element: dict[str, Any], edge: str) -> None:
    found = _edge(_label(element))

    assert len(found) == 1
    assert found[0].severity is Severity.WARNING
    assert found[0].element == 0
    assert edge in found[0].message
    assert " mm" in found[0].message


@pytest.mark.parametrize("element", [_QR_TALLER, _BOX_WIDER, _TEXT_HIGHER])
def test_strict_refuses_a_field_past_an_edge(element: dict[str, Any]) -> None:
    found = _edge(_label(element), FallbackPolicy.STRICT)

    assert [f.severity for f in found] == [Severity.ERROR]


def test_the_box_passes_the_width_by_its_overhang() -> None:
    """x 60 + 30 mm on an 80 mm label: about 10 mm past the width."""
    (finding,) = _edge(_label(_BOX_WIDER))

    assert "10.0 mm" in finding.message or "9.9 mm" in finding.message or "10.1 mm" in finding.message


@pytest.mark.parametrize(
    "element",
    [
        {"type": "box", "x": 0, "y": 0, "width": 80, "height": 55, "thickness": 0.5},
        {"type": "line", "x": 0, "y": 54.5, "x2": 80, "y2": 54.5, "thickness": 0.5},
        {"type": "text", "x": 5, "y": 5, "text": "HELLO", "font": 4},
        {"type": "qr", "x": 2, "y": 2, "data": "short", "module_dots": 4},
    ],
)
def test_a_field_inside_the_label_has_no_finding(element: dict[str, Any]) -> None:
    """A frame drawn exactly on the label's edges is inside it, offset or not."""
    assert _edge(_label(element)) == []
    assert _edge(_label(element), offset_along=0.5, offset_across=0.5) == []


def test_a_label_without_a_size_is_not_checked() -> None:
    label = Label.model_validate({"unit": "mm", "elements": [_BOX_WIDER]})

    assert _edge(label) == []


def test_continuous_media_checks_the_width_only() -> None:
    assert _edge(_label(_TEXT_HIGHER, media="continuous", continuous_length=55)) == []
    assert len(_edge(_label(_BOX_WIDER, media="continuous", continuous_length=55))) == 1


def test_one_field_past_both_edges_is_one_finding_naming_both() -> None:
    element = {"type": "box", "x": 60, "y": 50, "width": 30, "height": 10, "thickness": 0.5}
    (finding,) = _edge(_label(element))

    assert "width" in finding.message
    assert "height" in finding.message
