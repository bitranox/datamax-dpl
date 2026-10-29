"""`calibrate print`: the two calibration labels, rendered with the chosen printer's settings and sent."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from datamax_dpl.adapters import cli as cli_mod

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner, Result

_FORMAT_START = b"\x02L"
#: The across frame on a 100 x 55 mm label in the Allegro 2's whole-dot unit at feed scale 0.977:
#: 100 mm across is 400 units, 55 mm along is 55 / 0.977 mm, 225 units.
_ALLEGRO_FRAME = b"B400225001001"


def _lager(target: Path, **extra: Any) -> dict[str, Any]:
    printer = {"model": "allegro-2", "uri": f"file:{target}", "label_width": 100, "label_height": 55}
    return {"printers": {"lager": {**printer, "scale_along": 0.977, **extra}}}


def _calibrate(cli_runner: CliRunner, factory: Callable[[], Any], *args: str) -> Result:
    return cli_runner.invoke(cli_mod.cli, ["--json", "calibrate", "print", *args], obj=factory)


@pytest.mark.os_agnostic
def test_both_labels_go_to_the_named_printer_in_one_send(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"

    result = _calibrate(cli_runner, config_cli_context(_lager(target)), "--printer", "lager")

    assert result.exit_code == 0, result.output
    sent = target.read_bytes()
    data = json.loads(result.stdout)["data"]
    assert sent.count(_FORMAT_START) == 2
    assert _ALLEGRO_FRAME in sent
    assert data["labels"] == ["across", "along"]
    assert data["name"] == "lager"
    assert data["bytes_sent"] == len(sent)


@pytest.mark.os_agnostic
def test_each_label_names_the_printer_and_the_factor_it_was_printed_with(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"

    _calibrate(cli_runner, config_cli_context(_lager(target, scale_across=0.99)), "--printer", "lager")

    across, along = target.read_bytes().split(_FORMAT_START)[1:]
    assert b"LAGER SCALE 0.9900" in across
    assert b"LAGER SCALE 0.9770" in along


@pytest.mark.os_agnostic
def test_the_printers_scale_shapes_the_along_bar(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    plain, scaled = tmp_path / "plain.dpl", tmp_path / "scaled.dpl"
    _calibrate(cli_runner, config_cli_context(_lager(plain, scale_along=1.0)), "--printer", "lager", "--axis", "along")
    _calibrate(cli_runner, config_cli_context(_lager(scaled, scale_along=0.9)), "--printer", "lager", "--axis", "along")

    # 0.5 mm thick, 200 mm long: 2 x 799 whole-dot units; divided by the 0.9 feed scale, 888 long
    assert b"L002799" in plain.read_bytes()
    assert b"L002888" in scaled.read_bytes()


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("axis", "formats"), [("across", 1), ("along", 1), ("both", 2)])
def test_axis_chooses_which_labels_are_sent(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
    axis: str,
    formats: int,
) -> None:
    target = tmp_path / "out.dpl"

    result = _calibrate(cli_runner, config_cli_context(_lager(target)), "--printer", "lager", "--axis", axis)

    assert result.exit_code == 0, result.output
    assert target.read_bytes().count(_FORMAT_START) == formats
    assert (_ALLEGRO_FRAME in target.read_bytes()) == (axis != "along")


@pytest.mark.os_agnostic
def test_size_options_beat_the_configured_label_size(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"

    args = ("--printer", "lager", "--axis", "across", "--width", "50", "--height", "30")
    _calibrate(cli_runner, config_cli_context(_lager(target)), *args)

    # 50 mm across is 200 units; 30 mm along at feed scale 0.977 is 123
    assert b"B200123001001" in target.read_bytes()


@pytest.mark.os_agnostic
def test_a_missing_label_size_names_the_keys_and_sends_nothing(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"
    config = {"printers": {"lager": {"model": "allegro-2", "uri": f"file:{target}"}}}

    result = _calibrate(cli_runner, config_cli_context(config), "--printer", "lager")

    envelope = json.loads(result.stdout)
    assert result.exit_code == 2
    assert "label_width" in envelope["message"]
    assert "label_height" in envelope["message"]
    assert not target.exists()


@pytest.mark.os_agnostic
def test_the_along_label_needs_only_the_width(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"
    config = {"printers": {"lager": {"model": "allegro-2", "uri": f"file:{target}", "label_width": 100}}}

    result = _calibrate(cli_runner, config_cli_context(config), "--printer", "lager", "--axis", "along")

    assert result.exit_code == 0, result.output


@pytest.mark.os_agnostic
def test_a_label_too_small_for_calibration_is_refused_with_exit_2(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"

    result = _calibrate(cli_runner, config_cli_context(_lager(target)), "--printer", "lager", "--width", "10")

    assert result.exit_code == 2
    assert "20 mm" in json.loads(result.stdout)["message"]
    assert not target.exists()


@pytest.mark.os_agnostic
def test_a_label_with_an_error_finding_is_refused_and_nothing_is_sent(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"

    # Ticks past the Allegro 2's 104 mm print width cannot print: an error finding in every policy.
    result = _calibrate(cli_runner, config_cli_context(_lager(target)), "--printer", "lager", "--width", "200")

    envelope = json.loads(result.stdout)
    assert result.exit_code == 1
    assert any(finding["severity"] == "error" for finding in envelope["data"]["findings"])
    assert envelope["data"]["bytes_sent"] == 0
    assert not target.exists()
