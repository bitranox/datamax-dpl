"""Generate the model tables of ``docs/capabilities.md`` from the printer profiles.

The tables between the two marker lines of ``docs/capabilities.md`` are produced from
:mod:`datamax_dpl.domain.profiles`, so the document cannot drift from the code: the test
``tests/test_capabilities_doc.py`` regenerates them and compares. The prose outside the markers is
written by hand and left alone.

Usage::

    .venv/bin/python scripts/gen_capabilities.py           # print the generated section
    .venv/bin/python scripts/gen_capabilities.py --write   # replace it in docs/capabilities.md
    .venv/bin/python scripts/gen_capabilities.py --check   # exit 1 when the document is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from datamax_dpl.domain.enums import SPEED_IPS
from datamax_dpl.domain.profiles import Feature, PrinterProfile, SpeedRange, list_profiles

__all__ = [
    "BEGIN_MARKER",
    "CANNOT_RUN",
    "DEFAULT_DOC",
    "END_MARKER",
    "STALE",
    "extract_generated",
    "generated_section",
    "main",
    "replace_generated",
]

BEGIN_MARKER = "<!-- BEGIN GENERATED: scripts/gen_capabilities.py -->"
END_MARKER = "<!-- END GENERATED: scripts/gen_capabilities.py -->"
DEFAULT_DOC = Path(__file__).resolve().parents[1] / "docs" / "capabilities.md"

#: Exit code of ``--check`` when the document differs from the profiles.
STALE = 1
#: Exit code when the document cannot be read or lacks its markers.
CANNOT_RUN = 2

# The feature matrix is split in two so that neither table is wider than a screen.
_JOB_FEATURES = (
    Feature.CUT_BY,
    Feature.CUT_NOW,
    Feature.CUTTER_PULSE,
    Feature.STOP_POSITION,
    Feature.PRESENT_DISTANCE,
    Feature.PAUSE_EACH_LABEL,
    Feature.SENSOR_SELECT,
    Feature.BACKUP_SPEED,
    Feature.STORED_FORMATS,
    Feature.TIME_DATE,
)
_FORMAT_FEATURES = (
    Feature.FORMAT_METRIC,
    Feature.ATTRIBUTE,
    Feature.MIRROR,
    Feature.FIELD_TERMINATOR,
    Feature.CIRCLE_POLYGON,
    Feature.SCALABLE_FONTS,
    Feature.EXTENDED_BARCODES,
    Feature.PDF417,
    Feature.DATAMATRIX,
    Feature.DEFAULT_BAR_WIDTHS,
)


#: Narrowest column the Markdown delimiter row allows (``---``).
_MIN_COLUMN = 3


def _row(cells: list[str], widths: list[int]) -> str:
    return "| " + " | ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True)) + " |"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    """Render a table already padded the way the repository's Markdown formatter aligns it.

    Emitting the aligned form keeps the committed file stable: a formatter that realigns the
    document after an edit leaves these tables byte for byte as generated, so the doc test does
    not fail on whitespace.
    """
    widths = [max(_MIN_COLUMN, *(len(row[i]) for row in [header, *rows])) for i in range(len(header))]
    delimiter = "|" + "|".join("-" * (width + 2) for width in widths) + "|"
    return [_row(header, widths), delimiter, *(_row(row, widths) for row in rows)]


def _number(value: float) -> str:
    """Print 203.0 as 203 and 289.56 as 289.56."""
    return f"{value:g}"


def _speed(speed: SpeedRange | None) -> str:
    if speed is None:
        return "none"
    low, high, default = speed.low, speed.high, speed.default
    if low is high:
        return f"{low.value} ({_number(SPEED_IPS[low])} ips), default {default.value}"
    return (
        f"{low.value}-{high.value} ({_number(SPEED_IPS[low])}-{_number(SPEED_IPS[high])} ips), default {default.value}"
    )


def _points(points: tuple[int, ...]) -> str:
    return ", ".join(str(p) for p in points) if points else "none"


def _limits_table(profiles: list[PrinterProfile]) -> list[str]:
    header = ["Model", "Name", "dpi", "Print width (in)", "Max column (in/100)", "Max fields", "Chars/field", "Heat"]
    rows = [
        [
            f"`{p.id}`",
            p.name,
            _number(p.dpi),
            _number(p.print_width_in),
            str(p.max_column),
            str(p.max_fields),
            str(p.max_field_chars),
            f"0-{p.heat_max}",
        ]
        for p in profiles
    ]
    return _table(header, rows)


def _speed_table(profiles: list[PrinterProfile]) -> list[str]:
    header = ["Model", "Print speed", "Slew (feed) speed", "Backup speed"]
    rows = [[f"`{p.id}`", _speed(p.print_speed), _speed(p.slew_speed), _speed(p.backup_speed)] for p in profiles]
    return _table(header, rows)


def _font_table(profiles: list[PrinterProfile]) -> list[str]:
    header = ["Model", "`Annn` sizes (pt)", "Old `000`-`010` sizes (pt)"]
    rows = [[f"`{p.id}`", _points(p.a_series_points), _points(p.old_series_points)] for p in profiles]
    return _table(header, rows)


def _feature_table(profiles: list[PrinterProfile], features: tuple[Feature, ...]) -> list[str]:
    header = ["Model", *(f"`{feature.value}`" for feature in features)]
    rows = [[f"`{p.id}`", *(p.support(feature).describe() for feature in features)] for p in profiles]
    return _table(header, rows)


def _calibration_table(profiles: list[PrinterProfile]) -> list[str]:
    """Only models that ship measured values: every other model prints nominal lengths."""
    header = [
        "Model",
        "`scale_across`",
        "`scale_along`",
        "`offset_across` (mm)",
        "`offset_along` (mm)",
        "Stop (mm)",
        "Source",
    ]
    rows = [
        [
            f"`{p.id}`",
            _number(c.scale_across),
            _number(c.scale_along),
            _number(c.offset_across),
            _number(c.offset_along),
            "printer setting" if c.stop_position_mm is None else _number(c.stop_position_mm),
            c.provenance,
        ]
        for p in profiles
        if (c := p.calibration).provenance
    ]
    return _table(header, rows)


def _image_memory_table(profiles: list[PrinterProfile]) -> list[str]:
    """Only models that ship an image memory budget: every other model has none."""
    header = ["Model", "Image memory budget (bytes)", "Source"]
    rows = [
        [f"`{p.id}`", str(c.image_memory_bytes), c.image_memory_provenance]
        for p in profiles
        if (c := p.calibration).image_memory_bytes is not None
    ]
    return _table(header, rows)


def generated_section(profiles: list[PrinterProfile] | None = None) -> str:
    """Return the Markdown that belongs between the markers, built from ``profiles``.

    Args:
        profiles: The profiles to describe; every profile when omitted.

    Returns:
        The tables, separated by blank lines, ending with a newline.
    """
    chosen = profiles if profiles is not None else list_profiles()
    parts = [
        ["### Limits", "", *_limits_table(chosen)],
        ["### Speeds", "", *_speed_table(chosen)],
        ["### Font 9 point sizes", "", *_font_table(chosen)],
        ["### Features: job, media and printer settings", "", *_feature_table(chosen, _JOB_FEATURES)],
        ["### Features: format, graphics and barcodes", "", *_feature_table(chosen, _FORMAT_FEATURES)],
        ["### Calibration defaults", "", *_calibration_table(chosen)],
        ["### Image memory budget", "", *_image_memory_table(chosen)],
    ]
    return "\n\n".join("\n".join(part) for part in parts) + "\n"


def _bounds(document: str) -> tuple[int, int]:
    """Return where the generated text starts and ends inside ``document``.

    Raises:
        ValueError: A marker is missing, or they are out of order.
    """
    begin = document.find(BEGIN_MARKER)
    end = document.find(END_MARKER)
    if begin < 0 or end < 0 or end < begin:
        msg = f"the document needs the lines {BEGIN_MARKER!r} and {END_MARKER!r}, in that order"
        raise ValueError(msg)
    return begin + len(BEGIN_MARKER) + 1, end


def extract_generated(document: str) -> str:
    """Return the text between the markers of ``document``.

    Raises:
        ValueError: A marker is missing, or they are out of order.
    """
    start, end = _bounds(document)
    return document[start:end].strip("\n") + "\n"


def replace_generated(document: str, section: str) -> str:
    """Return ``document`` with the text between its markers replaced by ``section``.

    Raises:
        ValueError: A marker is missing, or they are out of order.
    """
    start, end = _bounds(document)
    return f"{document[:start]}\n{section}\n{document[end:]}"


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the model tables of docs/capabilities.md.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="replace the generated section in the document")
    mode.add_argument("--check", action="store_true", help="exit 1 when the document's section is stale")
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC, help="the document (default: docs/capabilities.md)")
    return parser.parse_args(argv)


def _check_or_write(doc: Path, section: str, *, write: bool) -> int:
    document = doc.read_text(encoding="utf-8")
    if write:
        doc.write_text(replace_generated(document, section), encoding="utf-8", newline="\n")
        return 0
    if extract_generated(document) == section:
        return 0
    sys.stderr.write(f"{doc} is stale; run scripts/gen_capabilities.py --write\n")
    return STALE


def main(argv: list[str] | None = None) -> int:
    """Print, write or check the generated section.

    Returns:
        0 when done (or the document is current), 1 when ``--check`` finds it stale, 2 when the
        document cannot be read or has no markers.
    """
    args = _parse(argv)
    section = generated_section()
    if not (args.write or args.check):
        sys.stdout.write(section)
        return 0
    try:
        return _check_or_write(args.doc, section, write=args.write)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return CANNOT_RUN


if __name__ == "__main__":
    raise SystemExit(main())
