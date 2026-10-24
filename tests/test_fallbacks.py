"""What happens on a model that lacks a feature: fallback under degrade, refusal under strict."""

from __future__ import annotations

import pytest

from datamax_dpl.domain.enums import FallbackPolicy, FindingCode, Severity, SpeedCode
from datamax_dpl.domain.errors import LabelRejectedError
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.profiles import PROFILES
from datamax_dpl.domain.render import build_job, render
from datamax_dpl.domain.render_context import RenderOptions

STX = b"\x02"


def label(**fields: object) -> Label:
    document: dict[str, object] = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "X"}]}
    document.update(fields)
    return Label.model_validate(document)


def codes(result_label: Label, model: str, *, firmware: str = "") -> set[FindingCode]:
    result = build_job(result_label, RenderOptions(model=model, firmware=firmware))
    return {f.code for f in result.findings}


KITCHEN_SINK = Label.model_validate(
    {
        "quantity": 7,
        "cut": {"every": 3},
        "heat": 25,
        "print_speed": "S",
        "backup_speed": "C",
        "present_distance": 10,
        "media": "gap",
        "pause_each_label": True,
        "elements": [
            {"type": "text", "x": 5, "y": 5, "text": "Gr\u00fc\u00dfe \u20ac", "font": 9, "points": 11},
            {"type": "scalable_text", "x": 5, "y": 15, "text": "Big", "height_points": 30},
            {"type": "line", "x": 0, "y": 0, "x2": 40, "y2": 20, "thickness": 0.5},
            {"type": "box", "x": 50, "y": 5, "width": 20, "height": 10, "thickness": 0.5, "fill": 4},
            {"type": "circle", "x": 80, "y": 20, "radius": 8},
            {"type": "polygon", "points": [{"x": 1, "y": 1}, {"x": 9, "y": 1}, {"x": 5, "y": 8}], "fill": 1},
            {"type": "barcode", "x": 5, "y": 30, "symbology": "code128", "data": "ABC123"},
        ],
    }
)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", sorted(PROFILES))
def test_every_model_renders_the_kitchen_sink_under_degrade_without_errors(model: str) -> None:
    result = build_job(KITCHEN_SINK, RenderOptions(model=model, cutter_installed=True))
    assert [f.message for f in result.errors] == []
    assert result.data.count(STX + b"L\r") == 1


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", sorted(PROFILES))
def test_under_strict_every_applied_fallback_becomes_an_error(model: str) -> None:
    degraded = build_job(KITCHEN_SINK, RenderOptions(model=model, cutter_installed=True))
    strict = build_job(KITCHEN_SINK, RenderOptions(model=model, cutter_installed=True, policy=FallbackPolicy.STRICT))
    fallbacks = {f.code for f in degraded.findings if f.fallback is not None}
    assert {f.code for f in strict.errors} == fallbacks


@pytest.mark.os_agnostic
def test_circle_is_rasterized_and_downloaded_on_a_model_without_circles() -> None:
    result = build_job(
        label(elements=[{"type": "circle", "x": 20, "y": 20, "radius": 5}]), RenderOptions(model="allegro")
    )
    assert FindingCode.CIRCLE_RASTERIZED in {f.code for f in result.warnings}
    assert result.downloads == ("DPLR0001",)
    assert STX + b"IAAFDPLR0001\r" in result.data
    assert b"1Y11000" in result.data
    assert result.data.index(STX + b"IAAF") < result.data.index(STX + b"L\r")


@pytest.mark.os_agnostic
def test_circle_is_native_where_the_model_has_circles() -> None:
    result = build_job(
        label(elements=[{"type": "circle", "x": 20, "y": 20, "radius": 5}]), RenderOptions(model="dmx-600")
    )
    assert result.downloads == ()
    assert b"C0010001" in result.data


@pytest.mark.os_agnostic
def test_firmware_unlocks_circles_on_the_dmx_400() -> None:
    circle = label(elements=[{"type": "circle", "x": 20, "y": 20, "radius": 5}])
    assert FindingCode.CIRCLE_RASTERIZED in codes(circle, "dmx-400")
    assert FindingCode.CIRCLE_RASTERIZED not in codes(circle, "dmx-400", firmware="2.01")


@pytest.mark.os_agnostic
def test_strict_policy_refuses_a_label_that_needs_a_fallback() -> None:
    circle = label(elements=[{"type": "circle", "x": 20, "y": 20, "radius": 5}])
    with pytest.raises(LabelRejectedError, match="circle"):
        render(circle, RenderOptions(model="allegro", policy=FallbackPolicy.STRICT))


@pytest.mark.os_agnostic
def test_mirror_has_no_fallback_and_is_refused_even_under_degrade() -> None:
    with pytest.raises(LabelRejectedError, match="mirror"):
        render(label(mirror=True), RenderOptions(model="allegro"))


@pytest.mark.os_agnostic
def test_speed_outside_the_model_range_is_clamped() -> None:
    result = build_job(label(print_speed=SpeedCode.IPS_10_0), RenderOptions(model="dmx-400"))
    assert b"\rPG\r" in result.data
    assert FindingCode.SPEED_OUT_OF_RANGE in {f.code for f in result.warnings}


@pytest.mark.os_agnostic
def test_heat_above_the_model_maximum_is_clamped() -> None:
    assert b"\rH20\r" in build_job(label(heat=30), RenderOptions(model="dmx-400")).data
    assert b"\rH30\r" in build_job(label(heat=30), RenderOptions(model="pe42")).data


@pytest.mark.os_agnostic
def test_cut_by_on_the_prodigy_falls_back_to_cutting_each_label() -> None:
    result = build_job(label(quantity=10, cut={"every": 3}), RenderOptions(model="prodigy", cutter_installed=True))
    found = {f.code for f in result.findings}
    assert {FindingCode.CUT_BY_UNSUPPORTED, FindingCode.CUT_NOW_UNSUPPORTED} <= found
    assert b":0003" not in result.data
    assert not result.data.endswith(STX + b"o")


@pytest.mark.os_agnostic
def test_font9_size_the_model_lacks_is_replaced_by_the_nearest() -> None:
    text = label(elements=[{"type": "text", "x": 1, "y": 1, "text": "A", "font": 9, "points": 11}])
    result = build_job(text, RenderOptions(model="dmx-600"))
    assert b"9110A10" in result.data or b"911A10" in result.data
    assert FindingCode.FONT_SIZE_SUBSTITUTED in {f.code for f in result.warnings}


@pytest.mark.os_agnostic
def test_scalable_text_falls_back_to_font_9_on_a_model_without_scalable_fonts() -> None:
    text = label(elements=[{"type": "scalable_text", "x": 1, "y": 1, "text": "A", "height_points": 24}])
    result = build_job(text, RenderOptions(model="dmx-400"))
    assert b"1911007" in result.data
    assert FindingCode.SCALABLE_FONT_UNSUPPORTED in {f.code for f in result.warnings}


@pytest.mark.os_agnostic
def test_scalable_text_is_native_where_the_model_has_scalable_fonts() -> None:
    text = label(elements=[{"type": "scalable_text", "x": 1, "y": 1, "text": "A", "height_points": 24}])
    assert b"P024P024A" in render(text, RenderOptions(model="dmx-800")).data


@pytest.mark.os_agnostic
def test_unmappable_characters_are_replaced_and_reported() -> None:
    text = label(elements=[{"type": "text", "x": 1, "y": 1, "text": "5 \u20ac", "font": 2}])
    result = build_job(text, RenderOptions(model="dmx-600"))
    assert b"5 ?" in result.data
    assert FindingCode.CHARACTERS_UNMAPPABLE in {f.code for f in result.warnings}


@pytest.mark.os_agnostic
def test_umlauts_reach_the_printer_as_code_page_437_bytes() -> None:
    text = label(elements=[{"type": "text", "x": 1, "y": 1, "text": "\u00e4\u00f6\u00fc\u00df", "font": 2}])
    assert bytes([132, 148, 129, 225]) in render(text, RenderOptions(model="dmx-600")).data


@pytest.mark.os_agnostic
def test_a_wrong_check_digit_is_refused() -> None:
    bad = label(elements=[{"type": "barcode", "x": 1, "y": 1, "symbology": "ean13", "data": "4006381333932"}])
    result = build_job(bad, RenderOptions(model="dmx-600"))
    assert FindingCode.BARCODE_BAD_CHECK_DIGIT in {f.code for f in result.errors}


@pytest.mark.os_agnostic
def test_extended_symbologies_are_refused_where_the_model_lacks_them() -> None:
    fim = label(elements=[{"type": "barcode", "x": 1, "y": 1, "symbology": "fim", "data": "A"}])
    assert FindingCode.SYMBOLOGY_UNSUPPORTED in codes(fim, "allegro")
    assert FindingCode.SYMBOLOGY_UNSUPPORTED not in codes(fim, "dmx-600")


@pytest.mark.os_agnostic
def test_field_past_the_printable_width_is_refused() -> None:
    wide = label(elements=[{"type": "text", "x": 150, "y": 1, "text": "X"}])
    assert FindingCode.OUTSIDE_PRINT_WIDTH in {f.code for f in build_job(wide, RenderOptions(model="dmx-400")).errors}
    assert build_job(wide, RenderOptions(model="dmx-800")).ok


@pytest.mark.os_agnostic
def test_more_fields_than_the_model_takes_are_refused() -> None:
    many = label(elements=[{"type": "text", "x": 1, "y": 1, "text": str(i)} for i in range(101)])
    assert FindingCode.TOO_MANY_FIELDS in {f.code for f in build_job(many, RenderOptions(model="prodigy")).errors}
    assert build_job(many, RenderOptions(model="dmx-600")).ok


@pytest.mark.os_agnostic
def test_default_bar_widths_are_replaced_on_the_prodigy_plus() -> None:
    code = label(elements=[{"type": "barcode", "x": 1, "y": 1, "symbology": "code39", "data": "AB"}])
    result = build_job(code, RenderOptions(model="prodigy-plus"))
    assert b"1A62" in result.data
    assert FindingCode.DEFAULT_BAR_WIDTHS_UNSUPPORTED in {f.code for f in result.warnings}


@pytest.mark.os_agnostic
def test_every_error_finding_is_marked_as_error_severity() -> None:
    result = build_job(label(mirror=True), RenderOptions(model="allegro"))
    assert all(f.severity is Severity.ERROR for f in result.errors)
    assert not result.ok
