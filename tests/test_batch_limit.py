"""The limit on the bytes of a batch's job, and the job joined only when it is read."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamax_dpl import BatchTooLargeError
from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory.printer import RecordingPrinter, open_recording_printer
from datamax_dpl.application.use_cases import BatchResult, print_result, render_batch
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.label import Label, Text
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from click.testing import CliRunner

_DMX = RenderOptions(model="dmx-600")


def _one_field_records(count: int, *, seen: list[int]) -> Iterator[Mapping[str, str]]:
    for number in range(count):
        seen.append(number)
        yield {"a": str(number)}


@pytest.mark.os_agnostic
def test_render_batch_stops_at_the_limit_without_rendering_the_rest() -> None:
    label = Label(elements=[Text(x=10, y=10, text="{a}")])
    one = len(render_batch(label, [{"a": "0"}], _DMX).data)
    seen: list[int] = []
    with pytest.raises(BatchTooLargeError, match=r"at data record 3"):
        render_batch(label, _one_field_records(1000, seen=seen), _DMX, max_job_bytes=2 * one + 1)
    assert seen == [0, 1, 2]


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
@pytest.mark.parametrize("command", ["validate", "render", "print"])
def test_the_command_line_refuses_a_batch_past_the_key_naming_it(
    cli_runner: CliRunner, tmp_path: Path, command: str
) -> None:
    label = tmp_path / "label.json"
    label.write_text(Label(elements=[Text(x=10, y=10, text="{a}")]).model_dump_json(), encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_text("a\n" + "x\n" * 50, encoding="utf-8")
    target = tmp_path / "job.dpl"
    printer = ["--printer", f"file:{target}"] if command == "print" else []
    args = ["--json-bare", "--set", "printer.max_batch_job_bytes=100", command, str(label), "--data", str(data)]
    result = cli_runner.invoke(cli_mod.cli, [*args, "--model", "dmx-600", *printer], obj=build_testing)
    assert result.exit_code == 2, result.output
    document = json.loads(result.stdout)
    assert document["error"] == "BatchTooLargeError"
    assert "[printer] max_batch_job_bytes" in document["message"]
    assert not target.exists()


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
