"""The scan that walks a JSON array element by element takes exactly the arrays orjson takes whole."""

from __future__ import annotations

from typing import cast

import orjson
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from datamax_dpl.adapters.cli.json_array import MAX_DEPTH, MalformedArrayError, array_elements, starts_an_array

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
@pytest.mark.parametrize("levels", [MAX_DEPTH - 1, MAX_DEPTH, MAX_DEPTH + 1])
@pytest.mark.parametrize(("opener", "closer"), [(b"[", b"]"), (b'{"k":', b"}")])
def test_the_scan_refuses_the_nesting_orjson_refuses(levels: int, opener: bytes, closer: bytes) -> None:
    inner = levels - 1
    data = b"[" + opener * inner + b"1" + closer * inner + b"]"
    assert _scanned(data) == _whole_parse(data)
