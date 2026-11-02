"""What a label with very many elements costs: transparency emulation stays linear in memory and is refused fast."""

from __future__ import annotations

import json
import tracemalloc
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.enums import Attribute, FindingCode
from datamax_dpl.domain.label import MAX_ELEMENTS, Box, Element, Label, Text
from datamax_dpl.domain.profiles import MODEL_PROFILES
from datamax_dpl.domain.render import RenderResult, build_job
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner

#: Under what holding every pair of these fields takes (54 MB and 153 MB measured), and well over what
#: the fields themselves take (under 3 MB), so a new field type does not move it.
_PEAK_BYTES = 30_000_000


def _peak_of_build(label: Label) -> tuple[RenderResult, int]:
    tracemalloc.start()
    try:
        result = build_job(label, RenderOptions(model="allegro-2"))
        return result, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


@pytest.mark.os_agnostic
def test_a_label_takes_at_most_the_element_limit() -> None:
    """One element past the limit is refused when the label is read; the limit itself is a label."""
    assert len(Label(elements=[Text(x=1, y=1, text="A")] * MAX_ELEMENTS).elements) == MAX_ELEMENTS
    with pytest.raises(ValidationError, match="elements"):
        Label(elements=[Text(x=1, y=1, text="A")] * (MAX_ELEMENTS + 1))


@pytest.mark.os_agnostic
def test_every_model_s_field_limit_stays_under_the_element_limit() -> None:
    """The limit refuses nothing a real model could print: its largest format holds far fewer records."""
    real = [profile.max_fields for profile in MODEL_PROFILES.values() if not profile.synthetic]
    assert real
    assert max(real) < MAX_ELEMENTS


@pytest.mark.os_agnostic
def test_text_fields_past_the_field_limit_skip_the_overlap_sweep() -> None:
    """Printer-drawn fields each stay one record, so past the limit the job is refused without pairing them."""
    elements: list[Element] = [Text(x=5, y=5, text="AB") for _ in range(MAX_ELEMENTS)]
    result, peak = _peak_of_build(Label(attribute=Attribute.TRANSPARENT, elements=elements))
    codes = {finding.code for finding in result.findings}
    assert FindingCode.TOO_MANY_FIELDS in codes
    assert FindingCode.TRANSPARENCY_NOT_EMULATED not in codes
    assert FindingCode.TRANSPARENCY_NOT_NEEDED not in codes
    assert not result.ok
    assert peak < _PEAK_BYTES


@pytest.mark.os_agnostic
def test_overlapping_exact_fields_are_paired_without_holding_every_pair() -> None:
    """Fields that can merge are still swept, one pair at a time: they all land in one image."""
    elements: list[Element] = [Box(x=5 + i * 0.001, y=5, width=2, height=2, thickness=1) for i in range(1200)]
    result, peak = _peak_of_build(Label(attribute=Attribute.TRANSPARENT, width=50, height=50, elements=elements))
    assert FindingCode.TRANSPARENCY_EMULATED in {finding.code for finding in result.findings}
    assert peak < _PEAK_BYTES


@pytest.mark.os_agnostic
def test_a_label_with_far_too_many_elements_gets_an_envelope_exit_2(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The input that took 49 s and 1.5 GB is refused at once, with the usual error envelope."""
    elements: list[dict[str, Any]] = [{"type": "text", "x": 5, "y": 5, "text": "AB"}] * 25600
    path = tmp_path / "many.json"
    path.write_text(json.dumps({"attribute": 2, "elements": elements}), encoding="utf-8")
    result = cli_runner.invoke(
        cli_mod.cli, ["--json-bare", "validate", "--model", "allegro-2", str(path)], obj=build_testing
    )
    assert result.exit_code == 2, result.output
    document = json.loads(result.stdout)
    assert document["ok"] is False
    assert document["error"] == "LabelInputError"
    assert str(MAX_ELEMENTS) in document["message"]
    assert len(document["message"]) < 2000
