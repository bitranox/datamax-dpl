"""scripts/length_ladder.py numbers each tick by the printed dot row the format length check counts.

The number the user reads beside the last tick fully printed becomes ``max_format_rows``, so these
tests prove the numbering through the encoder itself: an image cut off at a tick reaches exactly the
row its number names (``max_format_rows`` at that row renders, one row less refuses).
"""

from __future__ import annotations

import json
import os
import runpy
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest

from datamax_dpl import __init__conf__
from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.composition import build_production
from datamax_dpl.domain.enums import FindingCode, Severity
from datamax_dpl.domain.label import Label, Text
from datamax_dpl.domain.overlap import DotArea, inexact_rectangle, printed_dots
from datamax_dpl.domain.render import build_job, job_context

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner

    from datamax_dpl.domain.render_context import RenderOptions

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "length_ladder.py"
_ENV_PREFIX = __init__conf__.LAYEREDCONF_SLUG.upper().replace("-", "_")
#: The Allegro 2's image memory budget; a ladder past it would be halved and its rows doubled.
_ALLEGRO_2_BUDGET = 44535
_SPINE = "##"


class _Ladder(Protocol):
    document: dict[str, Any]
    bottom: int
    top: int
    ticks: dict[int, int]
    numbers: tuple[int, ...]


class _Script(Protocol):
    def main(self, argv: list[str]) -> int: ...
    def ladder_options(self, model: str) -> RenderOptions: ...
    def build_ladder(self, options: RenderOptions, *, first: int = ..., last: int = ...) -> _Ladder: ...


class _Namespace:
    """The script's functions, loaded from its file the way a maintainer runs it."""

    def __init__(self) -> None:
        self._names = runpy.run_path(str(_SCRIPT), run_name="length_ladder")

    def main(self, argv: list[str]) -> int:
        return cast("int", self._names["main"](argv))

    def ladder_options(self, model: str) -> RenderOptions:
        return cast("RenderOptions", self._names["ladder_options"](model))

    def build_ladder(self, options: RenderOptions, *, first: int = 1600, last: int = 1760) -> _Ladder:
        return cast("_Ladder", self._names["build_ladder"](options, first=first, last=last))


def _script() -> _Script:
    return _Namespace()


@pytest.fixture(autouse=True)
def no_ambient_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clear_config_cache: None) -> None:
    """Keep a developer's ``DATAMAX_DPL___`` variables and ``.env`` out of the CLI the command runs."""
    for name in [name for name in os.environ if name.startswith(_ENV_PREFIX)]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


def _run(tmp_path: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[dict[str, Any], str]:
    """Run the script; return its JSON line and the command line it printed."""
    assert _script().main(["--out", str(tmp_path / "out"), *extra]) == 0
    first, command = capsys.readouterr().out.splitlines()
    return cast("dict[str, Any]", json.loads(first)), command


def _label(summary: dict[str, Any]) -> Label:
    return Label.model_validate_json(Path(summary["file"]).read_text(encoding="utf-8"))


def _only_the_image_up_to(document: dict[str, Any], index: int) -> Label:
    """The ladder's image alone, its rows above bitmap row ``index`` cut off; it still stands at y 0."""
    image = dict(document["elements"][0])
    image["bitmap"] = image["bitmap"][index:]
    return Label.model_validate({**document, "elements": [image]})


def _codes(label: Label, options: RenderOptions) -> list[FindingCode]:
    return [finding.code for finding in build_job(label, options).errors]


@pytest.mark.os_agnostic
def test_the_ladder_renders_clean_in_strict_with_its_image_whole(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Strict refuses every deviation (a clamped offset, a halved image), so clean means placed as built."""
    summary, _ = _run(tmp_path, capsys)
    options = _script().ladder_options("allegro-2")

    result = build_job(_label(summary), options)

    assert [(finding.code, finding.severity) for finding in result.findings] == [
        (FindingCode.MODEL_DEFAULTS_APPLIED, Severity.INFO)
    ]
    assert result.halved_elements == ()
    assert result.image_bytes == summary["image_bytes"] == 3 * (summary["image_rows"][1] + 1)
    assert result.image_bytes < _ALLEGRO_2_BUDGET


@pytest.mark.os_agnostic
def test_without_the_raised_limit_the_ladder_passes_the_allegro_2_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    summary, _ = _run(tmp_path, capsys)
    options = _script().ladder_options("allegro-2").model_copy(update={"max_format_rows": None})

    assert FindingCode.FORMAT_PAST_MAX_LENGTH in _codes(_label(summary), options)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("row", [100, 1600, 1655, 1760])
def test_a_tick_prints_on_the_row_its_number_names(row: int) -> None:
    """Cut the image off at the tick: the format length check then puts its top on exactly ``row``."""
    script = _script()
    options = script.ladder_options("allegro-2")
    ladder = script.build_ladder(options)
    index = ladder.ticks[row]
    bitmap: list[str] = ladder.document["elements"][0]["bitmap"]

    assert bitmap[index] != _SPINE + "." * 22
    assert bitmap[index - 1] == bitmap[index + 1] == _SPINE + "." * 22
    cut = _only_the_image_up_to(ladder.document, index)
    assert _codes(cut, options.model_copy(update={"max_format_rows": row})) == []
    assert _codes(cut, options.model_copy(update={"max_format_rows": row - 1})) == [FindingCode.FORMAT_PAST_MAX_LENGTH]


@pytest.mark.os_agnostic
def test_the_ticks_are_every_5_rows_up_from_first_and_every_100_below_it() -> None:
    script = _script()
    ladder = script.build_ladder(script.ladder_options("allegro-2"))
    bitmap: list[str] = ladder.document["elements"][0]["bitmap"]

    inked = {index for index, line in enumerate(bitmap) if line != _SPINE + "." * 22}
    assert inked == set(ladder.ticks.values())
    assert sorted(ladder.ticks) == [*range(100, 1600, 100), *range(1600, 1761, 5)]
    assert {row for row in ladder.ticks if bitmap[ladder.ticks[row]] == "#" * 24} == {
        row for row in ladder.ticks if row % 10 == 0
    }
    assert ladder.top == 1770


@pytest.mark.os_agnostic
def test_each_number_stands_centred_on_its_tick_beside_the_image() -> None:
    """The rectangle is the one the format check and the overlap check place text by."""
    script = _script()
    options = script.ladder_options("allegro-2")
    ladder = script.build_ladder(options)
    label = Label.model_validate(ladder.document)
    ctx = job_context(label, options)
    image = printed_dots(label.elements[0], ctx)
    assert image is not None

    areas: dict[int, DotArea] = {}
    for element in label.elements[1:]:
        area = inexact_rectangle(element, ctx)
        assert isinstance(element, Text)
        assert area is not None
        areas[int(element.text)] = area
    assert sorted(areas) == [*range(100, 1600, 100), *range(1600, 1761, 20)]
    for row, area in areas.items():
        assert area.bottom <= row <= area.top
        assert abs(area.bottom + (area.height - 1) / 2 - row) <= 1
        assert area.left > image.area.right
    tops = [areas[row].top for row in sorted(areas)]
    bottoms = [areas[row].bottom for row in sorted(areas)]
    assert all(top < bottom for top, bottom in zip(tops[:-1], bottoms[1:], strict=True))


@pytest.mark.os_agnostic
def test_the_printed_command_renders_exactly_the_job_the_rows_were_counted_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], cli_runner: CliRunner
) -> None:
    """The command line pins scale, offset and limit, so the CLI's job is the script's own."""
    summary, command = _run(tmp_path, capsys)
    argv: list[str] = summary["argv"]
    if sys.platform != "win32":
        assert shlex.split(command) == argv
    output = tmp_path / "ladder.prn"
    words = ["render" if word == "print" else word for word in argv[1:]]

    result = cli_runner.invoke(cli_mod.cli, [*words, "-o", str(output)], obj=build_production)

    assert result.exit_code == 0, result.output
    assert output.read_bytes() == build_job(_label(summary), _script().ladder_options("allegro-2")).data
    assert argv[argv.index("--printer") + 1] == "tcp://192.0.2.60:9100"


@pytest.mark.os_agnostic
def test_another_tick_range_is_numbered_the_same_way(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    summary, _ = _run(tmp_path, capsys, "--first", "1400", "--last", "1600")
    script = _script()
    options = script.ladder_options("allegro-2")
    ladder = script.build_ladder(options, first=1400, last=1600)
    cut = _only_the_image_up_to(ladder.document, ladder.ticks[1455])

    assert summary["ticks"] == [1400, 1600]
    assert _codes(cut, options.model_copy(update={"max_format_rows": 1455})) == []
    assert _codes(cut, options.model_copy(update={"max_format_rows": 1454})) == [FindingCode.FORMAT_PAST_MAX_LENGTH]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("first", "last"), [("1610", "1760"), ("1600", "1762"), ("1600", "1580")])
def test_a_tick_range_off_the_grid_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], first: str, last: str
) -> None:
    main: Callable[[list[str]], int] = _script().main
    assert main(["--out", str(tmp_path), "--first", first, "--last", last]) == 2
    assert "error: --" in capsys.readouterr().err
