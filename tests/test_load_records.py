"""What ``--data`` reads, file by file: the exact records, or a refusal naming the file."""

from __future__ import annotations

import json
import tracemalloc
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.label_io import LabelInputError, load_records
from datamax_dpl.composition import build_production
from datamax_dpl.domain.label import Label, Text

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner

_TWO = [{"a": "r0"}, {"a": "r1"}]
_BOM = b"\xef\xbb\xbf"

_READ = [
    pytest.param("d.csv", b"a\nr0\nr1\n", _TWO, id="csv"),
    pytest.param("d.json", b'[{"a":"r0"},{"a":"r1"}]', _TWO, id="json-array"),
    pytest.param("d.jsonl", b'{"a":"r0"}\n{"a":"r1"}\n', _TWO, id="json-lines"),
    pytest.param("d.csv", b"a\nr0\nr1", _TWO, id="csv-no-last-newline"),
    pytest.param("d.jsonl", b'{"a":"r0"}\n{"a":"r1"}', _TWO, id="json-lines-no-last-newline"),
    pytest.param("d.jsonl", b'{"a":"r0"}\n\n  \n{"a":"r1"}\n', _TWO, id="json-lines-blank-lines"),
    pytest.param("d.csv", _BOM + b"a\nr0\nr1\n", _TWO, id="csv-bom"),
    pytest.param("d.csv", b"a\nr0,extra\n", [{"a": "r0"}], id="csv-extra-column-dropped"),
    pytest.param("d.csv", b"a,b\nr0\n", [{"a": "r0", "b": ""}], id="csv-missing-column-empty"),
    pytest.param("d.json", b'{"a":"r0"}', [{"a": "r0"}], id="json-one-object"),
]

_REFUSED = [
    pytest.param("d.jsonl", b'{"a":"r0"}\nnot json\n', "line 2 is not JSON", id="bad-json-line"),
    pytest.param("d.jsonl", b'{"a":"r0"}\n[1]\n', "line 2 is not an object", id="json-line-not-object"),
    pytest.param("d.json", b'[{"a":"r0"}, 1]', "record 1 is not an object", id="array-item-not-object"),
    pytest.param("d.json", b"null", "record 0 is not an object", id="json-null"),
    pytest.param("d.csv", b"a\n\xff\xfe\n", "is not UTF-8", id="csv-not-utf8"),
    pytest.param("d.csv", b'a\n"r0\nr1\n', "is not valid CSV", id="csv-stray-quote"),
    pytest.param("d.csv", b"", "holds no data record", id="csv-empty"),
    pytest.param("d.csv", b"a\n", "holds no data record", id="csv-header-only"),
    pytest.param("d.json", b"", "holds no data record", id="json-empty"),
    pytest.param("d.json", b"[]", "holds no data record", id="json-empty-array"),
    pytest.param("d.jsonl", b"\n\n", "holds no data record", id="json-lines-blank-only"),
    # The skill tells users only a CSV file may start with a BOM; these pin the JSON half of that.
    pytest.param("d.jsonl", _BOM + b'{"a":"r0"}\n', "line 1 is not JSON: UTF-8 byte order mark", id="json-lines-bom"),
    pytest.param("d.json", _BOM + b'[{"a":"r0"}]', "line 1 is not JSON: UTF-8 byte order mark", id="json-array-bom"),
]


def _data(tmp_path: Path, name: str, content: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("name", "content", "records"), _READ)
def test_a_file_gives_exactly_these_records(
    tmp_path: Path, name: str, content: bytes, records: list[dict[str, str]]
) -> None:
    loaded = load_records(_data(tmp_path, name, content))
    assert list(loaded) == records
    assert len(loaded) == len(records)
    assert list(loaded) == records, "a second read gives the same"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("name", "content", "records"), _READ)
def test_the_command_reads_the_same_records(
    cli_runner: CliRunner, tmp_path: Path, name: str, content: bytes, records: list[dict[str, str]]
) -> None:
    label = tmp_path / "label.json"
    label.write_text(Label(elements=[Text(x=10, y=10, text="{a}")]).model_dump_json(), encoding="utf-8")
    args = ["--json-bare", "validate", str(label), "--data", _data(tmp_path, name, content), "--model", "dmx-600"]
    result = cli_runner.invoke(cli_mod.cli, args, obj=build_production)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["records"] == len(records)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("name", "content", "says"), _REFUSED)
def test_a_bad_file_is_refused_naming_what_is_wrong(tmp_path: Path, name: str, content: bytes, says: str) -> None:
    source = _data(tmp_path, name, content)
    with pytest.raises(LabelInputError, match=says) as refused:
        load_records(source)
    assert source in str(refused.value)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("name", "content", "says"), _REFUSED)
def test_the_command_refuses_it_with_exit_2_and_no_traceback(
    cli_runner: CliRunner, tmp_path: Path, name: str, content: bytes, says: str
) -> None:
    label = tmp_path / "label.json"
    label.write_text(Label(elements=[Text(x=10, y=10, text="{a}")]).model_dump_json(), encoding="utf-8")
    args = ["--json-bare", "validate", str(label), "--data", _data(tmp_path, name, content), "--model", "dmx-600"]
    result = cli_runner.invoke(cli_mod.cli, args, obj=build_production)
    assert result.exit_code == 2, result.output
    answer = json.loads(result.stdout)
    assert answer["error"] == "LabelInputError"
    assert says in answer["message"]


@pytest.mark.os_agnostic
def test_a_json_null_value_reads_as_empty(tmp_path: Path) -> None:
    assert list(load_records(_data(tmp_path, "d.jsonl", b'{"a":null}\n'))) == [{"a": ""}]


def _peak_of_a_read(tmp_path: Path, name: str, rows: int) -> int:
    source = tmp_path / f"{rows}-{name}"
    if name.endswith(".csv"):
        source.write_bytes(b"a\n" + b"".join(b"r%d\n" % row for row in range(rows)))
    else:
        source.write_bytes(b"".join(b'{"a":"r%d"}\n' % row for row in range(rows)))
    records = load_records(str(source))
    tracemalloc.start()
    try:
        # Each record goes as soon as the next arrives: a parser that collected them would hold them all.
        for _ in records:
            pass
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


#: What one read of the records may peak at, whatever their number: its buffers, not its rows.
_MOST_A_READ_HOLDS = 256 * 1024


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["d.csv", "d.jsonl"])
def test_reading_the_records_holds_none_of_them(tmp_path: Path, name: str) -> None:
    few, many = _peak_of_a_read(tmp_path, name, 5000), _peak_of_a_read(tmp_path, name, 50000)
    assert many < _MOST_A_READ_HOLDS, (few, many)
    assert many < 2 * few + 16 * 1024, (few, many)
