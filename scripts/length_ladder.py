"""Write a label that measures, on paper, how many dot rows one format of a printer prints.

The label holds one downloaded image, 24 dots wide, standing on the bottom edge of the label: a
2-dot spine from the bottom to the top, and a 1-row tick on every 5th printed dot row from
``--first`` to ``--last`` (default 1600 to 1760; every 10th tick is long). A number every 20 rows
beside the image names the row of the tick it stands next to, and below ``--first`` a long tick
with its number every 100 rows gives a coarse scale in case the format ends lower than expected.

Every number is the printed dot row its tick sits on, counted the way the ``format_past_max_length``
check counts (the row the encoder commands, offset included): image rows count one printed row
each, and the image's bottom row and every number are placed by the encoder, so the rows here
are read from the encoder, never assumed. A printer that cuts the format off prints the ticks up to its
limit; the number of the last tick fully printed (the next number down plus 5 per tick above it,
when its own number was cut) is only a floor: the format ends on that row or up to 4 rows above it.

``--steps ROW`` then reads the exact row. It writes the step label: a reference line on ``ROW``
(the last whole tick) and, on each of the 5 rows above it, one step one dot row high, 5 mm long and
2 mm after the one before, rising to the right. A number under each step names it: the step's row
less ``ROW`` rounded down to ten, so the steps above 1645 read 6 to 10, and ``ROW`` itself stands
under the reference line. The numbers sit below the image, so they print where the format cuts a
step off; the row of the last step that printed is the value for ``max_format_rows``. Only those
rows are an image (about 0.5 KB on an Allegro 2), so the job stays small over a slow serial link.

The label is in dots, and lengths in dots count printed dots, so no ``scale_along`` changes it;
an ``offset_along`` does (millimetres moved at the model's resolution). The label is built with
``offset_along`` 0, which stands the image's bottom on row 0 with nothing to clamp, and with
``max_format_rows`` 10000, so the library itself does not refuse it; the printed command line
sets both, so a configured offset or limit cannot move or refuse the ladder. ``--policy strict``
makes it refuse rather than print a ladder whose image was halved or whose fields were moved.
Pass the printer as a URI: a named printer whose section sets its own offset or max_format_rows
wins over ``--set printer.*``.

Usage::

    .venv/bin/python scripts/length_ladder.py --out length --printer tcp://192.0.2.60:9100
    .venv/bin/python scripts/length_ladder.py --out length --printer tcp://192.0.2.60:9100 --first 1400 --last 1600
    .venv/bin/python scripts/length_ladder.py --out length --printer tcp://192.0.2.60:9100 --steps 1645

``--printer`` is required: it only goes into the printed command, so a forgotten one fails at once
rather than printing a command aimed at the wrong address.

The first output line is JSON (the label file, the printed rows the image spans, the tick range,
its image bytes, the finding codes of a strict render and the command as a list); the second is
the command to run, for example::

    datamax-dpl --set printer.max_format_rows=10000 --set printer.offset_along=0 \\
        print length/length-ladder.json --model allegro-2 --policy strict \\
        --printer tcp://192.0.2.60:9100
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from datamax_dpl.domain.enums import FallbackPolicy, Severity
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.overlap import inexact_rectangle, printed_dots
from datamax_dpl.domain.render import RenderResult, build_job, job_context
from datamax_dpl.domain.render_context import RenderContext, RenderOptions
from datamax_dpl.domain.units import Axis

__all__ = [
    "BAD_ARGUMENTS",
    "DEFAULT_FIRST",
    "DEFAULT_LAST",
    "IMAGE_NAME",
    "LADDER_FORMAT_ROWS",
    "NOT_CLEAN",
    "STEP_COUNT",
    "Ladder",
    "Steps",
    "build_ladder",
    "build_steps",
    "ladder_options",
    "main",
]

#: First and last printed row with a fine tick: around the Allegro 2's 1648 rows.
DEFAULT_FIRST = 1600
DEFAULT_LAST = 1760
#: The format limit the ladder is built and printed with: far past any tick, so nothing refuses it.
LADDER_FORMAT_ROWS = 10_000
#: Name the image is downloaded under.
IMAGE_NAME = "LENGTH"
#: Exit code for a ``--first`` or ``--last`` that does not fit the tick grid.
BAD_ARGUMENTS = 2
#: Exit code when a strict render of the ladder has an error or warning finding.
NOT_CLEAN = 3
#: How many single-row steps the step label prints above its reference row: one per row a ladder
#: tick (every 5 rows) leaves open.
STEP_COUNT = 5

_IMAGE_X_DOTS = 8
_IMAGE_WIDTH_DOTS = 24
_SPINE_DOTS = 2
_SHORT_TICK_DOTS = 12
_TICK_STEP = 5
_LONG_TICK_STEP = 10
_NUMBER_STEP = 20
_COARSE_STEP = 100
# The spine goes on above the last tick, so that tick is never mistaken for where the image ends.
_HEADROOM_ROWS = 10
_TEXT_GAP_DOTS = 8
_TEXT_FONT = 2
_TEXT_WIDTH_DOTS = 48
# The label runs this far past the image, so its length never cuts the ladder before the format does.
_LABEL_MARGIN_ROWS = 100
# The step label's geometry in millimetres, the user's design: a reference line at the left, then
# steps 5 mm long with 2 mm between them, the first one well to the right of the line's end.
_REFERENCE_MM = 20.0
_FIRST_STEP_MM = 30.0
_STEP_MM = 5.0
_STEP_GAP_MM = 2.0
_MM_PER_INCH = 25.4


@dataclass(frozen=True)
class Ladder:
    """One ladder label and where its ticks print.

    Attributes:
        document: The label JSON document.
        bottom: Printed dot row of the image's bottom row.
        top: Printed dot row of the image's top row.
        ticks: Printed dot row of each tick, mapped to its bitmap row index (0 is the top row).
        numbers: Printed dot rows that carry a number beside them.
    """

    document: dict[str, object]
    bottom: int
    top: int
    ticks: dict[int, int]
    numbers: tuple[int, ...]


@dataclass(frozen=True)
class Steps:
    """One step label and the row each of its steps prints on.

    Attributes:
        document: The label JSON document.
        reference: Printed dot row of the reference line.
        bottom: Printed dot row of the image's bottom row: the reference row, or the nearest row
            below it the encoder can place an image on (blank rows fill the gap).
        top: Printed dot row of the last step, the image's top row.
        steps: The number printed under each step, mapped to the printed dot row of that step.
    """

    document: dict[str, object]
    reference: int
    bottom: int
    top: int
    steps: dict[int, int]


def ladder_options(model: str) -> RenderOptions:
    """Return the options the ladder is built for: strict, offset 0, ``max_format_rows`` 10000.

    Example:
        >>> ladder_options("allegro-2").offset_along
        0.0
    """
    return RenderOptions(
        model=model,
        policy=FallbackPolicy.STRICT,
        offset_along=0.0,
        max_format_rows=LADDER_FORMAT_ROWS,
    )


def _context(options: RenderOptions, *, height: int) -> RenderContext:
    """A render context for a dots label of ``height``: the geometry every field is placed by."""
    probe = Label.model_validate(
        {"unit": "dots", "height": height, "elements": [{"type": "text", "x": 0, "y": 0, "text": "0"}]}
    )
    return job_context(probe, options)


def _image_bottom(ctx: RenderContext) -> int:
    """The printed row the encoder puts the bottom of an image standing at ``y`` 0 on."""
    image = Label.model_validate(
        {
            "unit": "dots",
            "elements": [{"type": "image", "x": _IMAGE_X_DOTS, "y": 0, "name": IMAGE_NAME, "bitmap": ["#"]}],
        }
    ).elements[0]
    dots = printed_dots(image, ctx)
    if dots is None:
        raise RuntimeError("an image with a bitmap has printed dots")
    return dots.area.bottom


def _image_placement(row: int, ctx: RenderContext, *, width: int) -> tuple[int, int]:
    """``(y, bottom)``: the ``y`` whose image bottom is the highest printed row at or below ``row``.

    The encoder places an image in its own units (hundredths of an inch on most models, about two
    dots), so not every row can be an image's bottom; the caller pads blank rows up to ``row``.
    """

    def bottom(y: int) -> int:
        image = Label.model_validate(
            {
                "unit": "dots",
                "elements": [
                    {"type": "image", "x": _IMAGE_X_DOTS, "y": y, "name": IMAGE_NAME, "bitmap": ["#" * width]}
                ],
            }
        ).elements[0]
        dots = printed_dots(image, ctx)
        if dots is None:
            raise RuntimeError("an image with a bitmap has printed dots")
        return dots.area.bottom

    # The bottom grows with y and never by more than y does: bisect for the highest y whose bottom
    # is still at or below the row.
    if bottom(0) > row:
        raise RuntimeError(f"no image bottom lies at or below row {row}")
    low, high = 0, row
    while low < high:
        middle = (low + high + 1) // 2
        low, high = (middle, high) if bottom(middle) <= row else (low, middle - 1)
    return low, bottom(low)


def _text_below(text: str, x: int, row: int, ctx: RenderContext) -> dict[str, object]:
    """A text field at ``x`` whose rectangle ends ``_TEXT_GAP_DOTS`` below ``row``, as high as that allows."""

    def element(y: int) -> dict[str, object]:
        return {"type": "text", "x": x, "y": y, "text": text, "font": _TEXT_FONT}

    def top(y: int) -> int:
        area = inexact_rectangle(Label.model_validate({"unit": "dots", "elements": [element(y)]}).elements[0], ctx)
        if area is None:
            raise RuntimeError("a text field has a rectangle")
        return area.top

    limit = row - 1 - _TEXT_GAP_DOTS
    if top(0) > limit:
        raise RuntimeError(f"row {row} leaves no room for a number below it")
    # The top grows with y: bisect for the highest y whose rectangle still ends at or below the limit.
    low, high = 0, row
    while low < high:
        middle = (low + high + 1) // 2
        low, high = (middle, high) if top(middle) <= limit else (low, middle - 1)
    return element(low)


def _number(row: int, ctx: RenderContext) -> dict[str, object]:
    """A text field naming ``row``, placed where the encoder centres its rectangle closest to that row."""

    def element(y: int) -> dict[str, object]:
        return {
            "type": "text",
            "x": _IMAGE_X_DOTS + _IMAGE_WIDTH_DOTS + _TEXT_GAP_DOTS,
            "y": y,
            "text": str(row),
            "font": _TEXT_FONT,
        }

    def centre(y: int) -> float:
        area = inexact_rectangle(Label.model_validate({"unit": "dots", "elements": [element(y)]}).elements[0], ctx)
        if area is None:
            raise RuntimeError("a text field has a rectangle")
        return area.bottom + (area.height - 1) / 2

    # The centre grows with y: bisect for the first y whose centre reaches the row, then keep it or
    # the y below, whichever centres closer. A dots label is not scaled, so the y lies near the row.
    low, high = 0, 2 * row + _COARSE_STEP
    while low < high:
        middle = (low + high) // 2
        low, high = (middle + 1, high) if centre(middle) < row else (low, middle)
    candidates = [y for y in (low - 1, low) if y >= 0]
    return element(min(candidates, key=lambda y: abs(centre(y) - row)))


def _tick_rows(first: int, last: int) -> dict[int, int]:
    """Each tick's printed row and its width in dots: fine ticks first to last, coarse ones below."""
    ticks = dict.fromkeys(range(first, last + 1, _TICK_STEP), _SHORT_TICK_DOTS)
    ticks.update(dict.fromkeys(range(first, last + 1, _LONG_TICK_STEP), _IMAGE_WIDTH_DOTS))
    ticks.update(dict.fromkeys(range(_COARSE_STEP, first, _COARSE_STEP), _IMAGE_WIDTH_DOTS))
    return ticks


def _bitmap(top: int, bottom: int, widths: dict[int, int]) -> list[str]:
    """The ladder image, top row first: the spine on every row, a tick where ``widths`` has one."""
    rows: list[str] = []
    for row in range(top, bottom - 1, -1):
        ink = max(_SPINE_DOTS, widths.get(row, 0))
        rows.append("#" * ink + "." * (_IMAGE_WIDTH_DOTS - ink))
    return rows


def _label_height(top: int, options: RenderOptions) -> int:
    """A label length in dots whose continuous length, as the job sends it, ends ``_LABEL_MARGIN_ROWS`` past ``top``."""
    height = top + _LABEL_MARGIN_ROWS
    while True:
        ctx = _context(options, height=height)
        if ctx.printed_dots(ctx.inch_hundredths(height, axis=Axis.ALONG)) >= top + _LABEL_MARGIN_ROWS:
            return height
        height += _LABEL_MARGIN_ROWS


def build_ladder(options: RenderOptions, *, first: int = DEFAULT_FIRST, last: int = DEFAULT_LAST) -> Ladder:
    """Build the ladder label for ``options``, its rows read from the encoder.

    Example:
        >>> ladder = build_ladder(ladder_options("allegro-2"))
        >>> ladder.bottom, ladder.top, ladder.ticks[1760]
        (0, 1770, 10)
    """
    ctx = _context(options, height=last)
    bottom = _image_bottom(ctx)
    top = last + _HEADROOM_ROWS
    widths = _tick_rows(first, last)
    numbers = (*range(_COARSE_STEP, first, _COARSE_STEP), *range(first, last + 1, _NUMBER_STEP))
    height = _label_height(top, options)
    ctx = _context(options, height=height)
    image = {"type": "image", "x": _IMAGE_X_DOTS, "y": 0, "name": IMAGE_NAME, "bitmap": _bitmap(top, bottom, widths)}
    document: dict[str, object] = {
        "unit": "dots",
        "width": _IMAGE_X_DOTS + _IMAGE_WIDTH_DOTS + _TEXT_GAP_DOTS + _TEXT_WIDTH_DOTS + _IMAGE_X_DOTS,
        "height": height,
        "media": "continuous",
        "elements": [image, *(_number(row, ctx) for row in numbers)],
    }
    ticks = {row: top - row for row in sorted(widths)}
    return Ladder(document=document, bottom=bottom, top=top, ticks=ticks, numbers=numbers)


@dataclass(frozen=True)
class _StepShape:
    """The step label's lengths in dots: the reference line, one step, and the whole image row."""

    line: int
    step: int
    width: int


def _step_bitmap(reference: int, starts: dict[int, int], *, bottom: int, shape: _StepShape) -> list[str]:
    """The step image, top row first: a step per row above the reference line, blank rows below it to ``bottom``."""
    rows: list[str] = []
    for row in range(reference + len(starts), bottom - 1, -1):
        if row == reference:
            left, length = 0, shape.line
        elif row in starts:
            left, length = starts[row], shape.step
        else:
            left, length = 0, 0
        rows.append("." * left + "#" * length + "." * (shape.width - left - length))
    return rows


def build_steps(options: RenderOptions, *, reference: int) -> Steps:
    """Build the step label for ``options``: a reference line on ``reference``, a step on each row above it.

    Each step is one dot row, 5 mm long, 2 mm after the one before, and has a number under it: its
    row less ``reference`` rounded down to ten, so the steps above 1645 read 6 to 10. The numbers
    and the reference row's own number stand below the image, so they print even where the format
    cuts a step off, and the last step that printed names the format's last row.

    Example:
        >>> steps = build_steps(ladder_options("allegro-2"), reference=1645)
        >>> steps.reference, steps.top, steps.steps[6], steps.steps[10]
        (1645, 1650, 1646, 1650)
    """
    top = reference + STEP_COUNT
    height = _label_height(top, options)
    ctx = _context(options, height=height)

    def dots(mm: float) -> int:
        return round(mm * ctx.profile.dpi / _MM_PER_INCH)

    step, pitch = dots(_STEP_MM), dots(_STEP_MM + _STEP_GAP_MM)
    starts = {reference + k: dots(_FIRST_STEP_MM) + (k - 1) * pitch for k in range(1, STEP_COUNT + 1)}
    width = starts[top] + step
    base = reference - reference % 10
    y, bottom = _image_placement(reference, ctx, width=width)
    shape = _StepShape(line=dots(_REFERENCE_MM), step=step, width=width)
    bitmap = _step_bitmap(reference, starts, bottom=bottom, shape=shape)
    image = {"type": "image", "x": _IMAGE_X_DOTS, "y": y, "name": IMAGE_NAME, "bitmap": bitmap}
    numbers = [_text_below(str(row - base), _IMAGE_X_DOTS + left, bottom, ctx) for row, left in starts.items()]
    document: dict[str, object] = {
        "unit": "dots",
        "width": _IMAGE_X_DOTS + width + _IMAGE_X_DOTS,
        "height": height,
        "media": "continuous",
        "elements": [image, _text_below(str(reference), _IMAGE_X_DOTS, bottom, ctx), *numbers],
    }
    return Steps(
        document=document, reference=reference, bottom=bottom, top=top, steps={row - base: row for row in starts}
    )


def _command(args: argparse.Namespace, path: Path) -> list[str]:
    """The print command that sends the ladder with the settings it was built for."""
    return [
        "datamax-dpl",
        "--set",
        f"printer.max_format_rows={LADDER_FORMAT_ROWS}",
        "--set",
        "printer.offset_along=0",
        "print",
        str(path),
        "--model",
        args.model,
        "--policy",
        "strict",
        "--printer",
        args.printer,
    ]


def _shell_line(argv: list[str]) -> str:
    """``argv`` as one line the platform's shell reads back into the same words."""
    return subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write a label of ticks that shows where a format is cut off.")
    parser.add_argument("--out", type=Path, required=True, help="folder the label JSON is written to")
    parser.add_argument("--model", default="allegro-2", help="model profile (default: allegro-2)")
    parser.add_argument("--first", type=int, help=f"first fine tick row, a multiple of 20 (default {DEFAULT_FIRST})")
    parser.add_argument("--last", type=int, help=f"last fine tick row, a multiple of 5 (default {DEFAULT_LAST})")
    parser.add_argument(
        "--steps",
        type=int,
        metavar="ROW",
        help=f"write the step label instead: a reference line on ROW, a step on each of the {STEP_COUNT} rows above",
    )
    parser.add_argument("--printer", required=True, help="printer URI for the printed command")
    return parser.parse_args(argv)


def _grid_error(first: int, last: int) -> str | None:
    """Why ``first`` and ``last`` do not fit the tick grid, or None when they do."""
    if first <= 0 or first % _NUMBER_STEP:
        return f"--first must be a positive multiple of {_NUMBER_STEP}, got {first}"
    if last < first or last % _TICK_STEP:
        return f"--last must be a multiple of {_TICK_STEP} not below --first, got {last}"
    return None


def _steps_error(args: argparse.Namespace) -> str | None:
    """Why a ``--steps`` request cannot be built, or None when it can."""
    if args.first is not None or args.last is not None:
        return "--steps builds the step label, which takes no --first or --last"
    if args.steps <= 0:
        return f"--steps must be a positive row, got {args.steps}"
    return None


def _clean_job(document: dict[str, object], options: RenderOptions) -> tuple[RenderResult | None, list[str]]:
    """The strict render of ``document``, or None with the codes that keep it from printing as built."""
    result = build_job(Label.model_validate(document), options)
    unclean = [f.code.value for f in result.findings if f.severity is not Severity.INFO]
    if unclean or result.halved_elements:
        return None, unclean
    return result, []


def _write(args: argparse.Namespace, name: str, document: dict[str, object], details: dict[str, object]) -> int:
    """Write ``document`` if its strict render is clean, then print the summary line and the command."""
    result, unclean = _clean_job(document, ladder_options(args.model))
    if result is None:
        sys.stderr.write(f"error: a strict render of the label is not clean: {unclean}\n")
        return NOT_CLEAN
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / name
    path.write_text(json.dumps(document), encoding="utf-8")
    command = _command(args, path)
    summary = {
        "file": str(path),
        **details,
        "image_bytes": result.image_bytes,
        "findings": [finding.code.value for finding in result.findings],
        "argv": command,
    }
    sys.stdout.write(json.dumps(summary) + "\n")
    sys.stdout.write(_shell_line(command) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Write the ladder (or with ``--steps`` the step label), print a JSON line about it and its print command.

    Returns:
        0 when written, 2 for a tick range off the grid or a bad ``--steps`` request, 3 when a
        strict render reports an error or a warning (the label would not print as numbered).
    """
    args = _parse(argv)
    if args.steps is not None:
        problem = _steps_error(args)
        if problem is not None:
            sys.stderr.write(f"error: {problem}\n")
            return BAD_ARGUMENTS
        steps = build_steps(ladder_options(args.model), reference=args.steps)
        details: dict[str, object] = {
            "image_rows": [steps.bottom, steps.top],
            "reference": steps.reference,
            "steps": {str(number): row for number, row in steps.steps.items()},
        }
        return _write(args, "length-steps.json", steps.document, details)
    first = DEFAULT_FIRST if args.first is None else args.first
    last = DEFAULT_LAST if args.last is None else args.last
    problem = _grid_error(first, last)
    if problem is not None:
        sys.stderr.write(f"error: {problem}\n")
        return BAD_ARGUMENTS
    ladder = build_ladder(ladder_options(args.model), first=first, last=last)
    return _write(
        args, "length-ladder.json", ladder.document, {"image_rows": [ladder.bottom, ladder.top], "ticks": [first, last]}
    )


if __name__ == "__main__":
    raise SystemExit(main())
