"""The limit on the bytes of a batch's job, and the job joined only when it is read."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamax_dpl import BatchTooLargeError
from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.settings import PrinterSettings
from datamax_dpl.adapters.memory.printer import RecordingPrinter, open_recording_printer
from datamax_dpl.application.use_cases import BatchResult, print_result, render_batch
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.label import Label, Qr, Text
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from click.testing import CliRunner

_DMX = RenderOptions(model="dmx-600")
_TOO_LONG_FOR_A_QR_CODE = "A" * 4000


def _one_field_records(count: int, *, seen: list[int]) -> Iterator[Mapping[str, str]]:
    for number in range(count):
        seen.append(number)
        yield {"a": str(number)}


@pytest.mark.os_agnostic
def test_render_batch_stops_at_the_limit_without_rendering_the_rest() -> None:
    label = Label(elements=[Text(x=10, y=10, text="{a}")])
    one = len(render_batch(label, [{"a": "0"}], _DMX).data)
    seen: list[int] = []
    with pytest.raises(BatchTooLargeError, match=r"at data record 2\b"):
        render_batch(label, _one_field_records(1000, seen=seen), _DMX, max_job_bytes=2 * one + 1)
    # The record that passed the limit, and the one read ahead to know whether it was the last.
    assert seen == [0, 1, 2, 3]


@pytest.mark.os_agnostic
def test_a_batch_of_exactly_the_limit_goes_through() -> None:
    label = Label(elements=[Text(x=10, y=10, text="{a}")])
    one = len(render_batch(label, [{"a": "0"}], _DMX).data)
    batch = render_batch(label, [{"a": "0"}, {"a": "1"}], _DMX, max_job_bytes=2 * one)
    assert batch.job_bytes == 2 * one


@pytest.mark.os_agnostic
def test_a_library_caller_sets_no_limit_by_default() -> None:
    label = Label(elements=[Text(x=10, y=10, text="{a}")])
    assert render_batch(label, [{"a": "0"}] * 3, _DMX).ok


@pytest.mark.os_agnostic
def test_render_holding_the_job_refuses_a_batch_past_the_key_naming_it(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = cli_runner.invoke(cli_mod.cli, _limited(tmp_path, "render"), obj=build_testing)
    assert result.exit_code == 2, result.output
    document = json.loads(result.stdout)
    assert document["error"] == "BatchTooLargeError"
    assert "[printer] max_batch_job_bytes" in document["message"]
    assert "-o FILE" in document["message"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("validate", ()),
        ("render", ("-o", "{target}")),
        ("print", ("--printer", "file:{target}")),
    ],
)
def test_a_command_holding_no_job_is_not_bound_by_the_key(
    cli_runner: CliRunner, tmp_path: Path, command: str, extra: tuple[str, ...]
) -> None:
    target = tmp_path / "job.dpl"
    args = [*_limited(tmp_path, command), *(part.format(target=target) for part in extra)]
    result = cli_runner.invoke(cli_mod.cli, args, obj=build_testing)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["records"] == 50
    if command == "render":
        assert target.stat().st_size > 100


def _limited(tmp_path: Path, command: str) -> list[str]:
    """A command line over 50 records, whose job is far past a 100-byte ``max_batch_job_bytes``."""
    label = tmp_path / "label.json"
    label.write_text(Label(elements=[Text(x=10, y=10, text="{a}")]).model_dump_json(), encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_text("a\n" + "x\n" * 50, encoding="utf-8")
    limit = ["--json-bare", "--set", "printer.max_batch_job_bytes=100"]
    return [*limit, command, str(label), "--data", str(data), "--model", "dmx-600"]


@pytest.mark.os_agnostic
def test_the_default_limit_is_64_mib_and_says_nothing_of_a_process_size() -> None:
    """1 GiB rested on "100000 one-field records come to about 430 MB": that job is 6-7 MB."""
    assert PrinterSettings().max_batch_job_bytes == 64 * 1024 * 1024


@pytest.mark.os_agnostic
def test_a_refused_render_shows_no_job(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The help says nothing is written then: a caller piping the base64 must get none."""
    label = tmp_path / "label.json"
    label.write_text(Label(elements=[Qr(x=1, y=1, data="{a}")]).model_dump_json(), encoding="utf-8")
    data = tmp_path / "data.csv"
    # Past what a QR Code holds: an error finding on the second record, not an exception.
    data.write_text(f"a\nfine\n{_TOO_LONG_FOR_A_QR_CODE}\n", encoding="utf-8")
    args = ["render", str(label), "--data", str(data), "--model", "allegro-2"]
    bare = cli_runner.invoke(cli_mod.cli, ["--json-bare", *args], obj=build_testing)
    assert bare.exit_code == 1, bare.output
    document = json.loads(bare.stdout)["data"]
    assert (document["dpl"], document["dpl_base64"]) == (None, None)
    human = cli_runner.invoke(cli_mod.cli, args, obj=build_testing)
    assert human.exit_code == 1, human.output
    assert "<CR>" not in human.stdout


@pytest.mark.os_agnostic
def test_a_refused_single_label_shows_no_job(cli_runner: CliRunner, tmp_path: Path) -> None:
    label = tmp_path / "label.json"
    label.write_text(Label(elements=[Qr(x=1, y=1, data=_TOO_LONG_FOR_A_QR_CODE)]).model_dump_json(), encoding="utf-8")
    args = ["render", str(label), "--model", "allegro-2"]
    bare = cli_runner.invoke(cli_mod.cli, ["--json-bare", *args], obj=build_testing)
    assert bare.exit_code == 1, bare.output
    document = json.loads(bare.stdout)["data"]
    assert (document["dpl"], document["dpl_base64"]) == (None, None)
    assert "<CR>" not in cli_runner.invoke(cli_mod.cli, args, obj=build_testing).stdout


@pytest.mark.os_agnostic
def test_a_batch_sent_one_way_needs_no_joined_copy() -> None:
    """``data`` is joined on the first read; the send and the byte count never read it."""
    label = Label(elements=[Text(x=10, y=10, text="{a}")])
    batch = render_batch(label, [{"a": "0"}, {"a": "1"}], _DMX)
    printer = RecordingPrinter()
    report = print_result(batch, uri="file:x", timeout=1.0, open_printer=open_recording_printer(printer))
    assert report.bytes_sent == batch.job_bytes
    assert "data" not in batch.__dict__
    assert b"".join(printer.sent) == batch.data


@pytest.mark.os_agnostic
def test_a_batch_holds_its_joined_job_only_through_its_results() -> None:
    """Dumping it must not join a job the size of the limit, and JSON still rebuilds the same bytes."""
    label = Label(elements=[Text(x=10, y=10, text="{a}")])
    batch = render_batch(label, [{"a": "0"}], _DMX)
    assert "data" not in batch.model_dump()
    assert "data" not in batch.__dict__
    assert BatchResult.model_validate_json(batch.model_dump_json()).data == batch.data
    assert batch == BatchResult(results=batch.results)
