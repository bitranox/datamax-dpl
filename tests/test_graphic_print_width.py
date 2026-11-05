"""A box, line or circle the printer draws itself that ends past the print width is reported.

The user's decision of 2026-11-01 ([210]): such a graphic gets ``graphic_past_print_width``, naming the
element and by how many mm it passes the print width: a warning, an error under strict. Only its start
column was checked before (``outside_print_width``), so a box starting inside the head and running off it
built with no finding. The labels here have no size, so the label-edge check stays out of the way.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from datamax_dpl.domain.enums import FallbackPolicy, FindingCode, Severity
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions

pytestmark = pytest.mark.os_agnostic

#: allegro-2 prints 4.1 in, 832 dots, about 104.1 mm across; its firmware has no circle record, so
#: a native circle is checked on dmx-600 (6.4 in, about 162.6 mm).
_BOX = {"type": "box", "x": 90, "y": 5, "width": 30, "height": 10, "thickness": 0.5}
_LINE = {"type": "line", "x": 90, "y": 5, "x2": 120, "y2": 5, "thickness": 0.5}
#: Starts inside the last column; its 4 mm thickness carries it past the head's last dot.
_VERTICAL = {"type": "line", "x": 101, "y": 5, "x2": 101, "y2": 30, "thickness": 4}
_CIRCLE = {"type": "circle", "x": 155, "y": 20, "radius": 10}
_CASES = [
    (_BOX, "box", "allegro-2"),
    (_LINE, "line", "allegro-2"),
    (_VERTICAL, "line", "allegro-2"),
    (_CIRCLE, "circle", "dmx-600"),
]


def _label(*elements: dict[str, Any]) -> Label:
    return Label.model_validate({"unit": "mm", "elements": list(elements)})


def _build(label: Label, policy: FallbackPolicy = FallbackPolicy.DEGRADE, model: str = "allegro-2") -> list[Any]:
    return list(build_job(label, RenderOptions(model=model, policy=policy)).findings)


def _past(label: Label, policy: FallbackPolicy = FallbackPolicy.DEGRADE, model: str = "allegro-2") -> list[Any]:
    return [f for f in _build(label, policy, model) if f.code is FindingCode.GRAPHIC_PAST_PRINT_WIDTH]


@pytest.mark.parametrize(("element", "what", "model"), _CASES)
def test_a_graphic_past_the_print_width_is_a_warning_naming_it_and_the_mm(
    element: dict[str, Any], what: str, model: str
) -> None:
    found = _past(_label(element), model=model)

    assert len(found) == 1
    assert found[0].severity is Severity.WARNING
    assert found[0].element == 0
    assert what in found[0].message
    assert "print width" in found[0].message
    assert re.search(r"by about \d+\.\d mm", found[0].message)


@pytest.mark.parametrize(("element", "what", "model"), _CASES)
def test_strict_refuses_a_graphic_past_the_print_width(element: dict[str, Any], what: str, model: str) -> None:
    found = _past(_label(element), FallbackPolicy.STRICT, model)

    assert [(f.severity, what in f.message) for f in found] == [(Severity.ERROR, True)]


def test_the_box_passes_the_print_width_by_its_overhang() -> None:
    """x 90 + 30 mm on a 104.1 mm head: about 16 mm past it."""
    (finding,) = _past(_label(_BOX))

    match = re.search(r"by about (\d+\.\d) mm", finding.message)
    assert match is not None
    assert 15.5 <= float(match.group(1)) <= 16.3
    assert "104.1 mm" in finding.message


@pytest.mark.parametrize(
    "element",
    [
        {"type": "box", "x": 0, "y": 5, "width": 104.1, "height": 10, "thickness": 0.5},
        {"type": "line", "x": 0, "y": 5, "x2": 104.1, "y2": 5, "thickness": 0.5},
        {"type": "box", "x": 10, "y": 5, "width": 30, "height": 10, "thickness": 0.5},
        # A vertical line reaches across by its thickness, never by its length.
        {"type": "line", "x": 100, "y": 5, "x2": 100, "y2": 60, "thickness": 0.5},
    ],
)
def test_a_graphic_inside_the_print_width_has_no_finding(element: dict[str, Any]) -> None:
    """A box or line drawn exactly across the whole head still fits it."""
    assert _past(_label(element)) == []


def test_a_native_circle_inside_the_print_width_has_no_finding() -> None:
    assert _past(_label({"type": "circle", "x": 140, "y": 20, "radius": 10}), model="dmx-600") == []


def test_a_circle_drawn_as_an_image_keeps_the_image_refusal() -> None:
    """allegro-2 draws a circle into an image; that image is refused past the head, not warned about."""
    codes = [f.code for f in _build(_label({"type": "circle", "x": 100, "y": 20, "radius": 10}))]

    assert FindingCode.IMAGE_PAST_PRINT_WIDTH in codes
    assert FindingCode.GRAPHIC_PAST_PRINT_WIDTH not in codes


def test_a_graphic_starting_past_the_last_column_gets_only_that_refusal() -> None:
    """Its start is already refused (``outside_print_width``); its right edge gets no second finding."""
    findings = _build(_label({"type": "box", "x": 106, "y": 5, "width": 10, "height": 10, "thickness": 0.5}))
    codes = [f.code for f in findings]

    assert FindingCode.OUTSIDE_PRINT_WIDTH in codes
    assert FindingCode.GRAPHIC_PAST_PRINT_WIDTH not in codes


def test_each_graphic_past_the_print_width_is_reported_on_its_own_element() -> None:
    found = _past(_label({"type": "text", "x": 5, "y": 5, "text": "HI", "font": 2}, _BOX, _LINE))

    assert [f.element for f in found] == [1, 2]


def test_a_wider_head_takes_the_same_graphic() -> None:
    """The limit is the model's own print width: dmx-600 prints 6.4 in."""
    assert _past(_label(_BOX), model="dmx-600") == []
