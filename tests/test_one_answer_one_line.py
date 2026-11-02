"""stdout carries one envelope and an error is one line on stderr, whatever the input or the timing."""

from __future__ import annotations

import io
import json
import sys
from typing import TYPE_CHECKING, cast

import pytest

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.exit_codes import ExitCode
from datamax_dpl.adapters.cli.main import main
from datamax_dpl.composition import build_production
from datamax_dpl.domain.printable import one_line, printable

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

    from datamax_dpl.composition import AppServices


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "hostile",
    [
        pytest.param("x\nOK: model dmx-600, 1 label(s)", id="newline"),
        pytest.param("x\tOK", id="tab"),
        pytest.param(f"x{chr(0x2028)}OK", id="line-separator"),
        pytest.param(f"x{chr(0x2029)}OK", id="paragraph-separator"),
        pytest.param(f"x{chr(0x202E)}KO", id="right-to-left-override"),
        pytest.param(f"x{chr(0x2066)}KO{chr(0x2069)}", id="isolate"),
    ],
)
def test_a_label_key_cannot_forge_a_line_in_the_error(cli_runner: CliRunner, tmp_path: Path, hostile: str) -> None:
    label = tmp_path / "label.json"
    document = {"elements": [{"type": "text", "x": 1, "y": 1, "text": "a"}], hostile: 1}
    label.write_text(json.dumps(document), encoding="utf-8")
    result = cli_runner.invoke(cli_mod.cli, ["validate", str(label), "--model", "dmx-600"], obj=build_production)
    assert result.exit_code == 2, result.output
    said = result.stderr.removesuffix("\n")
    assert said.startswith("Error: ")
    assert not any(character in said for character in hostile if not character.isprintable()), said


@pytest.mark.os_agnostic
def test_a_file_name_cannot_forge_a_line_in_the_error(cli_runner: CliRunner, tmp_path: Path) -> None:
    """No file is made: a newline is no character for a name on Windows, and a missing one is refused the same."""
    missing = str(tmp_path / "gone\nOK: sent 10 bytes.json")
    result = cli_runner.invoke(cli_mod.cli, ["validate", missing, "--model", "dmx-600"], obj=build_production)
    assert result.exit_code == 2, result.output
    assert len(result.stderr.splitlines()) == 1, result.stderr


@pytest.mark.os_agnostic
def test_the_envelope_keeps_the_text_as_it_is_for_a_program_to_read(cli_runner: CliRunner, tmp_path: Path) -> None:
    """JSON escapes it already, and a reader of the message gets what the file holds."""
    missing = str(tmp_path / "gone\nb.json")
    args = ["--json-bare", "validate", missing, "--model", "dmx-600"]
    result = cli_runner.invoke(cli_mod.cli, args, obj=build_production)
    assert "gone\nb.json" in json.loads(result.stdout)["message"]


@pytest.mark.os_agnostic
def test_a_multi_line_view_keeps_its_lines() -> None:
    assert printable("first\nsecond\tcolumn") == "first\nsecond\tcolumn"
    assert one_line("first\nsecond\tcolumn") == "first\\x0asecond\\x09column"


class _InterruptedAtFlush(io.StringIO):
    """A stdout where the Ctrl-C lands once the envelope's bytes are out and the flush is under way."""

    encoding = "utf-8"

    def __init__(self) -> None:
        super().__init__()
        self.interrupted = False

    def flush(self) -> None:
        if self.getvalue() and not self.interrupted:
            self.interrupted = True
            raise KeyboardInterrupt


@pytest.mark.os_agnostic
def test_an_interrupt_after_the_envelope_adds_no_second_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A real terminal delivers this at any point; stdout is the one seam that can place it exactly."""
    label = tmp_path / "label.json"
    label.write_text('{"elements": [{"type": "text", "x": 1, "y": 1, "text": "a"}]}', encoding="utf-8")
    stdout = _InterruptedAtFlush()
    monkeypatch.setattr(sys, "stdout", stdout)
    code = main(
        ["--json-bare", "validate", str(label), "--model", "dmx-600"],
        services_factory=build_production,
        restore_traceback=False,
    )
    assert code == ExitCode.SIGNAL_INT
    answers = stdout.getvalue().splitlines()
    assert [json.loads(line)["ok"] for line in answers] == [True]
    assert "interrupted" in capsys.readouterr().err


@pytest.mark.os_agnostic
def test_an_interrupt_before_any_envelope_still_writes_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The run before this one may have written an envelope: each run starts over."""
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    label = tmp_path / "label.json"
    label.write_text('{"elements": [{"type": "text", "x": 1, "y": 1, "text": "a"}]}', encoding="utf-8")
    args = ["--json-bare", "validate", str(label), "--model", "dmx-600"]
    assert main(args, services_factory=build_production, restore_traceback=False) == 0

    def interrupted() -> None:
        raise KeyboardInterrupt

    stdout.truncate(0)
    stdout.seek(0)
    code = main(args, services_factory=cast("Callable[[], AppServices]", interrupted), restore_traceback=False)
    assert code == ExitCode.SIGNAL_INT
    assert json.loads(stdout.getvalue())["error"] == "KeyboardInterrupt"
    capsys.readouterr()
