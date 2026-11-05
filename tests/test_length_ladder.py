"""scripts/length_ladder.py numbers each tick and step by the printed dot row the format length check counts.

The last tick fully printed is a floor (the format ends there or up to 4 rows above); the last step
of the ``--steps`` label that printed is the exact row that becomes ``max_format_rows``. So these
tests prove both numberings through the encoder itself: an image cut off at a tick or a step reaches
exactly the row its number names (``max_format_rows`` at that row renders, one row less refuses).
"""

from __future__ import annotations

import itertools
import json
import runpy
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.composition import build_production
from datamax_dpl.domain.enums import FindingCode, Severity
from datamax_dpl.domain.label import Label, Line, Text
from datamax_dpl.domain.overlap import DotArea, inexact_rectangle, printed_dots
from datamax_dpl.domain.render import build_job, effective_calibration, job_context
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner, Result

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "length_ladder.py"
_PRINTER = "tcp://192.0.2.60:9100"
_SPINE = "##"
#: An on-site calibration the ladder must survive: another scale and the model's offset.
_AMBIENT_CALIBRATION = "[printer]\nscale_along = 1.0\noffset_along = -0.25\n"

pytestmark = pytest.mark.usefixtures("no_ambient_configuration")


class _Ladder(Protocol):
    document: dict[str, Any]
    bottom: int
    top: int
    ticks: dict[int, int]
    numbers: tuple[int, ...]


class _Steps(Protocol):
    document: dict[str, Any]
    reference: int
    bottom: int
    top: int
    steps: dict[int, int]


class _Script(Protocol):
    def main(self, argv: list[str]) -> int: ...
    def ladder_options(self, model: str) -> RenderOptions: ...
    def build_ladder(self, options: RenderOptions, *, first: int = ..., last: int = ...) -> _Ladder: ...
    def build_steps(self, options: RenderOptions, *, reference: int) -> _Steps: ...


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

    def build_steps(self, options: RenderOptions, *, reference: int) -> _Steps:
        return cast("_Steps", self._names["build_steps"](options, reference=reference))


def _script() -> _Script:
    return _Namespace()


def _run(tmp_path: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[dict[str, Any], str]:
    """Run the script; return its JSON line and the command line it printed."""
    assert _script().main(["--out", str(tmp_path / "out"), "--printer", _PRINTER, *extra]) == 0
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

    # The ladder configures no cutter, so the software switches stay as the printer holds them.
    assert [(finding.code, finding.severity) for finding in result.findings] == [
        (FindingCode.MODEL_DEFAULTS_APPLIED, Severity.INFO),
        (FindingCode.SOFTWARE_SWITCHES_NOT_SENT, Severity.INFO),
    ]
    assert result.halved_elements == ()
    assert result.image_bytes == summary["image_bytes"] == 3 * (summary["image_rows"][1] + 1)
    budget = effective_calibration(RenderOptions(model="allegro-2")).image_memory_bytes
    assert budget is not None
    assert result.image_bytes < budget


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


def _render_twin(cli_runner: CliRunner, argv: list[str], output: Path) -> Result:
    """Run the printed print command as ``render -o output`` through the production CLI."""
    words = ["render" if word == "print" else word for word in argv[1:]]
    return cli_runner.invoke(cli_mod.cli, [*words, "-o", str(output)], obj=build_production)


def _without_override(argv: list[str], key: str) -> list[str]:
    """``argv`` without its ``--set <key>=...`` pair."""
    index = next(index for index, word in enumerate(argv) if word.startswith(f"{key}="))
    assert argv[index - 1] == "--set"
    return argv[: index - 1] + argv[index + 1 :]


@pytest.mark.os_agnostic
def test_the_printed_command_beats_an_ambient_calibration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], cli_runner: CliRunner, user_layer_config_file: Path
) -> None:
    """A configured scale 1.0 and offset -0.25 mm, the on-site risk, leave the job as the rows were counted.

    The scale cannot reach a label in dots, so no override is needed for it. The offset can: the
    control drops the command's offset override, and the ambient -0.25 mm then pushes the image past
    the label edge and is refused, which also shows the ambient layer is read.
    """
    user_layer_config_file.write_text(_AMBIENT_CALIBRATION, encoding="utf-8")
    summary, command = _run(tmp_path, capsys)
    argv: list[str] = summary["argv"]
    if sys.platform != "win32":
        assert shlex.split(command) == argv
    expected = build_job(_label(summary), _script().ladder_options("allegro-2")).data

    result = _render_twin(cli_runner, argv, tmp_path / "ladder.prn")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "ladder.prn").read_bytes() == expected

    unshifted = _render_twin(cli_runner, _without_override(argv, "printer.offset_along"), tmp_path / "unshifted.prn")
    assert unshifted.exit_code != 0
    assert "offset_past_label_edge" in unshifted.output
    assert argv[argv.index("--printer") + 1] == _PRINTER


@pytest.mark.os_agnostic
def test_the_printer_must_be_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The address only goes into the printed command; a forgotten one must not fall back to anything."""
    with pytest.raises(SystemExit) as exited:
        _script().main(["--out", str(tmp_path)])
    assert exited.value.code == 2
    assert "--printer" in capsys.readouterr().err


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
    assert main(["--out", str(tmp_path), "--printer", _PRINTER, "--first", first, "--last", last]) == 2
    assert "error: --" in capsys.readouterr().err


# The step label: the user's on-site design of 2026-10-30, "each step like 5mm long with a 2mm
# distance between them, and print a small number 1 2 3 4 5 under them", starting further right
# than a reference line on the last row the coarse ladder showed whole.
_REFERENCE = 1645


def _mm_dots(mm: float, dpi: int) -> int:
    return round(mm * dpi / 25.4)


def _ink(line: str) -> list[int]:
    return [column for column, char in enumerate(line) if char == "#"]


def _steps_context(steps: _Steps, options: RenderOptions) -> tuple[Label, Any]:
    label = Label.model_validate(steps.document)
    return label, job_context(label, options)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("step", [1, 2, 3, 4, 5])
def test_each_step_prints_on_the_row_its_number_names(step: int) -> None:
    """Cut the image off at a step: the format length check then puts its top on exactly that row."""
    script = _script()
    options = script.ladder_options("allegro-2")
    steps = script.build_steps(options, reference=_REFERENCE)
    row = _REFERENCE + step
    number = row - 1640
    index = steps.top - row

    assert steps.steps[number] == row
    cut = _only_the_image_up_to(steps.document, index)
    assert _codes(cut, options.model_copy(update={"max_format_rows": row})) == []
    assert _codes(cut, options.model_copy(update={"max_format_rows": row - 1})) == [FindingCode.FORMAT_PAST_MAX_LENGTH]


@pytest.mark.os_agnostic
def test_the_steps_are_5_mm_long_2_mm_apart_right_of_a_reference_line() -> None:
    script = _script()
    options = script.ladder_options("allegro-2")
    steps = script.build_steps(options, reference=_REFERENCE)
    _, ctx = _steps_context(steps, options)
    dpi = ctx.profile.dpi
    bitmap: list[str] = steps.document["elements"][0]["bitmap"]

    assert steps.top == _REFERENCE + 5
    assert steps.bottom <= _REFERENCE
    assert len(bitmap) == steps.top - steps.bottom + 1
    assert all(_ink(line) == [] for line in bitmap[steps.top - _REFERENCE + 1 :])
    reference = _ink(bitmap[steps.top - _REFERENCE])
    assert reference == list(range(_mm_dots(20, dpi)))
    cut = _only_the_image_up_to(steps.document, steps.top - _REFERENCE)
    assert _codes(cut, options.model_copy(update={"max_format_rows": _REFERENCE})) == []
    assert _codes(cut, options.model_copy(update={"max_format_rows": _REFERENCE - 1})) == [
        FindingCode.FORMAT_PAST_MAX_LENGTH
    ]
    starts: list[int] = []
    for step in range(1, 6):
        ink = _ink(bitmap[steps.top - (_REFERENCE + step)])
        assert ink == list(range(ink[0], ink[0] + _mm_dots(5, dpi)))
        starts.append(ink[0])
    assert starts[0] > reference[-1] + _mm_dots(5, dpi)
    assert [b - a for a, b in itertools.pairwise(starts)] == [_mm_dots(7, dpi)] * 4


@pytest.mark.os_agnostic
def test_each_number_stands_under_its_step_below_the_reference_row() -> None:
    """A number below the image prints even when its step was cut, so a missing step is named."""
    script = _script()
    options = script.ladder_options("allegro-2")
    steps = script.build_steps(options, reference=_REFERENCE)
    label, ctx = _steps_context(steps, options)
    image = printed_dots(label.elements[0], ctx)
    assert image is not None
    bitmap: list[str] = steps.document["elements"][0]["bitmap"]

    assert image.area.bottom == steps.bottom <= _REFERENCE
    texts: dict[str, DotArea] = {}
    for element in (element for element in label.elements if isinstance(element, Text)):
        area = inexact_rectangle(element, ctx)
        assert area is not None
        texts[element.text] = area
    assert sorted(texts, key=int) == ["6", "7", "8", "9", "10", str(_REFERENCE)]
    for area in texts.values():
        assert area.top < image.area.bottom
    for number in range(6, 11):
        ink = _ink(bitmap[steps.top - steps.steps[number]])
        left = image.area.left + ink[0]
        assert left <= texts[str(number)].left <= image.area.left + ink[-1]
    assert texts[str(_REFERENCE)].left <= image.area.left + _ink(bitmap[steps.top - _REFERENCE])[-1]


@pytest.mark.os_agnostic
def test_a_full_height_line_beside_the_steps_makes_the_format_reach_past_them() -> None:
    """The Allegro 2 cuts at 1648 only when one element runs the label's whole height (on site
    2026-11-05: the steps alone all printed, the same label with a line from row 0 stopped at 1648),
    so the step label carries that line itself, left of the image and clear of it."""
    script = _script()
    options = script.ladder_options("allegro-2")
    steps = script.build_steps(options, reference=_REFERENCE)
    label, ctx = _steps_context(steps, options)
    image = printed_dots(label.elements[0], ctx)
    assert image is not None
    lines = [element for element in label.elements if isinstance(element, Line)]

    assert len(lines) == 1
    line = printed_dots(lines[0], ctx)
    assert line is not None
    assert line.area.bottom == script.build_ladder(options).bottom
    assert line.area.top > steps.top
    assert line.area.right < image.area.left
    assert line.area.width >= 2
    texts = [inexact_rectangle(element, ctx) for element in label.elements if isinstance(element, Text)]
    assert all(area is not None and line.area.right < area.left for area in texts)


@pytest.mark.os_agnostic
def test_the_step_label_renders_clean_and_small(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Only the rows near the cut are an image, so the job stays small over a 9600-baud link."""
    summary, _ = _run(tmp_path, capsys, "--steps", str(_REFERENCE))
    options = _script().ladder_options("allegro-2")

    result = build_job(_label(summary), options)

    assert Path(summary["file"]).name == "length-steps.json"
    assert [finding.severity for finding in result.findings] == [Severity.INFO] * len(result.findings)
    assert result.halved_elements == ()
    assert result.image_bytes == summary["image_bytes"] < 1024
    assert summary["reference"] == _REFERENCE
    assert summary["steps"] == {str(n): 1640 + n for n in range(6, 11)}
    assert summary["image_rows"][0] <= _REFERENCE
    assert summary["image_rows"][1] == _REFERENCE + 5
    assert len(result.data) < 2048


@pytest.mark.os_agnostic
def test_without_the_raised_limit_the_steps_pass_the_allegro_2_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    summary, _ = _run(tmp_path, capsys, "--steps", str(_REFERENCE))
    options = _script().ladder_options("allegro-2").model_copy(update={"max_format_rows": None})

    assert FindingCode.FORMAT_PAST_MAX_LENGTH in _codes(_label(summary), options)


@pytest.mark.os_agnostic
def test_the_printed_steps_command_renders_the_label_as_built(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], cli_runner: CliRunner, user_layer_config_file: Path
) -> None:
    user_layer_config_file.write_text(_AMBIENT_CALIBRATION, encoding="utf-8")
    summary, _ = _run(tmp_path, capsys, "--steps", str(_REFERENCE))
    expected = build_job(_label(summary), _script().ladder_options("allegro-2")).data

    result = _render_twin(cli_runner, summary["argv"], tmp_path / "steps.prn")

    assert result.exit_code == 0, result.output
    assert (tmp_path / "steps.prn").read_bytes() == expected


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "extra", [("--steps", "0"), ("--steps", "1645", "--first", "1600"), ("--steps", "1645", "--last", "1760")]
)
def test_a_bad_steps_request_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: tuple[str, ...]
) -> None:
    main: Callable[[list[str]], int] = _script().main
    assert main(["--out", str(tmp_path), "--printer", _PRINTER, *extra]) == 2
    assert "error: --" in capsys.readouterr().err
    assert not (tmp_path / "length-steps.json").exists()
