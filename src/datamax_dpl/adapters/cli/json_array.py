"""Walk a JSON array held as bytes one element at a time, so its records are never all parsed at once.

``orjson.loads`` of a whole array builds every element before the first is used: a JSON array data
file then peaked at several times its size, where CSV and JSON lines stay at the file's bytes. The
scan here finds where each top-level element ends, knowing only strings, their escapes and bracket
depth, and hands that slice to orjson. orjson still judges every byte of every element; the scan
judges only what lies between them (one comma, whitespace, the closing bracket and nothing after
it), so it takes exactly the arrays a whole parse takes.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import orjson

from ...domain.errors import DatamaxDplError

if TYPE_CHECKING:
    from collections.abc import Iterator

#: orjson's nesting limit, the outer array counted: it refuses a 1025th level of arrays and objects
#: together (measured, orjson 3.13). The scan refuses the same, as a whole parse did.
MAX_DEPTH = 1024

#: Most escapes one regular expression match takes inside a string. The engine keeps a backtracking
#: mark for every repetition, about 120 bytes of resident memory that tracemalloc does not see, so
#: an unbounded repeat over a string of millions of escapes took hundreds of MB. Past this many the
#: string goes on in a loop from Python (:func:`_string_end`), each match bounded the same: about
#: half a MB at most. A possessive or atomic group would keep no marks, but needs Python 3.11.
_ESCAPES_PER_MATCH = 4096
#: A string's content: plain bytes, and each backslash taking the byte after it, so an escaped
#: quote does not end the string.
_STRING_CONTENT = rb'[^"\\]*(?:\\.[^"\\]*){0,%d}' % _ESCAPES_PER_MATCH
#: What changes the scan's state at the array's own level: a whole string, a bracket, or the comma
#: between elements. The string goes in one match, in C: walking it from Python was most of the
#: scan's time. A quote matched alone opens a string that does not end within one match: one that
#: never ends, or one with more escapes than :data:`_ESCAPES_PER_MATCH`.
_OUTER = re.compile(rb'"' + _STRING_CONTENT + rb'"|[][{},"]', re.DOTALL)
#: The same inside an element, where a comma changes nothing.
_INNER = re.compile(rb'"' + _STRING_CONTENT + rb'"|[][{}"]', re.DOTALL)
#: As much of a string's content as one bounded match takes.
_STRING_RUN = re.compile(_STRING_CONTENT, re.DOTALL)
#: Anything JSON does not take as whitespace between tokens: space, tab, line feed, carriage return (RFC 8259).
_NOT_WHITESPACE = re.compile(rb"[^ \t\n\r]")

_QUOTE = ord('"')
_BACKSLASH = ord("\\")
_COMMA = ord(",")
_CLOSE_ARRAY = ord("]")
_OPENERS = frozenset(b"[{")
_CLOSERS = frozenset(b"]}")


class MalformedArrayError(DatamaxDplError, ValueError):
    """The bytes are not one JSON array: something between, around or inside its elements is not JSON.

    The CLI's own, as :class:`~.label_io.LabelInputError` is: the records reader asks for it and
    reads such a file as JSON lines instead, so no caller meets it.
    """


def _token(data: bytes, position: int) -> int:
    """Where the first byte at or after ``position`` that is not whitespace sits, or ``len(data)``."""
    found = _NOT_WHITESPACE.search(data, position)
    return len(data) if found is None else found.start()


def starts_an_array(data: bytes) -> bool:
    """Whether the first byte of ``data`` that is not whitespace opens an array.

    Example:
        >>> starts_an_array(b' \\n[1]'), starts_an_array(b'{"a": 1}')
        (True, False)
    """
    first = _token(data, 0)
    return data[first : first + 1] == b"["


def _string_end(data: bytes, position: int) -> int:
    """Where the string whose content starts at ``position`` ends, one past its closing quote.

    Goes on match after bounded match, each taking up to :data:`_ESCAPES_PER_MATCH` escapes, so
    the engine's memory stays bounded however many the string holds.

    Raises:
        MalformedArrayError: The string does not end: no closing quote, or its last byte is a
            backslash with nothing left to escape.

    Example:
        >>> _string_end(b'"a\\\\"b"', 1)  # the escaped quote does not end it
        6
    """
    while (run := _STRING_RUN.match(data, position)) is not None:
        position = run.end()
        if position == len(data):
            break
        if data[position] == _QUOTE:
            return position + 1
        # A backslash: the match stopped at its bound, unless nothing follows to escape.
        if data[position] != _BACKSLASH or position + 1 == len(data):
            break
    raise MalformedArrayError("a string does not end")


def _element_spans(data: bytes) -> Iterator[tuple[int, int]]:
    """Yield ``(start, end)`` of each top-level element of the array ``data`` holds, surrounding whitespace kept.

    Raises:
        MalformedArrayError: ``data`` holds no array, the array does not end, nests deeper than
            :data:`MAX_DEPTH`, ends with a comma, or something other than whitespace follows it.
    """
    opening = _token(data, 0)
    if data[opening : opening + 1] != b"[":
        raise MalformedArrayError("not an array")
    start = position = opening + 1
    depth, any_element = 1, False
    while (found := (_OUTER if depth == 1 else _INNER).search(data, position)) is not None:
        at, position = found.span()
        byte = data[at]
        if byte == _QUOTE:
            if position - at == 1:
                position = _string_end(data, position)
        elif byte in _OPENERS:
            depth += 1
            if depth > MAX_DEPTH:
                raise MalformedArrayError("nested too deep")
        elif byte == _COMMA:
            yield start, at
            start, any_element = position, True
        elif byte in _CLOSERS:
            depth -= 1
            if depth == 0:
                yield from _last_span(data, start=start, at=at, any_element=any_element)
                return
    raise MalformedArrayError("the array does not end")


def _last_span(data: bytes, *, start: int, at: int, any_element: bool) -> Iterator[tuple[int, int]]:
    """The span before the array's closing byte at ``at``: none for ``[]``, after checking what follows.

    Raises:
        MalformedArrayError: The byte at ``at`` closes an object, a comma comes last, or more than
            whitespace follows.
    """
    if data[at] != _CLOSE_ARRAY:
        raise MalformedArrayError("the array is closed as an object")
    empty = _token(data, start) == at
    if empty and any_element:
        raise MalformedArrayError("a comma comes last")
    if _token(data, at + 1) != len(data):
        raise MalformedArrayError("more follows the array")
    if not empty:
        yield start, at


def array_elements(data: bytes) -> Iterator[object]:
    """Parse the elements of the JSON array ``data`` holds, one at a time, in order.

    Only the element in hand is parsed and held. An element is yielded before what follows it is
    checked, so a caller that must not act on a malformed array reads it to the end first.

    Raises:
        MalformedArrayError: ``data`` is not one JSON array; orjson would refuse it whole.

    Example:
        >>> list(array_elements(b'[{"a": "[1,"}, {"b": 2}]'))
        [{'a': '[1,'}, {'b': 2}]
        >>> list(array_elements(b"[1,]"))
        Traceback (most recent call last):
        ...
        datamax_dpl.adapters.cli.json_array.MalformedArrayError: a comma comes last
    """
    try:
        for start, end in _element_spans(data):
            yield orjson.loads(data[start:end])
    except orjson.JSONDecodeError as exc:
        raise MalformedArrayError(str(exc)) from None


__all__ = ["MAX_DEPTH", "MalformedArrayError", "array_elements", "starts_an_array"]
