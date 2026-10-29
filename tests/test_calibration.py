"""The calibration labels: what a user measures, and the rule that no two shapes share a dot."""

from __future__ import annotations

import pytest

from datamax_dpl.domain.calibration import (
    ACROSS_INSET_MM,
    ALONG_LENGTH_MM,
    across_frame_width,
    across_label,
    along_label,
    offset_from_borders,
    scale_from_measurement,
)
from datamax_dpl.domain.decoder import explain
from datamax_dpl.domain.label import Box, Element, Label, Line, Text
from datamax_dpl.domain.preview import Canvas, canvas_for, text_box, widest_cell_mm
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions

Rect = tuple[int, int, int, int]
"""A half-open rectangle (left, bottom, right, top) in DPL record units."""


def _units(value: float) -> int:
    return round(value * 100)


def _rects(element: Element, canvas: Canvas) -> list[Rect]:
    """The area a decoded record covers, as the printer draws it: from its reference corner up and right."""
    if isinstance(element, Line):
        left, right = sorted((_units(element.x), _units(element.x2)))
        bottom, top = sorted((_units(element.y), _units(element.y2)))
        thickness = _units(element.thickness)
        horizontal = element.y == element.y2
        return [(left, bottom, right, bottom + thickness) if horizontal else (left, bottom, left + thickness, top)]
    if isinstance(element, Box):
        x, y, w, h = (_units(v) for v in (element.x, element.y, element.width, element.height))
        edge, side = _units(element.thickness), _units(element.side_thickness or element.thickness)
        return [
            (x, y, x + w, y + edge),
            (x, y + h - edge, x + w, y + h),
            (x, y, x + side, y + h),
            (x + w - side, y, x + w, y + h),
        ]
    assert isinstance(element, Text)
    # Text is taken as its whole box: the printer's glyphs differ from the preview's.
    _, _, width_px, height_px = text_box(element, canvas)
    x, y = _units(element.x), _units(element.y)
    return [(x, y, x + round(width_px * 100 / canvas.dpi), y + round(height_px * 100 / canvas.dpi))]


def _touching_pairs(label: Label, model: str = "allegro-2") -> list[tuple[int, int]]:
    """Pairs of rendered fields that share a dot: the printer XORs such dots away."""
    decoded = explain(build_job(label, RenderOptions(model=model)).data).label
    assert decoded is not None
    canvas = canvas_for(decoded, get_profile(model))
    areas = [_rects(element, canvas) for element in decoded.elements]
    return [
        (i, j)
        for i, first in enumerate(areas)
        for j in range(i + 1, len(areas))
        if any(_intersect(a, b) for a in first for b in areas[j])
    ]


def _intersect(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", ["allegro-2", "dmx-600"])
@pytest.mark.parametrize(("width", "height"), [(100, 55), (24, 30), (57.3, 31.7)])
def test_no_two_shapes_of_the_across_label_share_a_dot(model: str, width: float, height: float) -> None:
    assert _touching_pairs(across_label(width=width, height=height, title="LAGER 0.9770"), model) == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", ["allegro-2", "dmx-600"])
@pytest.mark.parametrize("width", [100, 20])
def test_no_two_shapes_of_the_along_label_share_a_dot(model: str, width: float) -> None:
    assert _touching_pairs(along_label(width=width, length=60, title="LAGER 0.9770"), model) == []


@pytest.mark.os_agnostic
def test_the_across_frame_stands_two_millimetres_in_from_every_edge_and_the_bar_fills_it() -> None:
    label = across_label(width=100, height=55, title="LAGER")
    (frame,) = [e for e in label.elements if isinstance(e, Box)]
    bar = max((e for e in label.elements if isinstance(e, Line)), key=lambda e: abs(e.x2 - e.x))
    assert (frame.x, frame.y, frame.width, frame.height) == (2, 2, 96, 51)
    assert (bar.x, bar.x2) == (2 + frame.thickness, 98 - frame.thickness)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("width", "frame"), [(100, 96), (57.3, 53.3), (24, 20)])
def test_the_frame_width_to_measure_follows_the_label_width(width: float, frame: float) -> None:
    assert across_frame_width(width) == pytest.approx(frame)
    assert ACROSS_INSET_MM == 2


@pytest.mark.os_agnostic
def test_the_across_ticks_stand_every_ten_millimetres() -> None:
    label = across_label(width=100, height=55, title="LAGER")
    ticks = sorted(e.x for e in label.elements if isinstance(e, Line) and e.x == e.x2)
    # counted from the frame's outer left edge at 2 mm; the frame's right side is at 98
    assert ticks == [12, 22, 32, 42, 52, 62, 72, 82, 92]


@pytest.mark.os_agnostic
def test_the_along_bar_has_the_asked_length_and_ticks_every_ten_millimetres() -> None:
    label = along_label(width=100, length=ALONG_LENGTH_MM, title="LAGER")
    bar = max((e for e in label.elements if isinstance(e, Line)), key=lambda e: abs(e.y2 - e.y))
    ticks = [e for e in label.elements if isinstance(e, Line) and e.y == e.y2]
    assert abs(bar.y2 - bar.y) == ALONG_LENGTH_MM
    assert len(ticks) == 21
    assert label.height is not None
    assert label.height >= bar.y2


@pytest.mark.os_agnostic
def test_the_labels_say_what_to_measure() -> None:
    across = across_label(width=100, height=55, title="LAGER 0.977")
    along = along_label(width=100, length=150, title="LAGER 0.977")
    across_text = " ".join(getattr(e, "text", "") for e in across.elements)
    along_text = " ".join(getattr(e, "text", "") for e in along.elements)
    assert "LAGER 0.977" in across_text and "96 MM" in across_text
    assert "LAGER 0.977" in along_text and "150 MM" in along_text


def _fonts(label: Label) -> list[int]:
    return [e.font for e in label.elements if isinstance(e, Text)]


@pytest.mark.os_agnostic
def test_a_label_with_room_prints_every_text_line_in_the_large_font_three() -> None:
    across = across_label(width=100, height=55, title="LAGER SCALE 1.0000")
    along = along_label(width=100, title="LAGER SCALE 0.9760")
    assert set(_fonts(across)) == {3}
    assert set(_fonts(along)) == {3}


@pytest.mark.os_agnostic
def test_a_block_too_big_for_a_larger_font_falls_back_to_font_one() -> None:
    label = across_label(width=24, height=30, title="LAGER")
    assert _fonts(label) == [3, 1, 1, 1, 1]


@pytest.mark.os_agnostic
def test_a_lower_case_title_keeps_its_case_in_font_two_instead_of_capitals_only_font_three() -> None:
    (title, *_) = [e for e in across_label(width=100, height=55, title="Lager").elements if isinstance(e, Text)]
    assert (title.text, title.font) == ("Lager", 2)


@pytest.mark.os_agnostic
def test_the_widest_cell_of_a_font_is_its_largest_at_any_resolution() -> None:
    advance, height = widest_cell_mm(3)
    # font 3: the 289.56 dpi cell (39 dots) is the tallest, the 300 dpi one (21 + 3 dots) the widest
    assert height == pytest.approx(39 / 289.56 * 25.4)
    assert advance == pytest.approx(24 / 300 * 25.4)


@pytest.mark.os_agnostic
def test_the_across_frame_renders_ninety_six_by_fifty_one_millimetres_on_the_allegro_2() -> None:
    nominal = RenderOptions(model="allegro-2", scale_along=1.0, offset_along=0.0)
    data = build_job(across_label(width=100, height=55, title="X"), nominal).data
    assert b"B384204001001" in data


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("width", "height", "minimum"), [(23.9, 55, "24 mm"), (100, 29.9, "30 mm")])
def test_an_across_label_narrower_than_its_frame_and_text_need_is_refused(
    width: float, height: float, minimum: str
) -> None:
    with pytest.raises(ValueError, match=minimum):
        across_label(width=width, height=height, title="X")


@pytest.mark.os_agnostic
def test_an_along_length_over_two_hundred_millimetres_is_refused() -> None:
    with pytest.raises(ValueError, match="200 mm"):
        along_label(width=100, length=201, title="X")


@pytest.mark.os_agnostic
def test_a_title_that_does_not_fit_is_refused() -> None:
    with pytest.raises(ValueError, match="too small for its text"):
        across_label(width=24, height=30, title="A VERY LONG PRINTER NAME THAT TAKES MANY LINES TO WRAP")


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("current", "measured", "nominal", "expected"),
    [
        (0.977, 199, 200, 0.9721),
        (1.0, 99.2, 100, 0.992),
        (1.0, 100, 100, 1.0),
        (0.5, 100, 100, 0.5),
        (1.5, 100, 100, 1.5),
    ],
)
def test_the_new_scale_is_the_old_one_times_measured_over_nominal(
    current: float, measured: float, nominal: float, expected: float
) -> None:
    assert scale_from_measurement(current, measured=measured, nominal=nominal) == expected


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("measured", "nominal"), [(49.9, 100), (150.1, 100), (0, 100), (100, 0), (-5, 100)])
def test_a_scale_outside_the_range_or_a_length_not_over_zero_is_refused(measured: float, nominal: float) -> None:
    with pytest.raises(ValueError, match=f"{measured:g} mm"):
        scale_from_measurement(1.0, measured=measured, nominal=nominal)


@pytest.mark.os_agnostic
def test_the_across_label_states_the_border_to_measure_on_every_side() -> None:
    label = across_label(width=100, height=55, title="LAGER")
    text = " ".join(e.text for e in label.elements if isinstance(e, Text))
    assert "BORDERS: 2 MM" in text


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("current", "low", "high", "expected"),
    [
        (0.0, 2.3, 1.8, -0.25),  # bottom border 2.3, top 1.8: the print sits high, move it down
        (0.0, 1.8, 2.2, 0.2),  # left border 1.8, right 2.2: the print sits left, move it right
        (-0.25, 2.0, 2.0, -0.25),  # even borders keep the offset in use
        (0.5, 2.25, 1.75, 0.25),
    ],
)
def test_the_new_offset_moves_the_print_half_the_border_difference(
    current: float, low: float, high: float, expected: float
) -> None:
    assert offset_from_borders(current, low=low, high=high) == pytest.approx(expected)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("current", "low", "high", "match"), [(0.0, -0.1, 2.0, "0 mm"), (9.0, 0.0, 4.0, "10")])
def test_a_negative_border_or_an_offset_past_ten_millimetres_is_refused(
    current: float, low: float, high: float, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        offset_from_borders(current, low=low, high=high)
