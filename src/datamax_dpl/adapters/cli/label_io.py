"""Read label specifications and data records from files or stdin.

A label is a JSON document matching ``datamax-dpl schema``. Data records come from CSV (header row
gives the field names), a JSON array of objects, or JSON lines. Input is bounded in size and
every value is read as text, because records only ever fill ``{name}`` placeholders. Records are
parsed one at a time as a command goes through them, so a batch does not hold them.
"""

from __future__ import annotations

import csv
import io
import itertools
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, cast

import orjson
from pydantic import ValidationError

from ...domain.errors import DatamaxDplError
from ...domain.label import Label
from ...domain.printable import printable
from .interrupt import raise_if_interrupted

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Largest label or data file accepted, so a wrong path (a disk image) fails fast.
MAX_INPUT_BYTES = 16 * 1024 * 1024
_READ_PIECE_BYTES = 64 * 1024
STDIN = "-"
_LINE_END = re.compile(rb"\r\n|\r|\n")


class LabelInputError(DatamaxDplError, ValueError):
    """A label or data file could not be read or does not describe a valid label."""


def _read_at_most(stream: BinaryIO, limit: int) -> bytes:
    """Read up to ``limit`` bytes in pieces: ``read(limit)`` allocates all of them even for a small file."""
    pieces: list[bytes] = []
    left = limit
    while left > 0 and (piece := stream.read(min(left, _READ_PIECE_BYTES))):
        pieces.append(piece)
        left -= len(piece)
    return b"".join(pieces)


def read_input(source: str) -> bytes:
    """Read ``source`` (a path, or ``-`` for stdin), refusing more than :data:`MAX_INPUT_BYTES`.

    Raises:
        LabelInputError: The file cannot be read or is too large.
    """
    try:
        if source == STDIN:
            data = _read_at_most(sys.stdin.buffer, MAX_INPUT_BYTES + 1)
        else:
            with Path(source).open("rb") as handle:
                data = _read_at_most(handle, MAX_INPUT_BYTES + 1)
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
    lines = (f"{'.'.join(str(p) for p in e['loc']) or 'label'}: {e['msg']}" for e in exc.errors())
    return printable("; ".join(lines))


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


def _as_record(row: object, *, source: str, number: int) -> dict[str, str]:
    if not isinstance(row, dict):
        raise LabelInputError(f"{source} record {number} is not an object")
    mapping = cast("dict[object, object]", row)
    return {str(key): "" if value is None else str(value) for key, value in mapping.items()}


def _json_lines(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield each line of ``data`` with its number, as ``data.splitlines()`` would list them."""
    start = 0
    number = 0
    for number, ending in enumerate(_LINE_END.finditer(data), start=1):
        yield number, data[start : ending.start()]
        start = ending.end()
    if start < len(data):
        yield number + 1, data[start:]


def _records_from_json_lines(data: bytes, source: str) -> Iterator[dict[str, str]]:
    for number, raw in _json_lines(data):
        if not raw.strip():
            continue
        try:
            parsed: object = orjson.loads(raw)
        except orjson.JSONDecodeError as exc:
            raise LabelInputError(f"{source} line {number} is not JSON: {exc}") from None
        yield _as_record(parsed, source=source, number=number)


def _records_from_csv(data: bytes, source: str) -> Iterator[dict[str, str]]:
    # Decoded as it is read: the whole text at once would hold up to four times the file.
    text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8-sig", newline="")
    # strict: without it a stray quote silently merges every row after it into one field.
    reader = csv.DictReader(text, strict=True)
    try:
        for row in reader:
            yield {key: value or "" for key, value in row.items() if key is not None}
    except UnicodeDecodeError as exc:
        raise LabelInputError(f"{source} is not UTF-8: {exc.reason}") from None
    except csv.Error as exc:
        raise LabelInputError(f"{source} is not valid CSV: line {reader.line_num}: {exc}") from None


class Records:
    """The data records of a file, read one at a time and as often as a command needs them.

    Holds the file's bytes, which :data:`MAX_INPUT_BYTES` bounds, and no record: stdin can be read
    only once, while a batch is gone over once to check it and once to send it. A JSON array has no
    way to be read in parts, so it is parsed once, whole, and held for as long as this object is.
    """

    def __init__(self, data: bytes, source: str, *, limit: int | None = None) -> None:
        """Read ``data``, the content of ``source``, and check every record as far as ``limit``.

        Args:
            data: The file's bytes.
            source: Its name, which also says the format: CSV for ``*.csv``.
            limit: Stop checking CSV or JSON lines once past this many records, so a caller refusing
                more than that does not go through them all. :func:`len` then stops one past it;
                a JSON array, parsed whole by then, counts in full.

        Raises:
            LabelInputError: A record that was checked cannot be parsed.
        """
        self._data = data
        self._source = source
        self._csv = source.lower().endswith(".csv")
        self._rows: list[object] | None = None if self._csv else _json_rows(data)
        checked = iter(self) if limit is None or self._rows is not None else itertools.islice(self, limit + 1)
        self._count = sum(1 for _ in checked)

    def __iter__(self) -> Iterator[dict[str, str]]:
        """Parse the records again from the start, one at a time.

        Raises:
            KeyboardInterrupt: A Ctrl-C came and its raise was lost, as it can be while a record is
                filled (see :mod:`.interrupt`); looked for at each record and at the end.
        """
        for record in self._parsed():
            raise_if_interrupted()
            yield record
        raise_if_interrupted()

    def _parsed(self) -> Iterator[dict[str, str]]:
        if self._csv:
            return _records_from_csv(self._data, self._source)
        if self._rows is None:
            return _records_from_json_lines(self._data, self._source)
        rows = enumerate(self._rows, start=1)
        return (_as_record(row, source=self._source, number=number) for number, row in rows)

    def __len__(self) -> int:
        """How many records were checked: all of them, unless the limit stopped it (see ``__init__``)."""
        return self._count


def _json_rows(data: bytes) -> list[object] | None:
    """The rows of a JSON document, or None when ``data`` is no one document: JSON lines."""
    try:
        document: object = orjson.loads(data)
    except orjson.JSONDecodeError:
        return None
    return cast("list[object]", document) if isinstance(document, list) else [document]


def load_records(source: str, *, limit: int | None = None) -> Records:
    """Read data records: CSV for ``*.csv``, else JSON array, JSON object or JSON lines.

    Args:
        source: The file, or ``-`` for stdin.
        limit: See :class:`Records`.

    Returns:
        The records, which can be iterated as often as needed; every one that was checked parsed.

    Raises:
        LabelInputError: The file cannot be read or parsed, or holds no record.
    """
    records = Records(read_input(source), source, limit=limit)
    if not len(records):
        raise LabelInputError(f"{source} holds no data record")
    return records


__all__ = [
    "MAX_INPUT_BYTES",
    "STDIN",
    "LabelInputError",
    "Records",
    "load_label",
    "load_records",
    "parse_label",
    "read_input",
    "validation_lines",
]
