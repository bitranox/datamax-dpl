"""Write a QR Code label and a CSV of unique codes, to check a batch prints each record's own code.

The label holds one ``qr`` element whose data is ``{code}`` and a text ``{n}`` beside it;
``rows.csv`` holds ``--count`` records, ``n`` running 01, 02, ... and ``code`` being
``DMX-130-<n>-<six random capitals or digits>``. Printed with ``--data``, the printer gets one
label per record; scanning each label must give the code listed beside its number, and a label
that scans as another record's code (or a repeat of the one before) shows a stale image.

The codes use only capitals, digits and ``-``, so each fits a version 1 symbol at level M. The
random part follows from ``--seed`` (a fresh one when not given, printed in the JSON line), so a
run can be repeated exactly; it only tells the labels apart and is not a secret.

Usage::

    .venv/bin/python scripts/qr_batch.py --out qr --count 20
    datamax-dpl print qr/qr-batch.json --data qr/rows.csv --model allegro-2 --printer tcp://192.0.2.60:9100

The first output line is JSON (the label and CSV paths, the seed, the codes in order and the
command as a list); then one line ``<n> <code>`` per record, the list to compare the scans with; then the
command that prints the batch.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import secrets
import shlex
import string
import subprocess
import sys
from pathlib import Path

__all__ = ["BAD_ARGUMENTS", "CODE_PREFIX", "MAX_COUNT", "batch_codes", "batch_label", "main"]

#: What every code starts with, before its number and random part.
CODE_PREFIX = "DMX-130"
#: Largest ``--count``: the number is two digits.
MAX_COUNT = 99
#: Exit code for a ``--count`` outside 1 to 99.
BAD_ARGUMENTS = 2

_RANDOM_LENGTH = 6
_RANDOM_ALPHABET = string.ascii_uppercase + string.digits
_DEFAULT_PRINTER = "tcp://192.0.2.60:9100"
_FRESH_SEEDS = 2**32


def batch_codes(count: int, *, seed: int) -> list[tuple[str, str]]:
    """Return ``count`` records ``(n, code)``, n from 01, every code different.

    The random part is drawn from a hash of ``seed`` and the number, so one seed always gives the
    same codes; the number in the code keeps them apart whatever the hash gives.

    Example:
        >>> [code[:11] for _, code in batch_codes(2, seed=1)]
        ['DMX-130-01-', 'DMX-130-02-']
        >>> batch_codes(3, seed=7) == batch_codes(3, seed=7)
        True
    """
    records: list[tuple[str, str]] = []
    for number in range(1, count + 1):
        digest = hashlib.sha256(f"{seed}:{number}".encode("ascii")).digest()
        tail = "".join(_RANDOM_ALPHABET[byte % len(_RANDOM_ALPHABET)] for byte in digest[:_RANDOM_LENGTH])
        records.append((f"{number:02d}", f"{CODE_PREFIX}-{number:02d}-{tail}"))
    return records


def batch_label() -> dict[str, object]:
    """Return the label document: a QR Code of ``{code}`` and the record number ``{n}`` beside it.

    The QR Code stands 2 mm in from the bottom left, clear of the label edge the Allegro 2's
    default along offset would otherwise clamp it to.

    Example:
        >>> [element["type"] for element in batch_label()["elements"]]
        ['qr', 'text']
    """
    return {
        "unit": "mm",
        "width": 40,
        "height": 20,
        "elements": [
            {"type": "qr", "x": 2, "y": 2, "data": "{code}", "error": "M", "module_dots": 4},
            {"type": "text", "x": 22, "y": 8, "text": "{n}", "font": 2, "width_multiplier": 2, "height_multiplier": 2},
        ],
    }


def _write_rows(path: Path, records: list[tuple[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["n", "code"])
        writer.writerows(records)


def _shell_line(argv: list[str]) -> str:
    """``argv`` as one line the platform's shell reads back into the same words."""
    return subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write a QR Code label and a CSV of unique codes for it.")
    parser.add_argument("--out", type=Path, required=True, help="folder the label and rows.csv are written to")
    parser.add_argument("--count", type=int, default=20, help="number of records, 1 to 99 (default: 20)")
    parser.add_argument("--seed", type=int, default=None, help="seed of the random part (default: a fresh one)")
    parser.add_argument("--model", default="allegro-2", help="model for the printed command (default: allegro-2)")
    parser.add_argument("--printer", default=_DEFAULT_PRINTER, help="printer URI for the printed command")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Write the label and ``rows.csv``, then print the expected codes and the print command.

    Returns:
        0 when written, 2 for a ``--count`` outside 1 to 99.
    """
    args = _parse(argv)
    if not 1 <= args.count <= MAX_COUNT:
        sys.stderr.write(f"error: --count must be 1 to {MAX_COUNT}, got {args.count}\n")
        return BAD_ARGUMENTS
    seed = secrets.randbelow(_FRESH_SEEDS) if args.seed is None else args.seed
    records = batch_codes(args.count, seed=seed)
    args.out.mkdir(parents=True, exist_ok=True)
    label_path, rows_path = args.out / "qr-batch.json", args.out / "rows.csv"
    label_path.write_text(json.dumps(batch_label()), encoding="utf-8")
    _write_rows(rows_path, records)
    command = ["datamax-dpl", "print", str(label_path), "--data", str(rows_path), "--model", args.model]
    command += ["--printer", args.printer]
    summary = {
        "label": str(label_path),
        "rows": str(rows_path),
        "seed": seed,
        "codes": [code for _, code in records],
        "argv": command,
    }
    sys.stdout.write(json.dumps(summary) + "\n")
    sys.stdout.writelines(f"{number} {code}\n" for number, code in records)
    sys.stdout.write(_shell_line(command) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
