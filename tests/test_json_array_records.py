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

from datamax_dpl.adapters.cli.label_io import LabelInputError, Records, load_records

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_SOURCE = "d.json"

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
        "line 1 is not JSON: depth limit exceeded: line 1 column 1026 (char 1025)",
        id="one-level-too-deep",
    ),
    pytest.param(b"[" * 1024 + b"]" * 1024, "record 0 is not an object", id="deepest-taken"),
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


def _array_of(rows: int) -> bytes:
    return b"[" + b",".join(b'{"a":"r%d"}' % row for row in range(rows)) + b"]"


def _peak_of_records(rows: int) -> int:
    data = _array_of(rows)
    tracemalloc.start()
    try:
        # Counting at construction and one read after: a parser that built the list would hold every record.
        for _ in Records(data, _SOURCE):
            pass
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


#: What reading an array may peak at above the bytes it holds, whatever its records: one record and
#: the scan's own state, not the rows. A whole parse held 12 MB for the 50000 records measured here.
_MOST_A_READ_HOLDS = 256 * 1024


@pytest.mark.os_agnostic
def test_reading_an_array_holds_none_of_its_records() -> None:
    few, many = _peak_of_records(5000), _peak_of_records(50000)
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
#: the element's bytes once more and its parsed text. A backtracking mark per escape in the one
#: string took about 120 bytes an escape: 242 MB for the 4 MB file measured here.
_MOST_FILES_A_READ_RAISES = 2
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
