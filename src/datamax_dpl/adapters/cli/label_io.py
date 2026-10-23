"""Read label specifications and data records from files or stdin.

A label is a JSON document matching ``datamax-dpl schema``. Data records come from CSV (header row
gives the field names), a JSON array of objects, or JSON lines. Input is bounded in size and
every value is read as text, because records only ever fill ``{name}`` placeholders.
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path
from typing import cast

import orjson
from pydantic import ValidationError

from ...domain.label import Label

#: Largest label or data file accepted, so a wrong path (a disk image) fails fast.
MAX_INPUT_BYTES = 16 * 1024 * 1024
STDIN = "-"


class LabelInputError(ValueError):
    """A label or data file could not be read or does not describe a valid label."""


def read_input(source: str) -> bytes:
    """Read ``source`` (a path, or ``-`` for stdin), refusing more than :data:`MAX_INPUT_BYTES`.

    Raises:
        LabelInputError: The file cannot be read or is too large.
    """
    try:
        if source == STDIN:
            data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        else:
            with Path(source).open("rb") as handle:
                data = handle.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise LabelInputError(f"cannot read {source}: {exc.strerror or exc}") from None
    if len(data) > MAX_INPUT_BYTES:
        raise LabelInputError(f"{source} is larger than {MAX_INPUT_BYTES} bytes")
    return data


def validation_lines(exc: ValidationError) -> str:
    """One ``field: reason`` per problem, without the library's internals.

    Example:
        >>> try:
        ...     Label.model_validate({"elements": []})
        ... except ValidationError as exc:
        ...     print(validation_lines(exc))
        elements: List should have at least 1 item after validation, not 0
    """
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'label'}: {e['msg']}" for e in exc.errors())


def parse_label(data: bytes, *, source: str = "label") -> Label:
    """Parse a JSON label document.

    Raises:
        LabelInputError: Not JSON, or not a valid label.
    """
    try:
        document = orjson.loads(data)
    except orjson.JSONDecodeError as exc:
        raise LabelInputError(f"{source} is not valid JSON: {exc}") from None
    try:
        return Label.model_validate(document)
    except ValidationError as exc:
        raise LabelInputError(f"{source} is not a valid label: {validation_lines(exc)}") from None


def load_label(source: str) -> Label:
    """Read and parse the label at ``source`` (path or ``-``)."""
    return parse_label(read_input(source), source=source)


def _records_from_json(data: bytes, source: str) -> list[dict[str, str]]:
    try:
        document = orjson.loads(data)
    except orjson.JSONDecodeError:
        return _records_from_json_lines(data, source)
    parsed: object = document
    rows: list[object] = cast("list[object]", parsed) if isinstance(parsed, list) else [parsed]
    return [_as_record(row, source=source, number=i + 1) for i, row in enumerate(rows)]


def _records_from_json_lines(data: bytes, source: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for number, raw in enumerate(data.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            parsed: object = orjson.loads(raw)
            records.append(_as_record(parsed, source=source, number=number))
        except orjson.JSONDecodeError as exc:
            raise LabelInputError(f"{source} line {number} is not JSON: {exc}") from None
    return records


def _as_record(row: object, *, source: str, number: int) -> dict[str, str]:
    if not isinstance(row, dict):
        raise LabelInputError(f"{source} record {number} is not an object")
    mapping = cast("dict[object, object]", row)
    return {str(key): "" if value is None else str(value) for key, value in mapping.items()}


def _records_from_csv(data: bytes, source: str) -> list[dict[str, str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LabelInputError(f"{source} is not UTF-8: {exc.reason}") from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    return [{key: value or "" for key, value in row.items() if key is not None} for row in reader]


def load_records(source: str) -> list[dict[str, str]]:
    """Read data records: CSV for ``*.csv``, else JSON array, JSON object or JSON lines.

    Raises:
        LabelInputError: The file cannot be read or parsed, or holds no record.
    """
    data = read_input(source)
    records = _records_from_csv(data, source) if source.lower().endswith(".csv") else _records_from_json(data, source)
    if not records:
        raise LabelInputError(f"{source} holds no data record")
    return records


__all__ = [
    "MAX_INPUT_BYTES",
    "STDIN",
    "LabelInputError",
    "load_label",
    "load_records",
    "parse_label",
    "read_input",
    "validation_lines",
]
