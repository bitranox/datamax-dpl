"""A failure naming a file names it as the user typed it, not as Python's repr spells it.

``str()`` of an ``OSError`` quotes its file name with ``repr``, which doubles every backslash: on
Windows every path read ``C:\\\\Users\\\\...``, a path nobody typed and no shell accepts. A file name
holding a backslash shows the same on POSIX, which is how these tests see it on every system.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.cli.envelope import error_text
from datamax_dpl.adapters.cli.main import main
from datamax_dpl.composition import build_production

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.usefixtures("no_ambient_configuration")

_LABEL = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "{a}"}]}
_WINDOWS_PATH = r"C:\Users\me\labels\job.dpl"


@pytest.mark.os_agnostic
def test_an_os_error_naming_a_file_says_its_reason_and_the_path_as_typed() -> None:
    assert error_text(OSError(2, "No such file or directory", _WINDOWS_PATH)) == (
        r"No such file or directory: C:\Users\me\labels\job.dpl"
    )


@pytest.mark.os_agnostic
def test_an_os_error_naming_two_files_names_both_as_typed() -> None:
    # The fourth argument is the Windows error code, on every system: the second name comes fifth.
    error = OSError(13, "Access is denied", r"C:\a\new.tmp", None, r"C:\a\job.dpl")
    assert error_text(error) == r"Access is denied: C:\a\new.tmp -> C:\a\job.dpl"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("error", "said"),
    [
        (OSError(28, "No space left on device"), "[Errno 28] No space left on device"),
        (OSError("disk on fire"), "disk on fire"),
        (MemoryError(), "MemoryError"),
        (ValueError("bad value"), "bad value"),
    ],
    ids=["no-file", "no-errno", "no-message", "not-os-error"],
)
def test_an_error_naming_no_file_says_what_it_said(error: BaseException, said: str) -> None:
    assert error_text(error) == said


def _files(tmp_path: Path) -> tuple[str, str]:
    label = tmp_path / "label.json"
    label.write_text(json.dumps(_LABEL), encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_bytes(b"a\nr0\nr1\n")
    return str(label), str(data)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "route",
    ["render-label", "render-batch", "print-label", "print-batch"],
)
def test_a_target_that_cannot_be_written_is_named_as_typed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], route: str
) -> None:
    """A directory holding a backslash in its name: a separator on Windows, a plain character elsewhere."""
    label, data = _files(tmp_path)
    target = str(tmp_path / "back\\slash" / "missing" / "job.dpl")
    batch = ["--data", data] if route.endswith("batch") else []
    if route.startswith("print"):
        args = ["--json-bare", "print", label, *batch, "--printer", f"file:{target}", "--model", "dmx-600"]
    else:
        args = ["--json-bare", "render", label, *batch, "--model", "dmx-600", "-o", target]
    code = main(args, services_factory=build_production)
    captured = capsys.readouterr()
    message = json.loads(captured.out)["message"]
    assert code == 2, captured
    assert target in message
    assert target.replace("\\", "\\\\") not in message
    assert target in captured.err


@pytest.mark.os_windows
@pytest.mark.skipif(sys.platform != "win32", reason="a drive-letter path exists on Windows only")
def test_a_windows_path_is_named_with_single_backslashes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    label, _data = _files(tmp_path)
    target = str(tmp_path / "missing" / "job.dpl")
    code = main(["--json-bare", "render", label, "--model", "dmx-600", "-o", target], services_factory=build_production)
    message = json.loads(capsys.readouterr().out)["message"]
    assert code == 2
    assert target in message
    assert "\\\\" not in message
