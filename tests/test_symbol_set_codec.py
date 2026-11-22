"""Scalable text in a symbol set with no known codec is refused as a finding, not raised."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.enums import FallbackPolicy, FindingCode, Severity
from datamax_dpl.domain.label import Label, ScalableText, Text
from datamax_dpl.domain.profiles import MODEL_PROFILES, Feature, Firmware
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner

#: Every model with scalable fonts, with the oldest firmware that has them.
_SCALABLE = sorted(
    (model, support.min_firmware)
    for model, profile in MODEL_PROFILES.items()
    if (support := profile.support(Feature.SCALABLE_FONTS)).available
)
_WITHOUT_SCALABLE = sorted(
    model for model, profile in MODEL_PROFILES.items() if not profile.support(Feature.SCALABLE_FONTS).available
)


def _label(symbol_set: str) -> Label:
    return Label(
        symbol_set=symbol_set,
        elements=[Text(x=1, y=1, text="a"), ScalableText(x=1, y=5, text="Hi", height_points=10)],
    )


@pytest.mark.os_agnostic
def test_the_scalable_models_are_the_ones_the_fix_was_written_for() -> None:
    assert [model for model, _ in _SCALABLE] == [
        "dmx-600",
        "dmx-800",
        "pe42",
        "pe43",
        "prodigy-max-203",
        "prodigy-max-300",
        "titan-6200",
    ]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("policy", list(FallbackPolicy))
@pytest.mark.parametrize(("model", "firmware"), _SCALABLE)
def test_scalable_text_in_a_set_without_a_codec_is_refused_naming_the_field(
    model: str, firmware: Firmware | None, policy: FallbackPolicy
) -> None:
    result = build_job(_label("XX"), RenderOptions(model=model, firmware=firmware, policy=policy))
    assert result.ok is False
    (refusal,) = [f for f in result.findings if f.code is FindingCode.SYMBOL_SET_NO_CODEC]
    assert refusal.severity is Severity.ERROR
    assert refusal.element == 1
    assert "'XX'" in refusal.message


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", _WITHOUT_SCALABLE)
def test_a_model_without_scalable_fonts_prints_the_field_in_font_9_and_does_not_refuse(model: str) -> None:
    result = build_job(_label("XX"), RenderOptions(model=model))
    assert result.ok is True
    assert FindingCode.SYMBOL_SET_NO_CODEC not in {f.code for f in result.findings}


@pytest.mark.os_agnostic
def test_a_set_with_a_codec_is_not_refused() -> None:
    result = build_job(_label("W1"), RenderOptions(model="dmx-800"))
    assert result.ok is True
    assert FindingCode.SYMBOL_SET_NO_CODEC not in {f.code for f in result.findings}


@pytest.mark.os_agnostic
def test_a_set_without_a_codec_and_no_scalable_text_is_not_refused() -> None:
    label = Label(symbol_set="XX", elements=[Text(x=1, y=1, text="a")])
    result = build_job(label, RenderOptions(model="dmx-800"))
    assert FindingCode.SYMBOL_SET_NO_CODEC not in {f.code for f in result.findings}


@pytest.mark.os_agnostic
def test_render_refuses_it_with_exit_1_and_the_finding_in_the_envelope(cli_runner: CliRunner, tmp_path: Path) -> None:
    path = tmp_path / "label.json"
    path.write_text(_label("XX").model_dump_json(exclude_none=True), encoding="utf-8")
    args = ["--json", "render", str(path), "--model", "dmx-800"]
    result = cli_runner.invoke(cli_mod.cli, args, obj=build_testing)
    assert result.exit_code == 1, result.output
    document = json.loads(result.stdout)
    assert document["ok"] is False
    assert FindingCode.SYMBOL_SET_NO_CODEC.value in result.stdout
