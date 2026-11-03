"""Walk a JSON array held as bytes one element at a time, so its records are never all parsed at once.

``orjson.loads`` of a whole array builds every element before the first is used: a JSON array data
file then peaked at several times its size, where CSV and JSON lines stay at the file's bytes.
Python's own :meth:`json.JSONDecoder.raw_decode` finds where each top-level element ends, and
orjson parses that slice, so each record is what a whole parse gives. The code here judges only
what lies between the elements: whitespace, one comma, the closing bracket and nothing after it.
"""

from __future__ import annotations

import itertools
import json
import re
from typing import TYPE_CHECKING, cast

import orjson

from ...domain.errors import DatamaxDplError

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Finds where an element ends; what it builds is dropped, orjson builds the record.
_FINDER = json.JSONDecoder()
#: Anything JSON does not take as whitespace between tokens: space, tab, line feed, carriage return (RFC 8259).
_NOT_WHITESPACE = re.compile(r"[^ \t\n\r]")
#: The same over the bytes, so finding the first token copies nothing.
_NOT_WHITESPACE_BYTE = re.compile(rb"[^ \t\n\r]")
_OPEN_ARRAY = ord("[")


class MalformedArrayError(DatamaxDplError, ValueError):
    """The bytes are not one JSON array: something between, around or inside its elements is not JSON.

    The CLI's own, as :class:`~.label_io.LabelInputError` is: the records reader asks for it and
    reads such a file as JSON lines instead, so no caller meets it.
    """


class ElementTooDeepError(DatamaxDplError):
    """An element is nested deeper than Python's own JSON reader goes, so where it ends is not known.

    Not a malformed array: a whole parse may take it, so the file is refused by name, never read
    as JSON lines. The records reader puts the file's name in front; no library caller meets it.
    """

    def __init__(self, index: int) -> None:
        """Name the element at ``index``, counted from 0 as a record is."""
        super().__init__(
            f"record {index} is nested deeper than this Python reads"
            " (Python 3.10 and 3.11 read about 995 levels, 3.12 and later 1024)"
        )


def _token(text: str, position: int) -> int:
    """Where the first character at or after ``position`` that is not whitespace sits, or ``len(text)``."""
    found = _NOT_WHITESPACE.search(text, position)
    return len(text) if found is None else found.start()


def starts_an_array(data: bytes) -> bool:
    """Whether the first byte of ``data`` that is not whitespace opens an array.

    Example:
        >>> starts_an_array(b' \\n[1]'), starts_an_array(b'{"a": 1}')
        (True, False)
    """
    first = _NOT_WHITESPACE_BYTE.search(data)
    return first is not None and data[first.start()] == _OPEN_ARRAY


def _element_end(text: str, start: int, *, index: int) -> int:
    """Where element ``index``, starting at ``start``, ends, as Python's own JSON reader finds it.

    Raises:
        MalformedArrayError: No JSON value starts there.
        ElementTooDeepError: It nests deeper than the reader goes.
    """
    try:
        return _FINDER.raw_decode(text, start)[1]
    # The reader's own JSONDecodeError, and an integer of more digits than Python converts, which
    # orjson refuses too.
    except ValueError as exc:
        raise MalformedArrayError(str(exc)) from None
    # Past the reader's own limit: far past orjson's from Python 3.12, but about 995 levels on 3.10
    # and 3.11, short of orjson's 1024 (sys.getrecursionlimit). Raising that limit here would change
    # it for the whole process.
    except RecursionError:
        raise ElementTooDeepError(index) from None


def _parsed(data: bytes, start: int, end: int) -> object:
    """The element ``data[start:end]`` as orjson parses it, inside an array of its own.

    The array orjson parses it in stands for the one it came from: orjson then counts that level
    too, and refuses exactly the nesting it refused in the whole file.

    Raises:
        MalformedArrayError: orjson refuses the element.
    """
    try:
        wrapped = cast("list[object]", orjson.loads(b"".join((b"[", memoryview(data)[start:end], b"]"))))
    except orjson.JSONDecodeError as exc:
        raise MalformedArrayError(str(exc)) from None
    return wrapped[0]


def _after_the_array(text: str, closing: int) -> None:
    """Check only whitespace follows the array's closing bracket at ``closing``.

    Raises:
        MalformedArrayError: More follows the array.
    """
    if _token(text, closing + 1) != len(text):
        raise MalformedArrayError("more follows the array")


def array_elements(data: bytes) -> Iterator[object]:
    """Parse the elements of the JSON array ``data`` holds, one at a time, in order.

    Only the element in hand is parsed and held, beside one text copy of ``data``. An element is
    yielded before what follows it is checked, so a caller that must not act on a malformed array
    reads it to the end first.

    Raises:
        MalformedArrayError: ``data`` is not one JSON array; orjson would refuse it whole.
        ElementTooDeepError: An element nests deeper than Python's own JSON reader goes.

    Example:
        >>> list(array_elements(b'[{"a": "[1,"}, {"b": 2}]'))
        [{'a': '[1,'}, {'b': 2}]
        >>> list(array_elements(b"[1,]"))
        Traceback (most recent call last):
        ...
        datamax_dpl.adapters.cli.json_array.MalformedArrayError: Expecting value: line 1 column 4 (char 3)
    """
    # Latin-1 gives one character per byte: a position in the text is the same position in the
    # bytes, and the text takes one byte per byte of the file. JSON's own syntax is ASCII, and a
    # byte past 0x7F belongs inside a string, where the reader takes any character; orjson then
    # judges the UTF-8 of each slice. Read as UTF-8 instead, one character past U+FFFF would make
    # the whole text four bytes a character.
    text = data.decode("latin-1")
    opening = _token(text, 0)
    if text[opening : opening + 1] != "[":
        raise MalformedArrayError("not an array")
    position = _token(text, opening + 1)
    if text[position : position + 1] == "]":
        _after_the_array(text, position)
        return
    for index in itertools.count():
        end = _element_end(text, position, index=index)
        yield _parsed(data, position, end)
        position = _token(text, end)
        follows = text[position : position + 1]
        if follows == "]":
            _after_the_array(text, position)
            return
        if follows != ",":
            raise MalformedArrayError("a comma or the array's end is missing")
        position = _token(text, position + 1)


__all__ = ["ElementTooDeepError", "MalformedArrayError", "array_elements", "starts_an_array"]
