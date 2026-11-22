"""The metric max column is the inch max column times 2.54, rounded down, on every metric model."""

from __future__ import annotations

import pytest

from datamax_dpl.domain.enums import FindingCode, Unit
from datamax_dpl.domain.label import Label, Text
from datamax_dpl.domain.profiles import MODEL_PROFILES
from datamax_dpl.domain.render import build_job, job_context
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.units import DplUnit

#: The 1/10 mm limit each inch limit rounds DOWN to (the manual prints 1625 and 1680 for 640 and 661).
_EXPECTED = {398: 1010, 410: 1041, 425: 1079, 640: 1625, 661: 1678, 860: 2184}


def _options(model: str) -> RenderOptions:
    return RenderOptions(model=model, offset_across=0.0, offset_along=0.0, scale_across=1.0, scale_along=1.0)


def _label(x_mm: float) -> Label:
    return Label(unit=Unit.MM, elements=[Text(x=x_mm, y=5, text="X")])


def _metric_models() -> list[str]:
    return sorted(
        model for model in MODEL_PROFILES if job_context(_label(1), _options(model)).dpl_unit is DplUnit.MM_TENTHS
    )


_METRIC = _metric_models()


@pytest.mark.os_agnostic
def test_every_metric_model_has_an_expected_limit() -> None:
    assert _METRIC, "no metric model found: the selection is broken"
    assert {MODEL_PROFILES[model].max_column for model in _METRIC} <= set(_EXPECTED)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", _METRIC)
def test_a_field_at_the_rounded_down_limit_prints_and_one_tenth_mm_past_it_is_refused(model: str) -> None:
    limit = _EXPECTED[MODEL_PROFILES[model].max_column]
    at_limit = build_job(_label(limit / 10), _options(model))
    past = build_job(_label((limit + 1) / 10), _options(model))
    assert FindingCode.OUTSIDE_PRINT_WIDTH not in {f.code for f in at_limit.findings}
    assert f"{limit:04d}".encode() in at_limit.data
    refusals = [f.message for f in past.errors if f.code is FindingCode.OUTSIDE_PRINT_WIDTH]
    assert len(refusals) == 1, f"column {limit + 1} is not refused"
    assert f"({limit} 1/10 mm)" in refusals[0]
