"""What a batch's answer keeps past its limit, and what each view of it says it left out."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from printer_server import ImageModules, module_printer

from datamax_dpl import ReportLimits, check_batch
from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.composition import build_production
from datamax_dpl.domain.label import Cut, Label, Text
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner, Result
    from printer_server import PrinterServer

_ALLEGRO = RenderOptions(model="allegro-2")
_ROWS = 40
_LABEL = Label(elements=[Text(x=10, y=10, text="{a}")])
#: The record past the limit that prints something else than its data: its warning is what a reader needs.
_UNMAPPABLE = "中"
_WARNING = "characters_unmappable"
_LIMIT = "printer.max_reported_findings=3"


def _batch(tmp_path: Path) -> tuple[str, str]:
    label = tmp_path / "label.json"
    label.write_text(_LABEL.model_dump_json(), encoding="utf-8")
    data = tmp_path / "data.csv"
    rows = "".join(f"r{number}\n" for number in range(_ROWS)) + f"{_UNMAPPABLE}\n"
    data.write_bytes(f"a\n{rows}".encode())
    return str(label), str(data)


def _run(cli_runner: CliRunner, *args: str) -> Result:
    return cli_runner.invoke(cli_mod.cli, ["--set", _LIMIT, *args], obj=build_production)


@pytest.mark.os_agnostic
def test_past_the_limit_the_first_finding_of_each_code_is_kept() -> None:
    rows = [*({"a": f"r{number}"} for number in range(_ROWS)), {"a": _UNMAPPABLE}, {"a": _UNMAPPABLE}]
    report = check_batch(_LABEL, rows, _ALLEGRO, limits=ReportLimits(findings=3))
    kept = [item.finding.code.value for item in report.findings]
    assert sorted(set(kept)) == sorted(code.value for code in report.finding_counts)
    assert kept.count(_WARNING) == 1, "one of each past the limit, not every one"
    warning = next(item for item in report.findings if item.finding.code.value == _WARNING)
    assert warning.record == _ROWS
    assert len(kept) == len(set(kept)), "the limit filled with the first record's codes, each kept once"


@pytest.mark.os_agnostic
def test_the_findings_kept_are_bounded_by_the_codes_however_many_records() -> None:
    def kept(count: int) -> int:
        rows = [{"a": _UNMAPPABLE} for _ in range(count)]
        return len(check_batch(_LABEL, rows, _ALLEGRO, limits=ReportLimits(findings=1)).findings)

    assert kept(5) == kept(50)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "command",
    [
        pytest.param(("validate",), id="validate"),
        pytest.param(("render", "-o", "{out}"), id="render-file"),
        pytest.param(("print", "--printer", "file:{out}"), id="print"),
    ],
)
def test_a_warning_past_the_limit_reaches_a_person(
    cli_runner: CliRunner, tmp_path: Path, command: tuple[str, ...]
) -> None:
    """Allegro 2 gives three infos per record: the limit was full before the record that prints wrong."""
    label, data = _batch(tmp_path)
    verb, *rest = [word.format(out=tmp_path / "job.dpl") for word in command]
    result = _run(cli_runner, verb, label, "--data", data, "--model", "allegro-2", *rest)
    assert result.exit_code == 0, result.output
    assert f"WARNING {_WARNING} [record {_ROWS}]" in result.stdout


@pytest.mark.os_agnostic
def test_a_cut_prints_the_counts_per_code_in_human_mode(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _batch(tmp_path)
    result = _run(cli_runner, "validate", label, "--data", data, "--model", "allegro-2")
    assert f"{_WARNING} 1" in result.stdout
    assert f"inch_units {_ROWS + 1}" in result.stdout


@pytest.mark.os_agnostic
def test_a_filtered_view_says_what_the_answer_keeps_not_what_it_shows(cli_runner: CliRunner, tmp_path: Path) -> None:
    """``print`` shows one line of warnings: "4 of 124 findings shown" above it would be false."""
    label, data = _batch(tmp_path)
    target = tmp_path / "job.dpl"
    result = _run(cli_runner, "print", label, "--data", data, "--model", "allegro-2", "--printer", f"file:{target}")
    assert "findings shown" not in result.stdout
    bare = _run(cli_runner, "--json-bare", "validate", label, "--data", data, "--model", "allegro-2")
    payload = json.loads(bare.stdout)["data"]
    assert f"the answer keeps {len(payload['findings'])} of {payload['findings_total']} findings" in result.stdout


@pytest.mark.os_agnostic
def test_render_to_stdout_tells_warnings_and_errors_not_infos(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _batch(tmp_path)
    result = _run(cli_runner, "render", label, "--data", data, "--model", "allegro-2", "-o", "-")
    assert result.exit_code == 0, result.output
    assert f"WARNING {_WARNING}" in result.stderr
    assert "INFO" not in result.stderr


@pytest.mark.os_agnostic
def test_the_cap_holds_on_the_send_pass_over_tcp_in_both_modes(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[..., PrinterServer]
) -> None:
    """The report the answer shows comes from the send pass there, under the same configured keys."""
    label, data = _batch(tmp_path)
    limits = ("--set", _LIMIT, "--set", "printer.max_reported_downloads=1")

    def answer(*modes: str) -> Result:
        server = serve(module_printer(ImageModules()), next_connection_within=0.1)
        args = ["print", label, "--data", data, "--model", "allegro-2", "--printer", server.address]
        result = cli_runner.invoke(cli_mod.cli, [*modes, *limits, *args], obj=build_production)
        server.join()
        return result

    bare = answer("--json-bare")
    human = answer()
    assert bare.exit_code == 0, bare.output
    payload = json.loads(bare.stdout)["data"]
    assert len(payload["findings"]) < payload["findings_total"]
    assert sum(payload["findings_by_code"].values()) == payload["findings_total"]
    assert "[printer] max_reported_findings" in payload["report_note"]
    assert human.exit_code == 0, human.output
    assert "the answer keeps" in human.stdout
    assert "[printer] max_reported_findings" in human.stdout


@pytest.mark.os_agnostic
def test_an_envelope_with_only_the_batch_s_own_findings_has_no_note(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The batch's findings always stay, so they count as kept."""
    label = tmp_path / "label.json"
    label.write_text(Label(elements=[Text(x=10, y=10, text="{a}")], cut=Cut(every=2)).model_dump_json(), encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_bytes(b"a\nr0\nr1\n")
    args = ["--json-bare", "validate", str(label), "--data", str(data), "--model", "dmx-800"]
    payload = json.loads(cli_runner.invoke(cli_mod.cli, args, obj=build_production).stdout)["data"]
    assert payload["findings_total"] == len(payload["findings"]) > 0
    assert payload["report_note"] is None
