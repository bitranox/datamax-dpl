"""What happens on a model that lacks a feature: fallback under degrade, refusal under strict."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import datamax_dpl
from datamax_dpl.application.use_cases import render_batch
from datamax_dpl.domain.enums import FallbackPolicy, FindingCode, ImageModule, Severity, SpeedCode
from datamax_dpl.domain.errors import LabelRejectedError
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.profiles import PROFILES, Firmware, get_profile
from datamax_dpl.domain.render import build_job, render
from datamax_dpl.domain.render_context import RenderOptions

STX = b"\x02"


def label(**fields: object) -> Label:
    document: dict[str, object] = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "X"}]}
    document.update(fields)
    return Label.model_validate(document)


def codes(result_label: Label, model: str, *, firmware: Firmware | None = None) -> set[FindingCode]:
    result = build_job(result_label, RenderOptions(model=model, firmware=firmware))
    return {f.code for f in result.findings}


# The slanted line starts on the label's bottom edge, where a model's measured along offset (the
# Allegro 2's -0.25 mm) refuses it; the kitchen-sink tests are about fallbacks, so they render
# with offset_along=0.0.
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


def kitchen_sink_options(model: str, *, policy: FallbackPolicy = FallbackPolicy.DEGRADE) -> RenderOptions:
    """Options that print the kitchen sink on ``model``: a cutter, no offset, and a memory module.

    The circle and polygon are downloaded on a model without them, and a model with no built-in
    memory module refuses any download (``image_module_unknown``) until one is named: module A
    stands for a plug-in module fitted there.
    """
    module = ImageModule.A if PROFILES[model].image_module is None else None
    return RenderOptions(model=model, cutter_installed=True, offset_along=0.0, policy=policy, image_module=module)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", sorted(PROFILES))
def test_every_model_renders_the_kitchen_sink_under_degrade_without_errors(model: str) -> None:
    result = build_job(KITCHEN_SINK, kitchen_sink_options(model))
    assert [f.message for f in result.errors] == []
    assert result.data.count(STX + b"L\r") == 1


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", sorted(PROFILES))
def test_under_strict_every_applied_fallback_becomes_an_error(model: str) -> None:
    degraded = build_job(KITCHEN_SINK, kitchen_sink_options(model))
    strict = build_job(KITCHEN_SINK, kitchen_sink_options(model, policy=FallbackPolicy.STRICT))
    fallbacks = {f.code for f in degraded.findings if f.fallback is not None}
    assert {f.code for f in strict.errors} == fallbacks


#: What the kitchen sink above cannot show: each needs a label of its own, on a model lacking the feature.
_NEEDS_ITS_OWN_LABEL: dict[FindingCode, dict[str, object]] = {
    FindingCode.ATTRIBUTE_UNSUPPORTED: {"attribute": 3},
    FindingCode.DATAMATRIX_OPTION_UNCONFIRMED: {
        "elements": [{"type": "barcode", "x": 5, "y": 5, "symbology": "datamatrix", "data": "DM"}]
    },
    FindingCode.SYMBOL_SET_UNSUPPORTED: {"symbol_set": "PC"},
}
#: Each reached by its own tests, in the module named: they need a setting or a printer state, not a label.
_REACHED_ELSEWHERE = {
    FindingCode.ELEMENT_PAST_LABEL_EDGE: "test_label_edge",
    FindingCode.IMAGE_HALVED: "test_upload_check",
    FindingCode.MAX_LABEL_LENGTH_TOO_SHORT: "test_job_preamble",
    FindingCode.MODEL_OFFSET_CLAMPED: "test_render",
    FindingCode.STOP_POSITION_UNSUPPORTED: "test_render",
    FindingCode.TRANSPARENCY_EMULATED: "test_overlap",
    FindingCode.TRANSPARENCY_NOT_EMULATED: "test_overlap",
}
#: The rasterized codes share one call through a table, and transparency passes findings it was given.
_DEVIATE_THROUGH_A_VARIABLE = 2
_KITCHEN_SINK_CODES = {
    FindingCode.BOX_RASTERIZED,
    FindingCode.CHARACTERS_UNMAPPABLE,
    FindingCode.CIRCLE_RASTERIZED,
    FindingCode.CUT_BY_UNSUPPORTED,
    FindingCode.CUT_NOW_UNSUPPORTED,
    FindingCode.DEFAULT_BAR_WIDTHS_UNSUPPORTED,
    FindingCode.FONT_SIZE_SUBSTITUTED,
    FindingCode.HEAT_OUT_OF_RANGE,
    FindingCode.LINE_RASTERIZED,
    FindingCode.PAUSE_EACH_LABEL_UNSUPPORTED,
    FindingCode.POLYGON_RASTERIZED,
    FindingCode.PRESENT_DISTANCE_UNSUPPORTED,
    FindingCode.SCALABLE_FONT_UNSUPPORTED,
    FindingCode.SENSOR_SELECT_UNSUPPORTED,
    FindingCode.SPEED_OUT_OF_RANGE,
    FindingCode.SPEED_UNSUPPORTED,
}


def _deviate_calls() -> tuple[set[str], int]:
    """Read every ``.deviate(...)`` in the package: the codes it names, and the calls naming none."""
    named: set[str] = set()
    through_a_variable = 0
    for path in Path(datamax_dpl.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "deviate"
            ):
                continue
            first = node.args[0]
            if isinstance(first, ast.Attribute) and ast.unparse(first.value) == "FindingCode":
                named.add(first.attr)
            else:
                through_a_variable += 1
    return named, through_a_variable


@pytest.mark.os_agnostic
def test_every_deviate_code_is_on_a_list_that_has_it_proven_under_strict() -> None:
    """A new fallback fails here until it is listed, so none stays a warning under strict unseen."""
    named, through_a_variable = _deviate_calls()
    assert named, "the walk found no deviate call: it read the wrong directory"
    assert through_a_variable == _DEVIATE_THROUGH_A_VARIABLE
    listed = (
        _KITCHEN_SINK_CODES | set(_NEEDS_ITS_OWN_LABEL) | set(_REACHED_ELSEWHERE) | {FindingCode.BATCH_CUT_UNSUPPORTED}
    )
    rasterized_and_passed = {
        FindingCode.BOX_RASTERIZED,
        FindingCode.CIRCLE_RASTERIZED,
        FindingCode.LINE_RASTERIZED,
        FindingCode.POLYGON_RASTERIZED,
        FindingCode.TRANSPARENCY_EMULATED,
        FindingCode.TRANSPARENCY_NOT_EMULATED,
    }
    assert {FindingCode[name] for name in named} | rasterized_and_passed == listed


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("code", "module"), _REACHED_ELSEWHERE.items(), ids=str)
def test_the_module_a_code_is_left_to_does_test_it(code: FindingCode, module: str) -> None:
    source = Path(__file__).with_name(f"{module}.py").read_text(encoding="utf-8")
    assert code.value in source or code.name in source


@pytest.mark.os_agnostic
def test_the_kitchen_sink_reaches_every_code_it_is_listed_for() -> None:
    reached: set[FindingCode] = set()
    for model in PROFILES:
        degraded = build_job(KITCHEN_SINK, kitchen_sink_options(model))
        reached |= {finding.code for finding in degraded.findings if finding.fallback is not None}
    assert reached == _KITCHEN_SINK_CODES


@pytest.mark.os_agnostic
@pytest.mark.parametrize("code", list(_NEEDS_ITS_OWN_LABEL), ids=lambda code: code.value)
def test_a_fallback_the_kitchen_sink_cannot_show_is_an_error_under_strict(code: FindingCode) -> None:
    document = _NEEDS_ITS_OWN_LABEL[code]
    assert code in codes(label(**document), "allegro-2")
    strict = build_job(label(**document), RenderOptions(model="allegro-2", policy=FallbackPolicy.STRICT))
    assert code in {finding.code for finding in strict.errors}


@pytest.mark.os_agnostic
def test_a_batch_cut_the_model_cannot_send_is_an_error_under_strict() -> None:
    cut_label = label(cut={"every": 2})
    records: list[dict[str, str]] = [{}, {}]
    degraded = render_batch(cut_label, records, RenderOptions(model="prodigy"))
    assert degraded.ok
    assert [finding.severity for finding in degraded.batch_findings] == [Severity.WARNING]
    strict = render_batch(cut_label, records, RenderOptions(model="prodigy", policy=FallbackPolicy.STRICT))
    assert [(finding.code, finding.severity) for finding in strict.batch_findings] == [
        (FindingCode.BATCH_CUT_UNSUPPORTED, Severity.ERROR)
    ]


@pytest.mark.os_agnostic
def test_circle_is_rasterized_and_downloaded_on_a_model_without_circles() -> None:
    result = build_job(
        label(elements=[{"type": "circle", "x": 20, "y": 20, "radius": 5}]), RenderOptions(model="allegro")
    )
    assert FindingCode.CIRCLE_RASTERIZED in {f.code for f in result.warnings}
    [name] = result.downloads
    assert re.fullmatch(r"D[0-9A-Za-z]{7}", name)
    assert STX + b"IAAF" + name.encode("ascii") + b"\r" in result.data
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
    assert FindingCode.CIRCLE_RASTERIZED not in codes(circle, "dmx-400", firmware=Firmware(major=2, minor=1))


@pytest.mark.os_agnostic
def test_strict_policy_refuses_a_label_that_needs_a_fallback() -> None:
    circle = label(elements=[{"type": "circle", "x": 20, "y": 20, "radius": 5}])
    with pytest.raises(LabelRejectedError, match="circle cannot be drawn natively"):
        render(circle, RenderOptions(model="allegro", policy=FallbackPolicy.STRICT))


@pytest.mark.os_agnostic
def test_mirror_has_no_fallback_and_is_refused_even_under_degrade() -> None:
    with pytest.raises(LabelRejectedError, match="mirror: Allegro does not have it"):
        render(label(mirror=True), RenderOptions(model="allegro"))


@pytest.mark.os_agnostic
def test_speed_outside_the_model_range_is_clamped() -> None:
    result = build_job(label(print_speed=SpeedCode.IPS_10_0), RenderOptions(model="dmx-400"))
    assert b"\rPG\r" in result.data
    assert FindingCode.SPEED_OUT_OF_RANGE in {f.code for f in result.warnings}


@pytest.mark.os_agnostic
def test_each_speed_goes_out_under_its_own_command_letter() -> None:
    speeds = label(print_speed=SpeedCode.IPS_2_0, slew_speed=SpeedCode.IPS_3_0, backup_speed=SpeedCode.IPS_2_5)
    data = build_job(speeds, RenderOptions(model="dmx-600")).data
    assert (b"\rPC\r" in data, b"\rSE\r" in data, b"\rpD\r" in data) == (True, True, True)


@pytest.mark.os_agnostic
def test_a_speed_the_model_has_no_setting_for_is_named_by_its_command_letter() -> None:
    result = build_job(label(backup_speed=SpeedCode.IPS_2_0), RenderOptions(model="allegro"))
    unsupported = [f.message for f in result.findings if f.code is FindingCode.SPEED_UNSUPPORTED]
    assert unsupported == ["Allegro has no setting for 'p'"]


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


@pytest.mark.os_agnostic
def test_encoding_stops_once_a_label_has_more_fields_than_the_model_takes() -> None:
    """Refused either way, so the elements past the limit cost no time: each would warn here."""
    many = label(elements=[{"type": "text", "x": 1, "y": 1, "text": chr(0x20AC)} for _ in range(1000)])
    result = build_job(many, RenderOptions(model="prodigy"))
    assert [f.message for f in result.errors] == ["at least 101 fields; Prodigy takes at most 100"]
    assert sum(f.code is FindingCode.CHARACTERS_UNMAPPABLE for f in result.findings) == 101


@pytest.mark.os_agnostic
def test_a_label_exactly_at_the_field_limit_passes() -> None:
    limit = label(elements=[{"type": "text", "x": 1, "y": 1, "text": str(i)} for i in range(100)])
    assert build_job(limit, RenderOptions(model="prodigy")).ok


@pytest.mark.os_agnostic
def test_merged_fields_count_as_the_one_record_they_send() -> None:
    """Why the limit reads records, not elements: more elements than fields can still fit."""
    limit = get_profile("allegro-2").max_fields
    lines = [
        {"type": "line", "x": 1 + i % 50, "y": 1, "x2": 1 + i % 50, "y2": 50, "thickness": 2} for i in range(limit + 1)
    ]
    merged = label(attribute=2, elements=lines)
    codes = {f.code for f in build_job(merged, RenderOptions(model="allegro-2")).findings}
    assert FindingCode.TRANSPARENCY_EMULATED in codes
    assert FindingCode.TOO_MANY_FIELDS not in codes
