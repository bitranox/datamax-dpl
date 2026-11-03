"""A JSON array ``--data`` file is read one element at a time, with the records and refusals of a whole parse.

The refusals are pinned word for word: a file that is not one JSON document is read as JSON lines,
and every message below comes from that reading, whatever reads the array.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tracemalloc
from typing import TYPE_CHECKING

import orjson
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from json_depth import READER_STOPS_SHORT_OF_ORJSON

from datamax_dpl.adapters.cli.label_io import LabelInputError, Records, load_records

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_SOURCE = "d.json"

#: The refusal, after "record N", of an element nested deeper than Python 3.10 and 3.11 read.
_TOO_DEEP = (
    "is nested deeper than Python 3.10 and 3.11 read (about 995 levels);"
    " Python 3.12 or later reads up to orjson's limit of 1024"
)
#: orjson's refusal of a one-line array nested past its limit, as the JSON lines reading gives it.
_DEPTH_REFUSAL = "line 1 is not JSON: depth limit exceeded: line 1 column 1026 (char 1025)"

_REFUSED = [
    pytest.param(
        b'[{"a":"r0"},{"a":"r1"',
        "line 1 is not JSON: unexpected end of data: line 1 column 22 (char 21)",
        id="truncated",
    ),
    pytest.param(
        b'[{"a":"r0"}] x',
        "line 1 is not JSON: unexpected content after document: line 1 column 14 (char 13)",
        id="trailing-garbage",
    ),
    pytest.param(
        b'[{"a":"r0"},]',
        "line 1 is not JSON: trailing comma is not allowed: line 1 column 12 (char 11)",
        id="trailing-comma",
    ),
    pytest.param(
        b'[{"a":"r0"} {"a":"r1"}]',
        "line 1 is not JSON: unexpected character, expected ',' or ']': line 1 column 13 (char 12)",
        id="missing-comma",
    ),
    pytest.param(
        b'[,{"a":"r0"}]',
        "line 1 is not JSON: unexpected character, expected a JSON value: line 1 column 2 (char 1)",
        id="leading-comma",
    ),
    pytest.param(
        b'[{"a":"r0"}][{"a":"r1"}]',
        "line 1 is not JSON: unexpected content after document: line 1 column 13 (char 12)",
        id="two-arrays",
    ),
    pytest.param(
        b'[{"a":"r0"}}',
        "line 1 is not JSON: unexpected character, expected ',' or ']': line 1 column 12 (char 11)",
        id="mismatched-closer",
    ),
    pytest.param(b'"text"', "record 0 is not an object", id="a-string"),
    pytest.param(b"5", "record 0 is not an object", id="a-number"),
    pytest.param(b"  \n\t ", "holds no data record", id="whitespace-only"),
    pytest.param(b"[ \n ]", "holds no data record", id="empty-array-with-whitespace"),
    pytest.param(
        b'\xef\xbb\xbf[{"a":"r0"}]',
        "line 1 is not JSON: UTF-8 byte order mark (BOM) is not supported: line 1 column 1 (char 0)",
        id="bom",
    ),
    pytest.param(
        b'[\n  {"a": "r0"},\n  {"a": "r1"\n',
        "line 1 is not JSON: unexpected end of data: line 1 column 2 (char 1)",
        id="pretty-truncated",
    ),
    pytest.param(
        b'[\n  {"a": "r0"},\n  {"a": "r1"},\n  {"a" "r2"}\n]\n',
        "line 1 is not JSON: unexpected end of data: line 1 column 2 (char 1)",
        id="pretty-broken-at-record-2",
    ),
    pytest.param(
        b'[{"a":"r0"},\x00{"a":"r1"}]',
        "line 1 is not JSON: unexpected character, expected a JSON value: line 1 column 13 (char 12)",
        id="nul-between-elements",
    ),
    pytest.param(
        b'[{"a":"r\x000"}]',
        "line 1 is not JSON: unexpected control character in string: line 1 column 9 (char 8)",
        id="nul-in-a-string",
    ),
    pytest.param(
        b"[" * 1025 + b"]" * 1025,
        # On 3.10 and 3.11 Python's own reader stops first, before orjson's limit is reached.
        f"record 0 {_TOO_DEEP}" if READER_STOPS_SHORT_OF_ORJSON else _DEPTH_REFUSAL,
        id="one-level-too-deep",
    ),
    pytest.param(
        b"[" * 1024 + b"]" * 1024,
        f"record 0 {_TOO_DEEP}" if READER_STOPS_SHORT_OF_ORJSON else "record 0 is not an object",
        id="deepest-taken",
    ),
    pytest.param(
        # Deeper than Python's own JSON reader goes on any version and stack: it stops with a
        # RecursionError. On 3.12 and later that is past orjson's limit, so orjson's refusal.
        b"[" * 100_000 + b"]" * 100_000,
        f"record 0 {_TOO_DEEP}" if READER_STOPS_SHORT_OF_ORJSON else _DEPTH_REFUSAL,
        id="far-too-deep",
    ),
    pytest.param(
        b'[1, {"a":"r1"}, ' + b"[" * 100_000 + b"]" * 100_000 + b"]",
        # Named on 3.10 and 3.11, though an earlier record is no object: the array never read clean.
        f"record 2 {_TOO_DEEP}"
        if READER_STOPS_SHORT_OF_ORJSON
        else "line 1 is not JSON: depth limit exceeded: line 1 column 1041 (char 1040)",
        id="far-too-deep-after-a-refused-record",
    ),
    pytest.param(
        b'[{"a":"r0}]',
        "line 1 is not JSON: unexpected end of data: line 1 column 12 (char 11)",
        id="unterminated-string",
    ),
    pytest.param(
        b'[{"a":"\\ud800"}]',
        "line 1 is not JSON: no low surrogate in string: line 1 column 8 (char 7)",
        id="lone-surrogate",
    ),
    pytest.param(b'[{"a":"r0"}]\n{"a":"r1"}\n', "line 1 is not an object", id="array-then-a-line"),
    pytest.param(
        b'\x0c[{"a":"r0"}]',
        "line 1 is not JSON: unexpected character, expected a JSON value: line 1 column 1 (char 0)",
        id="form-feed-is-no-whitespace",
    ),
    pytest.param(
        b'[{"a":"\xff"}]',
        "line 1 is not JSON: str is not valid UTF-8: surrogates not allowed: line 1 column 1 (char 0)",
        id="not-utf8",
    ),
    pytest.param(b'[{"a":"r0"}, [1]]', "record 1 is not an object", id="second-element-not-an-object"),
    pytest.param(
        b'[{"a":NaN}]',
        "line 1 is not JSON: unexpected character, expected a JSON value: line 1 column 7 (char 6)",
        id="nan",
    ),
    pytest.param(
        b"[{'a':'r0'}]",
        "line 1 is not JSON: unexpected character, expected a string key: line 1 column 3 (char 2)",
        id="single-quotes",
    ),
]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("content", "says"), _REFUSED)
def test_a_malformed_array_is_refused_with_the_same_words(tmp_path: Path, content: bytes, says: str) -> None:
    source = tmp_path / _SOURCE
    source.write_bytes(content)
    with pytest.raises(LabelInputError) as refused:
        load_records(str(source))
    assert str(refused.value) == f"{source} {says}"


_READ = [
    pytest.param(b'[{"a":"[{\\"]}"}]', [{"a": '[{"]}'}], id="brackets-and-an-escaped-quote-in-a-string"),
    pytest.param(b'[{"a":"x\\\\"},{"a":"y"}]', [{"a": "x\\"}, {"a": "y"}], id="escaped-backslash-before-the-quote"),
    pytest.param(b'[{"a":"\\\\\\""}]', [{"a": '\\"'}], id="backslash-then-escaped-quote"),
    pytest.param(b'[{"a":"\\u00e4\\ud83d\\ude00"}]', [{"a": chr(0xE4) + chr(0x1F600)}], id="unicode-escapes"),
    pytest.param(b' \r\n\t[ \n{ "a" : "r0" } \r\n, {"a":"r1"}\t] \n', [{"a": "r0"}, {"a": "r1"}], id="whitespace"),
    pytest.param(b'[{"a":{"b":[1,{"c":"]"}]}}]', [{"a": "{'b': [1, {'c': ']'}]}"}], id="nested-values"),
    pytest.param(b'[{"a":1,"b":null,"c":true}]', [{"a": "1", "b": "", "c": "True"}], id="scalars-as-text"),
    pytest.param(b'[{"a":"r,0"},{"a":"r1"}]', [{"a": "r,0"}, {"a": "r1"}], id="comma-in-a-string"),
]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("content", "records"), _READ)
def test_an_array_gives_exactly_these_records(tmp_path: Path, content: bytes, records: list[dict[str, str]]) -> None:
    source = tmp_path / _SOURCE
    source.write_bytes(content)
    loaded = load_records(str(source))
    assert list(loaded) == records
    assert len(loaded) == len(records)


def _refusal_of(data: bytes) -> str:
    with pytest.raises(LabelInputError) as refused:
        Records(data, _SOURCE)
    return str(refused.value)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("before", "open_already", "opener", "closer", "after"),
    [
        pytest.param(b"[", 1, b"[", b"]", b"]", id="arrays"),
        pytest.param(b'[{"a":', 2, b"[", b"]", b"}]", id="a-record-s-value"),
        pytest.param(b'[{"a":"r0"},\n', 1, b'{"k":', b"}", b"\n]\n", id="objects-after-a-record-on-lines"),
    ],
)
def test_one_level_and_far_too_deep_read_alike(
    before: bytes, open_already: int, opener: bytes, closer: bytes, after: bytes
) -> None:
    """One refusal for nesting past the limit, however far past and whatever the stack lets Python read.

    From 3.14 Python's own reader goes as deep as the C stack lets it, so how far it reads differs
    from host to host; 100000 levels may stop it there, 1025 never does on 3.12 and later.
    """

    def nested(levels: int) -> bytes:
        inner = levels - open_already
        return before + opener * inner + b"1" + closer * inner + after

    assert _refusal_of(nested(1025)) == _refusal_of(nested(100_000))

    """1024 levels, the record's own counted: read on 3.12 and later; on 3.10 and 3.11 refused, not crashed.

    A whole parse took it there too, and then str() of the value crashed past the recursion limit.
    """
    data = b'[{"a":' + b"[" * 1022 + b"]" * 1022 + b"}]"
    if READER_STOPS_SHORT_OF_ORJSON:
        with pytest.raises(LabelInputError) as refused:
            Records(data, _SOURCE)
        assert str(refused.value) == f"{_SOURCE} record 0 {_TOO_DEEP}"
    else:
        assert len(Records(data, _SOURCE)) == 1


@pytest.mark.os_agnostic
def test_a_huge_single_element_is_read_whole(tmp_path: Path) -> None:
    value = "x" * (4 * 1024 * 1024)
    source = tmp_path / _SOURCE
    source.write_bytes(orjson.dumps([{"a": value}]))
    assert list(load_records(str(source))) == [{"a": value}]


@pytest.mark.os_agnostic
def test_a_limit_still_counts_an_array_in_full() -> None:
    data = orjson.dumps([{"a": f"r{number}"} for number in range(10)])
    assert len(Records(data, _SOURCE, limit=3)) == 10


def _array_of(rows: int, *, value: bytes = b"r%d") -> bytes:
    return b"[" + b",".join(b'{"a":"' + value % row + b'"}' for row in range(rows)) + b"]"


def _peak_above_the_file(rows: int, *, value: bytes) -> int:
    data = _array_of(rows, value=value)
    tracemalloc.start()
    try:
        # Counting at construction and one read after: a parser that built the list would hold every record.
        for _ in Records(data, _SOURCE):
            pass
        return tracemalloc.get_traced_memory()[1] - len(data)
    finally:
        tracemalloc.stop()


#: What reading an array may peak at beyond its bytes and one text copy of them (the copy the
#: element finder reads), whatever its records: one record and the walk's own state, not the rows.
#: A whole parse held 12 MB for the 50000 records measured here.
_MOST_A_READ_HOLDS = 256 * 1024


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(b"r%d", id="ascii"),
        # One character past U+FFFF: a text read as UTF-8 would take four bytes for every character of the file.
        pytest.param(chr(0x1F600).encode() + b"%d", id="past-u-ffff"),
    ],
)
def test_reading_an_array_holds_none_of_its_records(value: bytes) -> None:
    few, many = _peak_above_the_file(5000, value=value), _peak_above_the_file(50000, value=value)
    assert many < _MOST_A_READ_HOLDS, (few, many)
    assert many < 2 * few + 16 * 1024, (few, many)


_BASE_PYTHON: str = getattr(sys, "_base_executable", sys.executable)


def _resident_rise(escapes: int) -> tuple[int, int]:
    answer = subprocess.run(
        [sys.executable, "-m", "escapes_peak_probe"],
        executable=_BASE_PYTHON,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path), "ESCAPES": str(escapes)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=300,
    )
    assert answer.returncode == 0, answer.stderr
    size, rise = answer.stdout.split()
    return int(size), int(rise)


#: What reading may raise the resident peak by, in file sizes, plus the interpreter's own noise:
#: the file's text copy, the element's bytes once more and its parsed text, half a file here (2.5
#: files measured). A backtracking mark per escape in the one string took about 120 bytes an
#: escape: 242 MB for the 4 MB file measured here.
_MOST_FILES_A_READ_RAISES = 3
_RESIDENT_NOISE = 16 * 1024 * 1024


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="the resource module, which reads the resident peak, is POSIX-only")
@pytest.mark.parametrize("escapes", [500_000, 2_000_000])
def test_a_long_string_of_escapes_raises_the_resident_peak_by_no_more_than_its_bytes(escapes: int) -> None:
    size, rise = _resident_rise(escapes)
    assert rise < _MOST_FILES_A_READ_RAISES * size + _RESIDENT_NOISE, (size, rise)


_TEXT = st.text(alphabet=st.characters(codec="utf-8"), max_size=12) | st.sampled_from(['"', "\\", "[", "]", "{", "}"])
_SCALARS = st.none() | st.booleans() | st.integers(-(10**6), 10**6) | _TEXT
_VALUES = st.recursive(
    _SCALARS,
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(_TEXT, inner, max_size=4),
    max_leaves=12,
)
_OBJECTS = st.lists(st.dictionaries(_TEXT, _VALUES, max_size=5), max_size=8)


@settings(max_examples=300, deadline=None)
@given(rows=_OBJECTS, indent=st.booleans())
def test_streamed_records_equal_a_whole_parse(rows: list[dict[str, object]], indent: bool) -> None:
    data = orjson.dumps(rows, option=orjson.OPT_INDENT_2 if indent else 0)
    expected = orjson.loads(data)
    records = list(Records(data, _SOURCE))
    assert records == [{key: "" if value is None else str(value) for key, value in row.items()} for row in expected]


# ======================== one pass to check the array ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("content", "says"),
    [
        pytest.param(
            b'[{"a":"r0"}, 1, {"a" "r2"}]',
            "line 1 is not JSON: unexpected character, expected ':' after key: line 1 column 22 (char 21)",
            id="not-an-object-then-broken",
        ),
        pytest.param(b'[1, {"a":"r1"}, 2]', "record 0 is not an object", id="first-of-two-not-objects"),
        pytest.param(b'[{"a":"r0"}, [1], "x"]', "record 1 is not an object", id="second-and-third-not-objects"),
    ],
)
def test_a_record_that_is_no_object_is_refused_only_once_the_array_read_clean(content: bytes, says: str) -> None:
    """The first such record is the refusal; a broken array after it is read as JSON lines instead, as before."""
    with pytest.raises(LabelInputError) as refused:
        Records(content, _SOURCE)
    assert str(refused.value) == f"{_SOURCE} {says}"


class _CountedLoads:
    """``orjson.loads`` with a count of its calls: orjson is the edge where an element is parsed."""

    def __init__(self, loads: Callable[[bytes], object]) -> None:
        self._loads = loads
        self.calls = 0

    def __call__(self, data: bytes) -> object:
        self.calls += 1
        return self._loads(data)


@pytest.mark.os_agnostic
def test_checking_an_array_parses_each_element_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Knowing it is one array and counting its records is one pass: two passes cost twice the scan."""
    counted = _CountedLoads(orjson.loads)
    monkeypatch.setattr(orjson, "loads", counted)
    records = Records(_array_of(10), _SOURCE)
    assert (counted.calls, len(records)) == (10, 10)
