"""Write a label that measures, on paper, how many dot rows one format of a printer prints.

The label holds one downloaded image, 24 dots wide, standing on the bottom edge of the label: a
2-dot spine from the bottom to the top, and a 1-row tick on every 5th printed dot row from
``--first`` to ``--last`` (default 1600 to 1760; every 10th tick is long). A number every 20 rows
beside the image names the row of the tick it stands next to, and below ``--first`` a long tick
with its number every 100 rows gives a coarse scale in case the format ends lower than expected.

Every number is the printed dot row its tick sits on, counted the way the ``format_past_max_length``
check counts (the row the encoder commands, scale and offset included): image rows count one
printed row each, and the image's bottom row is placed by the encoder, so the rows here are read
from the encoder, never assumed. A printer that cuts the format off prints the ticks up to its
limit; the number of the last tick fully printed (the next number down plus 5 per tick above it,
when its own number was cut) is the value for ``max_format_rows`` (accurate to one tick, 5 rows).

The label is built with ``max_format_rows`` 10000, so the library itself does not refuse it, and
the printed command line sets the same, plus the scale and offset the rows were computed with:
a configured ``scale_along`` would move the numbers (text is scaled, image rows are not) and an
``offset_along`` would move the image. ``--policy strict`` makes the printer command refuse
rather than print a ladder whose image was halved or whose fields were moved. Pass the printer as
a URI: a named printer whose section sets its own scale, offset or max_format_rows wins over
``--set printer.*``.

Usage::

    .venv/bin/python scripts/length_ladder.py --out length
    .venv/bin/python scripts/length_ladder.py --out length --first 1400 --last 1600
    .venv/bin/python scripts/length_ladder.py --out length --printer tcp://192.0.2.60:9100

The first output line is JSON (the label file, the printed rows the image spans, the tick range,
its image bytes, the finding codes of a strict render and the command as a list); the second is
the command to run, for example::

    datamax-dpl --set printer.max_format_rows=10000 --set printer.scale_along=0.976 \\
        --set printer.offset_along=0 print length/length-ladder.json --model allegro-2 \\
        --policy strict --printer tcp://192.0.2.60:9100
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
from datamax_dpl.domain.render import build_job, effective_calibration, job_context
from datamax_dpl.domain.render_context import RenderContext, RenderOptions
from datamax_dpl.domain.units import Axis

__all__ = [
    "BAD_ARGUMENTS",
    "DEFAULT_FIRST",
    "DEFAULT_LAST",
    "IMAGE_NAME",
    "LADDER_FORMAT_ROWS",
    "NOT_CLEAN",
    "Ladder",
    "build_ladder",
    "ladder_options",
    "main",
]

#: First and last printed row with a fine tick: around the Allegro 2's provisional 1654 rows.
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
_DEFAULT_PRINTER = "tcp://192.0.2.60:9100"


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


def ladder_options(model: str, *, scale_along: float | None = None) -> RenderOptions:
    """Return the options the ladder is built for: strict, offset 0, ``max_format_rows`` 10000.

    The scale is the model's measured one unless given, pinned so the printed command can repeat it.

    Example:
        >>> ladder_options("allegro-2").scale_along
        0.976
    """
    scale = effective_calibration(RenderOptions(model=model)).scale_along if scale_along is None else scale_along
    return RenderOptions(
        model=model,
        policy=FallbackPolicy.STRICT,
        scale_along=scale,
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
    # the y below, whichever centres closer. The bound holds down to the smallest scale (0.5).
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
    """A label length in dots that prints at least ``_LABEL_MARGIN_ROWS`` past ``top`` at this scale."""
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


def _command(args: argparse.Namespace, options: RenderOptions, path: Path) -> list[str]:
    """The print command that sends the ladder with the settings it was built for."""
    return [
        "datamax-dpl",
        "--set",
        f"printer.max_format_rows={LADDER_FORMAT_ROWS}",
        "--set",
        f"printer.scale_along={options.scale_along:g}",
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
    parser.add_argument("--first", type=int, default=DEFAULT_FIRST, help="first fine tick row, a multiple of 20")
    parser.add_argument("--last", type=int, default=DEFAULT_LAST, help="last fine tick row, a multiple of 5")
    parser.add_argument("--scale-along", type=float, default=None, help="scale_along (default: the model's)")
    parser.add_argument("--printer", default=_DEFAULT_PRINTER, help="printer URI for the printed command")
    return parser.parse_args(argv)


def _grid_error(first: int, last: int) -> str | None:
    """Why ``first`` and ``last`` do not fit the tick grid, or None when they do."""
    if first <= 0 or first % _NUMBER_STEP:
        return f"--first must be a positive multiple of {_NUMBER_STEP}, got {first}"
    if last < first or last % _TICK_STEP:
        return f"--last must be a multiple of {_TICK_STEP} not below --first, got {last}"
    return None


def main(argv: list[str] | None = None) -> int:
    """Write the ladder label, print a JSON line about it and the command that prints it.

    Returns:
        0 when written, 2 for a tick range off the grid, 3 when a strict render reports an error or
        a warning (the ladder would not print as numbered).
    """
    args = _parse(argv)
    problem = _grid_error(args.first, args.last)
    if problem is not None:
        sys.stderr.write(f"error: {problem}\n")
        return BAD_ARGUMENTS
    options = ladder_options(args.model, scale_along=args.scale_along)
    ladder = build_ladder(options, first=args.first, last=args.last)
    result = build_job(Label.model_validate(ladder.document), options)
    unclean = [f.code.value for f in result.findings if f.severity is not Severity.INFO]
    if unclean or result.halved_elements:
        sys.stderr.write(f"error: a strict render of the ladder is not clean: {unclean}\n")
        return NOT_CLEAN
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "length-ladder.json"
    path.write_text(json.dumps(ladder.document), encoding="utf-8")
    command = _command(args, options, path)
    summary = {
        "file": str(path),
        "image_rows": [ladder.bottom, ladder.top],
        "ticks": [args.first, args.last],
        "image_bytes": result.image_bytes,
        "findings": [finding.code.value for finding in result.findings],
        "argv": command,
    }
    sys.stdout.write(json.dumps(summary) + "\n")
    sys.stdout.write(_shell_line(command) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
