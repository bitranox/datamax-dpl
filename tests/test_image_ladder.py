"""scripts/image_ladder.py writes labels that straddle the image memory budget."""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.domain.label import Label
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from collections.abc import Callable

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "image_ladder.py"


def _main() -> Callable[[list[str]], int]:
    """The script's ``main``, loaded from its file the way a maintainer runs it."""
    namespace = runpy.run_path(str(_SCRIPT), run_name="image_ladder")
    main: Callable[[list[str]], int] = namespace["main"]
    return main


@pytest.mark.os_agnostic
def test_the_ladder_writes_valid_labels_that_straddle_the_allegro_2_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main()(["--out", str(tmp_path)]) == 0

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["outcome"] for line in lines] == ["within", "within", "halved", "halved", "refused"]
    for line in lines:
        label = Label.model_validate_json(Path(line["file"]).read_text(encoding="utf-8"))
        result = build_job(label, RenderOptions(model="allegro-2"))
        assert result.image_bytes == line["image_bytes"]


@pytest.mark.os_agnostic
def test_a_model_without_a_budget_needs_one_given(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _main()(["--out", str(tmp_path), "--model", "dmx-600"]) == 2
    assert "has no image memory budget" in capsys.readouterr().err
