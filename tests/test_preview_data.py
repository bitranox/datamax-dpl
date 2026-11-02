"""``preview --data``: the first record fills the placeholders of the picture."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.cli.main import main
from datamax_dpl.composition import build_testing

if TYPE_CHECKING:
    from pathlib import Path


def _picture(tmp_path: Path, *names: str) -> bytes:
    """Draw a label whose only text is the record's ``name``, and return the PNG."""
    label = tmp_path / "label.json"
    label.write_text(json.dumps({"elements": [{"type": "text", "x": 5, "y": 5, "text": "{name}"}]}), encoding="utf-8")
    records = tmp_path / "records.json"
    records.write_text(json.dumps([{"name": name} for name in names]), encoding="utf-8")
    output = tmp_path / "out.png"
    args = ["preview", str(label), "-o", str(output), "--data", str(records), "--model", "dmx-800"]
    assert main(args, services_factory=build_testing) == 0
    return output.read_bytes()


@pytest.mark.os_agnostic
def test_the_filled_text_changes_the_picture(tmp_path: Path) -> None:
    assert _picture(tmp_path, "I") != _picture(tmp_path, "WWWWWWWWWW")


@pytest.mark.os_agnostic
def test_only_the_first_record_is_drawn(tmp_path: Path) -> None:
    assert _picture(tmp_path, "I", "WWWWWWWWWW") == _picture(tmp_path, "I")
