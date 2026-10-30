"""scripts/qr_batch.py writes a QR Code label and unique codes; its print command sends each record's code."""

from __future__ import annotations

import csv
import json
import re
import runpy
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from printer_server import silent

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.composition import build_production
from datamax_dpl.domain.enums import Severity
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner
    from printer_server import Play, PrinterServer

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "qr_batch.py"
_PRINTER = "tcp://192.0.2.60:9100"
_CODE = re.compile(r"DMX-130-([0-9]{2})-[A-Z0-9]{6}")

pytestmark = pytest.mark.usefixtures("no_ambient_configuration")


def _main() -> Callable[[list[str]], int]:
    """The script's ``main``, loaded from its file the way a maintainer runs it."""
    namespace = runpy.run_path(str(_SCRIPT), run_name="qr_batch")
    main: Callable[[list[str]], int] = namespace["main"]
    return main


def _run(tmp_path: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[dict[str, Any], list[str]]:
    """Run the script; return its JSON line and the lines after it."""
    assert _main()(["--out", str(tmp_path / "qr"), "--printer", _PRINTER, *extra]) == 0
    first, *rest = capsys.readouterr().out.splitlines()
    return cast("dict[str, Any]", json.loads(first)), rest


def _rows(summary: dict[str, Any]) -> list[dict[str, str]]:
    with Path(summary["rows"]).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _literal_label(summary: dict[str, Any], row: dict[str, str]) -> Label:
    """The batch label written out for one row by hand: its code and number in place of the placeholders."""
    document = json.loads(Path(summary["label"]).read_text(encoding="utf-8"))
    qr, text = document["elements"]
    document["elements"] = [{**qr, "data": row["code"]}, {**text, "text": row["n"]}]
    return Label.model_validate(document)


@pytest.mark.os_agnostic
def test_the_rows_hold_count_unique_codes_numbered_from_01(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    summary, lines = _run(tmp_path, capsys, "--count", "20", "--seed", "3")
    rows = _rows(summary)

    assert [row["n"] for row in rows] == [f"{number:02d}" for number in range(1, 21)]
    assert len({row["code"] for row in rows}) == 20
    for row in rows:
        match = _CODE.fullmatch(row["code"])
        assert match is not None
        assert match.group(1) == row["n"]
    assert summary["codes"] == [row["code"] for row in rows]
    assert lines[:20] == [f"{row['n']} {row['code']}" for row in rows]


@pytest.mark.os_agnostic
def test_a_seed_repeats_its_codes_and_another_seed_draws_others(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first, _ = _run(tmp_path / "a", capsys, "--count", "5", "--seed", "11")
    again, _ = _run(tmp_path / "b", capsys, "--count", "5", "--seed", "11")
    other, _ = _run(tmp_path / "c", capsys, "--count", "5", "--seed", "12")

    assert (first["seed"], first["codes"]) == (again["seed"], again["codes"])
    assert set(first["codes"]).isdisjoint(other["codes"])


@pytest.mark.os_agnostic
def test_the_printed_command_sends_each_record_with_its_own_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    cli_runner: CliRunner,
    serve: Callable[[Play], PrinterServer],
) -> None:
    """Each record's job equals the label written with that code by hand, in the order of the rows."""
    server = serve(silent)
    summary, lines = _run(tmp_path, capsys, "--count", "4", "--printer", server.address)
    argv: list[str] = summary["argv"]
    if sys.platform != "win32":
        assert shlex.split(lines[-1]) == argv

    result = cli_runner.invoke(cli_mod.cli, argv[1:], obj=build_production)
    server.join()

    assert result.exit_code == 0, result.output
    options = RenderOptions(model="allegro-2")
    jobs = [build_job(_literal_label(summary, row), options, filled=True) for row in _rows(summary)]
    assert bytes(server.received) == b"".join(job.data for job in jobs)
    assert len({job.downloads for job in jobs}) == 4
    for job in jobs:
        assert {finding.severity for finding in job.findings} == {Severity.INFO}


@pytest.mark.os_agnostic
def test_a_count_past_two_digits_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _main()(["--out", str(tmp_path), "--printer", _PRINTER, "--count", "100"]) == 2
    assert "--count must be 1 to 99" in capsys.readouterr().err


@pytest.mark.os_agnostic
def test_the_printer_must_be_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The address only goes into the printed command; a forgotten one must not fall back to anything."""
    with pytest.raises(SystemExit) as exited:
        _main()(["--out", str(tmp_path)])
    assert exited.value.code == 2
    assert "--printer" in capsys.readouterr().err
