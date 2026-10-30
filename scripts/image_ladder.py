"""Write ladder labels that test the image memory budget on paper.

Each label holds one image whose dot data is a share of the budget: well within it, just under it,
just past it, well past it (both halved), and past what one halving brings under it (refused).
Halving quarters the dot data, so a share up to 4 prints halved and a share above 4 is refused.
The image is a grid of one-dot lines inside a one-dot frame, so a print shows whether a halved
line survives, and a text line names the step. A halved image prints at double magnification, so
every label prints the same size whether it was halved or not.

The image is ``--width`` dots wide (default 800, about 100 mm at 203 dpi), so its height follows
from its share: at the Allegro 2's 44535 bytes, the 1.0 step is about 445 rows (56 mm), longer
than many labels; print the ladder on continuous media or labels long enough.

Usage::

    .venv/bin/python scripts/image_ladder.py --out ladder            # the allegro-2 budget
    .venv/bin/python scripts/image_ladder.py --out ladder --budget 20000
    datamax-dpl print ladder/ladder-03-1.05x.json --printer tcp://192.0.2.60:9100

Each line of the output names a label, its image bytes and what a render does with it
(``within``, ``halved`` or ``refused``), rendered for ``--model`` at the same budget;
``past_print_width`` when the image is wider than that model prints (pass a smaller ``--width``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from datamax_dpl.domain.enums import FindingCode
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.render import build_job, effective_calibration
from datamax_dpl.domain.render_context import RenderOptions

__all__ = ["DEFAULT_WIDTH_DOTS", "NO_BUDGET", "STEPS", "grid_rows", "ladder_label", "main"]

#: Shares of the budget the ladder's images hold: within, just under, just past, well past (both
#: halved), and past what one halving brings under the budget (refused).
STEPS = (0.5, 0.95, 1.05, 1.5, 4.2)
#: Image width in dots: 100 bytes a row. From x = 16 it ends at dot 816, inside the 832 dots of a
#: 4.1 in, 203 dpi model such as the Allegro 2; a narrower model refuses it (image_past_print_width).
DEFAULT_WIDTH_DOTS = 800
#: Exit code when the model has no budget and none was given.
NO_BUDGET = 2

_BITS_PER_BYTE = 8
_GRID_DOTS = 16
_MARGIN_DOTS = 16
_TEXT_DOTS = 48


def grid_rows(width: int, height: int) -> list[str]:
    """Return a one-dot frame with a one-dot line every 16 dots across and along, top row first.

    Example:
        >>> grid_rows(4, 3)
        ['####', '#..#', '####']
    """
    line = "#" * width
    inner = "".join("#" if x in (0, width - 1) or x % _GRID_DOTS == 0 else "." for x in range(width))
    return [line if y in (0, height - 1) or y % _GRID_DOTS == 0 else inner for y in range(height)]


def ladder_label(share: float, *, budget: int, width: int) -> dict[str, object]:
    """Return the label document of one step: an image of ``share`` times ``budget`` bytes and its name.

    Example:
        >>> label = ladder_label(1.0, budget=1000, width=80)
        >>> len(label["elements"][0]["bitmap"])
        100
    """
    row_bytes = -(-width // _BITS_PER_BYTE)
    height = max(1, round(share * budget / row_bytes))
    image = {
        "type": "image",
        "x": _MARGIN_DOTS,
        "y": _MARGIN_DOTS,
        "name": "LADDER",
        "bitmap": grid_rows(width, height),
    }
    caption = f"LADDER {share:g}X OF {budget} B: {height * row_bytes} B"
    text = {"type": "text", "x": _MARGIN_DOTS, "y": 2 * _MARGIN_DOTS + height, "text": caption, "font": 2}
    return {
        "unit": "dots",
        "width": width + 2 * _MARGIN_DOTS,
        "height": height + 2 * _MARGIN_DOTS + _TEXT_DOTS,
        "elements": [image, text],
    }


def _outcome(document: dict[str, object], options: RenderOptions) -> tuple[int, str]:
    """Render ``document`` and return its image bytes and what the budget did to it.

    An image wider than the model prints is ``past_print_width``, whatever the budget says.
    """
    result = build_job(Label.model_validate(document), options)
    if any(finding.code is FindingCode.IMAGE_PAST_PRINT_WIDTH for finding in result.errors):
        return result.image_bytes, "past_print_width"
    if not result.ok:
        return result.image_bytes, "refused"
    return result.image_bytes, "halved" if result.halved_elements else "within"


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write labels of growing image size around an image memory budget.")
    parser.add_argument("--out", type=Path, required=True, help="folder the label JSON files are written to")
    parser.add_argument("--model", default="allegro-2", help="model profile (default: allegro-2)")
    parser.add_argument("--budget", type=int, default=None, help="budget in bytes (default: the model's)")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH_DOTS, help="image width in dots (default: 800)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Write one label per step and print one JSON line per label.

    Returns:
        0 when written, 2 when the model has no budget and ``--budget`` was not given.
    """
    args = _parse(argv)
    options = RenderOptions(model=args.model, image_memory_bytes=args.budget)
    budget = effective_calibration(options).image_memory_bytes
    if budget is None:
        sys.stderr.write(f"error: model {args.model} has no image memory budget; pass --budget\n")
        return NO_BUDGET
    args.out.mkdir(parents=True, exist_ok=True)
    for number, share in enumerate(STEPS, start=1):
        document = ladder_label(share, budget=budget, width=args.width)
        path = args.out / f"ladder-{number:02d}-{share:g}x.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        image_bytes, outcome = _outcome(document, options)
        line = {"file": str(path), "share": share, "image_bytes": image_bytes, "outcome": outcome}
        sys.stdout.write(json.dumps(line) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
