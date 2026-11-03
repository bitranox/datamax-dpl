"""The walk over a JSON array element by element takes exactly the arrays orjson takes whole."""

from __future__ import annotations

from typing import cast

import orjson
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from json_depth import ORJSON_DEPTH, READER_STOPS_SHORT_OF_ORJSON

from datamax_dpl.adapters.cli.json_array import MalformedArrayError, array_elements, starts_an_array

_TEXT = st.text(alphabet=st.characters(codec="utf-8"), max_size=12) | st.sampled_from(['"', "\\", "[", "]", "{", "}"])
_SCALARS = st.none() | st.booleans() | st.integers(-(10**6), 10**6) | _TEXT
_VALUES = st.recursive(
    _SCALARS,
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(_TEXT, inner, max_size=4),
    max_leaves=12,
)


@settings(max_examples=300, deadline=None)
@given(rows=st.lists(_VALUES, max_size=8), indent=st.booleans())
def test_the_scan_gives_the_elements_of_a_whole_parse(rows: list[object], indent: bool) -> None:
    data = orjson.dumps(rows, option=orjson.OPT_INDENT_2 if indent else 0)
    assert starts_an_array(data)
    assert list(array_elements(data)) == orjson.loads(data)


def _whole_parse(data: bytes) -> list[object] | None:
    """The oracle: the elements orjson finds when it parses ``data`` whole, or None when it is no one array."""
    try:
        document: object = orjson.loads(data)
    except orjson.JSONDecodeError:
        return None
    return cast("list[object]", document) if isinstance(document, list) else None


def _scanned(data: bytes) -> list[object] | None:
    try:
        return list(array_elements(data))
    except MalformedArrayError:
        return None


_NOISE = st.sampled_from([b"[", b"]", b"{", b"}", b",", b'"', b"\\", b":", b" ", b"\n", b"1", b"\x00", b"\xff"])


@settings(max_examples=600, deadline=None)
@given(rows=st.lists(_VALUES, max_size=6), at=st.integers(min_value=0), noise=_NOISE, mutation=st.integers(0, 2))
def test_the_scan_takes_exactly_what_a_whole_parse_takes(
    rows: list[object], at: int, noise: bytes, mutation: int
) -> None:
    """Valid arrays with one byte inserted, replaced or removed: the scan agrees with orjson on every one."""
    data = orjson.dumps(rows)
    where = at % (len(data) + 1)
    data = [
        data[:where] + noise + data[where:],
        data[:where] + noise + data[where + 1 :],
        data[:where] + data[where + 1 :],
    ][mutation]
    if not starts_an_array(data):
        return
    assert _scanned(data) == _whole_parse(data), data


@pytest.mark.os_agnostic
@pytest.mark.parametrize("levels", [ORJSON_DEPTH - 1, ORJSON_DEPTH, ORJSON_DEPTH + 1])
@pytest.mark.parametrize(("opener", "closer"), [(b"[", b"]"), (b'{"k":', b"}")])
def test_the_scan_refuses_the_nesting_orjson_refuses(levels: int, opener: bytes, closer: bytes) -> None:
    inner = levels - 1
    data = b"[" + opener * inner + b"1" + closer * inner + b"]"
    takes = _whole_parse(data) is not None and not READER_STOPS_SHORT_OF_ORJSON
    # Whether, not what: comparing two lists this deep with == recurses past 3.10's limit itself.
    assert (_scanned(data) is not None) is takes


#: Runs of escapes from short to long: a reader that walks a string escape by escape, or keeps state
#: per escape, shows here.
_ESCAPE_RUNS = [1, 4096, 4097, 100_000]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("escapes", _ESCAPE_RUNS)
@pytest.mark.parametrize(
    ("escape", "tail"),
    [
        (b"\\\\", b'"}]'),
        (b'\\"', b'"}]'),
        (b"\\u00e4", b'"}]'),
        (b"\\\\", b"}]"),
        (b"\\\\", b'\\"}]'),
        (b"\\\\", b"\\"),
        (b'\\"', b""),
        (b"\\\\", b'x"},{"b":"]"}]'),
    ],
)
def test_a_long_run_of_escapes_reads_as_a_whole_parse_reads_it(escapes: int, escape: bytes, tail: bytes) -> None:
    """Terminated, unterminated, ending on a lone backslash, followed by more elements: as orjson says."""
    data = b'[{"a":"' + escape * escapes + tail
    assert _scanned(data) == _whole_parse(data)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("levels", "parses"), [(ORJSON_DEPTH, True), (ORJSON_DEPTH + 1, False)])
def test_orjson_still_refuses_exactly_the_nesting_the_scan_refuses(levels: int, parses: bool) -> None:
    """The depth pins above assume orjson's own limit: an orjson that moves it turns this red, not a user's file."""
    assert (_whole_parse(b"[" * levels + b"]" * levels) is not None) is parses
