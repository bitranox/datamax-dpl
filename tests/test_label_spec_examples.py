"""Every JSON example in docs/label-spec.md answers as the page says it does."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from datamax_dpl.adapters.cli.main import main
from datamax_dpl.composition import build_testing

_SPEC = Path(__file__).resolve().parents[1] / "docs" / "label-spec.md"
_BLOCK = re.compile(r"^```json\n(.*?)^```$", re.MULTILINE | re.DOTALL)
_COMMAND = ["validate", "--model", "dmx-800", "--option", "datamatrix"]
#: The page's one named exception: the model clears the image module this example's LOGO is stored in.
_STORED_IMAGE_EXAMPLE = '"name": "LOGO"}'


def _examples() -> list[str]:
    return _BLOCK.findall(_SPEC.read_text(encoding="utf-8"))


def _validate(example: str, *, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    label = tmp_path / "example.json"
    label.write_text(example, encoding="utf-8")
    code = main(["--json-bare", *_COMMAND, str(label)], services_factory=build_testing)
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.os_agnostic
def test_the_page_still_holds_its_examples() -> None:
    """A changed fence must not leave the tests below with nothing to read."""
    assert len(_examples()) == 11
    assert sum(_STORED_IMAGE_EXAMPLE in example for example in _examples()) == 1


@pytest.mark.os_agnostic
@pytest.mark.parametrize("example", [e for e in _examples() if _STORED_IMAGE_EXAMPLE not in e], ids=lambda e: e[:40])
def test_an_example_passes_the_command_the_page_names(
    example: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, document = _validate(example, tmp_path=tmp_path, capsys=capsys)
    assert code == 0, document


@pytest.mark.os_agnostic
def test_the_stored_image_example_is_refused_as_the_page_says(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    example = next(e for e in _examples() if _STORED_IMAGE_EXAMPLE in e)
    code, document = _validate(example, tmp_path=tmp_path, capsys=capsys)
    assert code == 1
    assert "stored_image_cleared" in {finding["code"] for finding in document["data"]["findings"]}
