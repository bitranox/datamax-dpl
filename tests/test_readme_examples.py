"""Every Python example in README.md runs and prints what a reader can use."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_README = Path(__file__).resolve().parents[1] / "README.md"
_BLOCK = re.compile(r"^```python\n(.*?)^```$", re.MULTILINE | re.DOTALL)
#: How a str Enum prints when the example forgets ``.value``: the class name, not the code a reader looks up.
_ENUM_REPR = re.compile(r"\b(?:FindingCode|Severity)\.[A-Z_]+")


def _examples() -> list[str]:
    return _BLOCK.findall(_README.read_text(encoding="utf-8"))


@pytest.mark.os_agnostic
def test_the_readme_still_holds_its_example() -> None:
    """A changed fence must not leave the test below with nothing to run."""
    assert len(_examples()) == 1


@pytest.mark.os_agnostic
@pytest.mark.parametrize("example", _examples(), ids=lambda example: example.splitlines()[0])
def test_a_readme_example_runs_and_prints_plain_values(example: str, tmp_path: Path) -> None:
    script = tmp_path / "example.py"
    script.write_text(example, encoding="utf-8")
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    finished = subprocess.run(  # noqa: S603 - this interpreter running the README's own example
        [sys.executable, str(script)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
        cwd=tmp_path,
        env=environment,
    )
    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip()
    assert _ENUM_REPR.findall(finished.stdout) == []
    # A comment showing printed bytes is what the example prints.
    shown = [line.removeprefix("# ") for line in example.splitlines() if line.startswith("# b'")]
    assert [line for line in shown if line not in finished.stdout.splitlines()] == []
