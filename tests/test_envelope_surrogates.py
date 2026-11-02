"""A file name that is not UTF-8 reaches ``argv`` as a lone surrogate; the envelope still comes out."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from datamax_dpl.adapters.cli.main import main
from datamax_dpl.composition import build_production

_LABEL = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "HELLO"}]}

#: Only where a file name is plain bytes: APFS and NTFS refuse these names at open.
_BYTE_NAMES = pytest.mark.skipif(sys.platform != "linux", reason="only Linux file systems store a name not UTF-8")


def _label(tmp_path: Path) -> str:
    path = tmp_path / "label.json"
    path.write_text(json.dumps(_LABEL), encoding="utf-8")
    return str(path)


def _run(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = main(args, services_factory=build_production)
    return code, capsys.readouterr().out


@_BYTE_NAMES
@pytest.mark.parametrize("flag", ["--json", "--json-bare"])
def test_print_to_such_a_file_reports_the_job_it_sent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flag: str
) -> None:
    """The job is written before the answer: a crash here makes a caller retry and print it twice."""
    target = str(tmp_path / "\udcff.out")
    code, out = _run([flag, "print", _label(tmp_path), "--printer", f"file:{target}"], capsys)
    assert code == 0, out
    document = json.loads(out)
    assert document["ok"] is True
    assert document["data"]["printer"] == f"file:{target}"
    assert document["data"]["bytes_sent"] == Path(target).stat().st_size


@_BYTE_NAMES
def test_render_to_such_a_file_answers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "\udcfe.dpl"
    code, out = _run(["--json-bare", "render", _label(tmp_path), "-o", str(target)], capsys)
    assert code == 0, out
    assert json.loads(out)["ok"] is True
    assert target.stat().st_size > 0


@pytest.mark.os_agnostic
def test_an_error_naming_such_a_file_is_an_envelope(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A surrogate in the message: no file is needed, so this runs wherever a name can be spelled."""
    code, out = _run(["--json-bare", "validate", str(tmp_path / "F\udcfcr.json")], capsys)
    assert code == 2, out
    document = json.loads(out)
    assert document["ok"] is False
    assert "F\udcfcr.json" in document["message"]


@pytest.mark.os_agnostic
def test_the_envelope_keeps_every_other_character_as_it_is(capsys: pytest.CaptureFixture[str]) -> None:
    """An escape only for what no encoding can write: readable text stays readable."""
    code, out = _run(["--json-bare", "validate", "Für-\udcfc.json"], capsys)
    assert code == 2, out
    assert "Für-" in out
    assert "\\udcfc" in out
