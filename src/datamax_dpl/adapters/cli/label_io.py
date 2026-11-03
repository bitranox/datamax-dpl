"""Read label specifications and data records from files or stdin.

A label is a JSON document matching ``datamax-dpl schema``. Data records come from CSV (header row
gives the field names), a JSON array of objects, or JSON lines. Input is bounded in size and
every value is read as text, because records only ever fill ``{name}`` placeholders. Records are
parsed one at a time as a command goes through them, a JSON array's too, so a batch does not hold them.
"""

from __future__ import annotations

import csv
import io
import itertools
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, cast

import orjson
from pydantic import ValidationError

from ...domain.errors import DatamaxDplError
from ...domain.label import MAX_POLYGON_POINTS, MAX_POLYGON_POINTS_LIMIT, Label
from ...domain.printable import printable
from .interrupt import raise_if_interrupted
from .json_array import array_elements, is_one_array, starts_an_array

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Default of ``[printer] max_input_bytes``: the largest label or data file accepted, so a wrong path
#: (a disk image) fails fast. The file's bytes are held while its records are read.
MAX_INPUT_BYTES = 16 * 1024 * 1024
#: Largest ``max_input_bytes`` taken, 1 GiB: a file is held whole while its records are read, so past
#: this no host here reads one.
MAX_INPUT_BYTES_LIMIT = 1024 * 1024 * 1024
_READ_PIECE_BYTES = 64 * 1024
STDIN = "-"
_LINE_END = re.compile(rb"\r\n|\r|\n")


class LabelInputError(DatamaxDplError, ValueError):
    """A label or data file could not be read or does not describe a valid label."""


@dataclass(frozen=True, slots=True)
class InputBounds:
    """How much of a label or data file a command reads, and the setting that allows more.

    Attributes:
        max_bytes: The largest file accepted (``[printer] max_input_bytes``).
        max_bytes_key: The setting a refusal names, as the section in use spells it; empty names none.
        max_polygon_points: The most vertices one polygon of a label may have
            (``[printer] max_polygon_points``).
        max_polygon_points_key: The setting that refusal names, spelled the same way; empty names none.

    Example:
        >>> InputBounds().max_bytes == MAX_INPUT_BYTES
        True
    """

    max_bytes: int = MAX_INPUT_BYTES
    max_bytes_key: str = ""
    max_polygon_points: int = MAX_POLYGON_POINTS
    max_polygon_points_key: str = ""


#: The bounds of a reader given none: the defaults, naming no setting.
DEFAULT_INPUT_BOUNDS = InputBounds()


def _read_at_most(stream: BinaryIO, limit: int) -> bytes:
    """Read up to ``limit`` bytes in pieces: ``read(limit)`` allocates all of them even for a small file."""
    pieces: list[bytes] = []
    left = limit
    while left > 0 and (piece := stream.read(min(left, _READ_PIECE_BYTES))):
        pieces.append(piece)
        left -= len(piece)
    return b"".join(pieces)


def read_input(source: str, *, bounds: InputBounds = DEFAULT_INPUT_BOUNDS) -> bytes:
    """Read ``source`` (a path, or ``-`` for stdin), refusing more than ``bounds.max_bytes``.

    Raises:
        LabelInputError: The file cannot be read or is too large; the message names the setting.
    """
    limit = bounds.max_bytes
    try:
        if source == STDIN:
            data = _read_at_most(sys.stdin.buffer, limit + 1)
        else:
            with Path(source).open("rb") as handle:
                data = _read_at_most(handle, limit + 1)
    except OSError as exc:
        raise LabelInputError(f"cannot read {source}: {exc.strerror or exc}") from None
    if len(data) > limit:
        advice = f"; a larger file needs {bounds.max_bytes_key}" if bounds.max_bytes_key else ""
        raise LabelInputError(f"{source} is larger than {limit} bytes{advice}")
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


def parse_label(data: bytes, *, source: str = "label", bounds: InputBounds = DEFAULT_INPUT_BOUNDS) -> Label:
    """Parse a JSON label document, refusing a polygon with more points than ``bounds`` allow.

    The schema takes far more points than the setting allows by default, and cannot read the
    setting; the check here keeps a long polygon a refusal of the file, as it was while the
    schema held the limit, rather than an error finding of the job. It counts the points in the
    parsed JSON before the schema runs, so a refused polygon's points are never built as models.

    Raises:
        LabelInputError: Not JSON, or not a valid label.
    """
    try:
        document: object = orjson.loads(data)
    except orjson.JSONDecodeError as exc:
        raise LabelInputError(f"{source} is not valid JSON: {exc}") from None
    _refuse_long_polygons(document, source=source, bounds=bounds)
    try:
        return Label.model_validate(document)
    except ValidationError as exc:
        raise LabelInputError(f"{source} is not a valid label: {validation_lines(exc)}") from None


def _raw_long_polygons(document: object, *, limit: int) -> Iterator[tuple[int, int]]:
    """Yield ``(index, points)`` of each polygon of a parsed, unvalidated label with more than ``limit`` points.

    Anything that is not shaped as a label is skipped: the schema refuses it next.

    Example:
        >>> polygon = {"type": "polygon", "points": [{"x": 0, "y": 0}] * 3}
        >>> list(_raw_long_polygons({"elements": [{"type": "circle"}, polygon]}, limit=2))
        [(1, 3)]
    """
    if not isinstance(document, dict):
        return
    elements = cast("dict[str, object]", document).get("elements")
    if not isinstance(elements, list):
        return
    for index, element in enumerate(cast("list[object]", elements)):
        if not isinstance(element, dict):
            continue
        fields = cast("dict[str, object]", element)
        points = fields.get("points")
        if fields.get("type") == "polygon" and isinstance(points, list):
            count = len(cast("list[object]", points))
            if count > limit:
                yield index, count


def _refuse_long_polygons(document: object, *, source: str, bounds: InputBounds) -> None:
    """Refuse ``document`` when a polygon has more points than ``bounds.max_polygon_points``.

    The message reads as the schema's did when it held the limit, plus the setting that allows more;
    at the schema's own ceiling no setting allows more, so the message names none.
    """
    limit = bounds.max_polygon_points
    lines = [
        f"elements.{index}.polygon.points: List should have at most {limit} items after validation, not {points}"
        for index, points in _raw_long_polygons(document, limit=limit)
    ]
    if not lines:
        return
    key = bounds.max_polygon_points_key if limit < MAX_POLYGON_POINTS_LIMIT else ""
    advice = f"; more points need {key}" if key else ""
    raise LabelInputError(f"{source} is not a valid label: {'; '.join(lines)}{advice}")


def load_label(source: str, *, bounds: InputBounds = DEFAULT_INPUT_BOUNDS) -> Label:
    """Read and parse the label at ``source`` (path or ``-``), within ``bounds``."""
    return parse_label(read_input(source, bounds=bounds), source=source, bounds=bounds)


def _as_record(row: object, *, where: str) -> dict[str, str]:
    if not isinstance(row, dict):
        raise LabelInputError(f"{where} is not an object")
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
        yield _as_record(parsed, where=f"{source} line {number}")


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

    Holds the file's bytes, which ``max_input_bytes`` bounds, and no record: stdin can be read
    only once, while a batch is gone over once to check it and once to send it. A JSON array is
    walked element by element (:mod:`.json_array`), each pass parsing one element at a time.
    """

    def __init__(self, data: bytes, source: str, *, limit: int | None = None) -> None:
        """Read ``data``, the content of ``source``, and check every record as far as ``limit``.

        Args:
            data: The file's bytes.
            source: Its name, which also says the format: CSV for ``*.csv``.
            limit: Stop checking CSV or JSON lines once past this many records, so a caller refusing
                more than that does not go through them all. :func:`len` then stops one past it;
                a JSON array, read to its end by then to know it is one, counts in full.

        Raises:
            LabelInputError: A record that was checked cannot be parsed.
        """
        self._data = data
        self._source = source
        self._csv = source.lower().endswith(".csv")
        opens_an_array = not self._csv and starts_an_array(data)
        self._array = opens_an_array and is_one_array(data)
        self._one: list[object] | None = None if self._csv or opens_an_array else _one_document(data)
        counted_in_full = self._array or self._one is not None
        checked = iter(self) if limit is None or counted_in_full else itertools.islice(self, limit + 1)
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
        if self._array:
            rows: Iterator[object] = array_elements(self._data)
        elif self._one is not None:
            rows = iter(self._one)
        else:
            return _records_from_json_lines(self._data, self._source)
        # Counted from 0, as a finding's record is; a JSON line goes by its line in the file instead.
        return (_as_record(row, where=f"{self._source} record {number}") for number, row in enumerate(rows))

    def __len__(self) -> int:
        """How many records were checked: all of them, unless the limit stopped it (see ``__init__``)."""
        return self._count


def _one_document(data: bytes) -> list[object] | None:
    """The one record of a JSON document that is no array, or None when ``data`` is no one document: JSON lines.

    An array never comes here, whole or broken: :func:`.json_array.is_one_array` judged it, and a
    broken one is read as JSON lines, whose refusal names the line that is not JSON.
    """
    try:
        document: object = orjson.loads(data)
    except orjson.JSONDecodeError:
        return None
    return [document]


def load_records(source: str, *, limit: int | None = None, bounds: InputBounds = DEFAULT_INPUT_BOUNDS) -> Records:
    """Read data records: CSV for ``*.csv``, else JSON array, JSON object or JSON lines.

    Args:
        source: The file, or ``-`` for stdin.
        limit: See :class:`Records`.
        bounds: How large the file may be.

    Returns:
        The records, which can be iterated as often as needed; every one that was checked parsed.

    Raises:
        LabelInputError: The file cannot be read or parsed, or holds no record.
    """
    records = Records(read_input(source, bounds=bounds), source, limit=limit)
    if not len(records):
        raise LabelInputError(f"{source} holds no data record")
    return records


__all__ = [
    "DEFAULT_INPUT_BOUNDS",
    "MAX_INPUT_BYTES",
    "MAX_INPUT_BYTES_LIMIT",
    "STDIN",
    "InputBounds",
    "LabelInputError",
    "Records",
    "load_label",
    "load_records",
    "parse_label",
    "read_input",
    "validation_lines",
]
