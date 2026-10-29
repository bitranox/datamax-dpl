"""CLI --set override integration tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from datamax_dpl.adapters import cli as cli_mod
from datamax_dpl.adapters.cli.settings import PrinterSettings, render_options
from datamax_dpl.domain.enums import CutterPresence, HardwareOption

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner, Result


@pytest.mark.os_agnostic
def test_when_set_override_is_passed_config_reflects_change(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
) -> None:
    """Verify --set override is visible in config command output."""
    factory = config_cli_context(
        {
            "lib_log_rich": {
                "console_level": "INFO",
            }
        }
    )

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "lib_log_rich.console_level=DEBUG", "config", "--section", "lib_log_rich"],
        obj=factory,
    )

    assert result.exit_code == 0
    assert "DEBUG" in result.output


@pytest.mark.os_agnostic
def test_when_multiple_set_overrides_are_passed_all_apply(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
) -> None:
    """Verify multiple --set options all apply."""
    factory = config_cli_context(
        {
            "lib_log_rich": {
                "console_level": "INFO",
                "force_color": False,
            }
        }
    )

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        [
            "--set",
            "lib_log_rich.console_level=DEBUG",
            "--set",
            "lib_log_rich.force_color=true",
            "config",
            "--section",
            "lib_log_rich",
        ],
        obj=factory,
    )

    assert result.exit_code == 0
    assert "DEBUG" in result.output


@pytest.mark.os_agnostic
def test_when_set_override_has_nested_key_it_works(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
) -> None:
    """Verify nested key override (e.g., SECTION.SUB.KEY=VALUE) works."""
    factory = config_cli_context(
        {
            "lib_log_rich": {
                "payload_limits": {
                    "message_max_chars": 4096,
                },
            }
        }
    )

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "lib_log_rich.payload_limits.message_max_chars=8192", "config", "--format", "json"],
        obj=factory,
    )

    assert result.exit_code == 0
    assert "8192" in result.stdout


@pytest.mark.os_agnostic
def test_when_set_override_is_invalid_it_shows_usage_error(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify invalid --set format shows usage error."""
    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "invalid_no_equals", "config"],
        obj=production_factory,
    )

    assert result.exit_code != 0
    assert "must contain '='" in result.output or "must contain '='" in (result.stderr or "")


@pytest.mark.os_agnostic
def test_when_set_override_has_no_dot_it_shows_usage_error(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify --set without dot in key shows usage error."""
    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "nodot=value", "config"],
        obj=production_factory,
    )

    assert result.exit_code != 0


@pytest.mark.os_agnostic
def test_when_set_override_is_empty_string_it_shows_error(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Verify --set with empty string shows error."""
    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "", "config"],
        obj=production_factory,
    )

    assert result.exit_code != 0


@pytest.mark.os_agnostic
def test_when_no_set_overrides_config_is_unchanged(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
) -> None:
    """Verify no --set leaves config unchanged."""
    factory = config_cli_context(
        {
            "lib_log_rich": {
                "console_level": "WARNING",
            }
        }
    )

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["config", "--section", "lib_log_rich"],
        obj=factory,
    )

    assert result.exit_code == 0
    assert "WARNING" in result.output


# --------------------------------------------------------------------------
# Printer values: cutter and installed options are closed sets, refused at the boundary
# --------------------------------------------------------------------------

_LABEL = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "Hello"}]}


def _validate(
    cli_runner: CliRunner, factory: Callable[[], Any], tmp_path: Path, *extra: str
) -> tuple[int, dict[str, Any]]:
    label = tmp_path / "label.json"
    label.write_text(json.dumps(_LABEL), encoding="utf-8")
    result = cli_runner.invoke(cli_mod.cli, ["--json", "validate", str(label), *extra], obj=factory)
    return result.exit_code, json.loads(result.stdout)


@pytest.mark.os_agnostic
def test_an_unknown_configured_option_is_a_configuration_error(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    factory = config_cli_context({"printer": {"options": ["laser"]}})

    exit_code, envelope = _validate(cli_runner, factory, tmp_path)

    assert exit_code == 2
    assert envelope["error"] == "ConfigurationError"
    assert envelope["message"] == "printer.options.0: Input should be 'datamatrix'"


@pytest.mark.os_agnostic
def test_an_unknown_option_flag_is_a_configuration_error_in_the_asked_shape(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    exit_code, envelope = _validate(cli_runner, config_cli_context({}), tmp_path, "--option", "laser")

    assert exit_code == 2
    assert envelope == {
        "ok": False,
        "command": "validate",
        "error": "ConfigurationError",
        "message": "--option: Input should be 'datamatrix', got 'laser'",
    }


@pytest.mark.os_agnostic
def test_a_known_option_flag_is_accepted_beside_a_configured_one(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    factory = config_cli_context({"printer": {"options": ["datamatrix"]}})

    exit_code, envelope = _validate(cli_runner, factory, tmp_path, "--option", "datamatrix")

    assert exit_code == 0, envelope
    assert envelope["ok"] is True


@pytest.mark.os_agnostic
def test_an_unknown_configured_cutter_names_the_key_and_the_choices(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    exit_code, envelope = _validate(cli_runner, config_cli_context({"printer": {"cutter": "maybe"}}), tmp_path)

    assert exit_code == 2
    assert envelope["message"] == "printer.cutter: Input should be 'yes', 'no' or 'unknown'"


@pytest.mark.os_agnostic
def test_an_unknown_cutter_flag_is_a_usage_error_listing_the_choices(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    label = tmp_path / "label.json"
    label.write_text(json.dumps(_LABEL), encoding="utf-8")

    result = cli_runner.invoke(cli_mod.cli, ["validate", str(label), "--cutter", "maybe"], obj=config_cli_context({}))

    assert result.exit_code == 2
    assert "'maybe' is not one of 'yes', 'no', 'unknown'" in result.output


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("cutter", "installed"),
    [(CutterPresence.YES, True), (CutterPresence.NO, False), (CutterPresence.UNKNOWN, None)],
)
def test_every_cutter_presence_maps_to_render_options(cutter: CutterPresence, installed: bool | None) -> None:
    assert render_options(PrinterSettings(cutter=cutter)).cutter_installed is installed


@pytest.mark.os_agnostic
def test_configured_options_reach_render_options_as_members() -> None:
    settings = PrinterSettings.model_validate({"options": ["datamatrix"]})

    assert render_options(settings).options == frozenset({HardwareOption.DATAMATRIX})


_VERTICAL_LINE = {"elements": [{"type": "line", "x": 10, "y": 10, "x2": 10, "y2": 110, "thickness": 0.5}]}


def _render_dpl(cli_runner: CliRunner, factory: Callable[[], Any], tmp_path: Path, *extra: str) -> str:
    label = tmp_path / "line.json"
    label.write_text(json.dumps(_VERTICAL_LINE), encoding="utf-8")
    result = cli_runner.invoke(cli_mod.cli, [*extra, "--json", "render", str(label), "--model", "allegro"], obj=factory)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["data"]["dpl"]


@pytest.mark.os_agnostic
def test_a_configured_feed_scale_reaches_the_rendered_rows(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    plain = _render_dpl(cli_runner, config_cli_context({}), tmp_path)
    scaled = _render_dpl(cli_runner, config_cli_context({"printer": {"scale_along": 0.8}}), tmp_path)

    assert "1X1100000390039L002394" in plain
    assert "1X1100000490039L002492" in scaled


@pytest.mark.os_agnostic
def test_a_set_override_of_the_feed_scale_reaches_the_rendered_rows(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    scaled = _render_dpl(cli_runner, config_cli_context({}), tmp_path, "--set", "printer.scale_along=0.8")

    assert "1X1100000490039L002492" in scaled


@pytest.mark.os_agnostic
def test_a_configured_scale_outside_the_range_names_the_key(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    exit_code, envelope = _validate(cli_runner, config_cli_context({"printer": {"scale_across": 3}}), tmp_path)

    assert exit_code == 2
    assert envelope["message"] == "printer.scale_across: Input should be less than or equal to 1.5"


@pytest.mark.os_agnostic
def test_both_configured_scales_reach_render_options() -> None:
    options = render_options(PrinterSettings(scale_across=0.99, scale_along=0.977))

    assert (options.scale_across, options.scale_along) == (0.99, 0.977)


# ======================== named printers ========================


def _lager(**extra: Any) -> dict[str, Any]:
    # offset_along 0.0 keeps the Allegro 2's measured -0.25 mm out of the row arithmetic below
    return {"printers": {"lager": {"model": "allegro-2", "scale_along": 0.8, "offset_along": 0.0, **extra}}}


def _render_named(cli_runner: CliRunner, factory: Callable[[], Any], tmp_path: Path, *args: str) -> Result:
    label = tmp_path / "line.json"
    label.write_text(json.dumps(_VERTICAL_LINE), encoding="utf-8")
    return cli_runner.invoke(cli_mod.cli, ["--json", "render", str(label), *args], obj=factory)


@pytest.mark.os_agnostic
def test_a_named_printer_supplies_its_model_and_scale_to_render(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    result = _render_named(cli_runner, config_cli_context(_lager()), tmp_path, "--printer", "lager")

    data = json.loads(result.stdout)["data"]
    assert result.exit_code == 0, result.output
    assert data["model"] == "allegro-2"
    # whole-dot unit (10 mm = 40 units) divided by the 0.8 feed scale: row 50, length 500
    assert "1X1100000500040L002500" in data["dpl"]


@pytest.mark.os_agnostic
def test_a_named_printer_sends_to_its_own_uri(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    target = tmp_path / "out.dpl"
    factory = config_cli_context(_lager(uri=f"file:{target}"))
    label = tmp_path / "line.json"
    label.write_text(json.dumps(_VERTICAL_LINE), encoding="utf-8")

    result = cli_runner.invoke(cli_mod.cli, ["--json", "print", str(label), "--printer", "lager"], obj=factory)

    assert result.exit_code == 0, result.output
    assert b"1X1100000500040L002500" in target.read_bytes()


@pytest.mark.os_agnostic
def test_a_named_printer_inherits_what_it_does_not_set_from_the_printer_section(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    factory = config_cli_context({"printer": {"policy": "strict"}, **_lager()})
    label = tmp_path / "cut.json"
    label.write_text(json.dumps({"elements": [{"type": "circle", "x": 20, "y": 20, "radius": 5}]}), encoding="utf-8")

    result = cli_runner.invoke(cli_mod.cli, ["--json", "validate", str(label), "--printer", "lager"], obj=factory)

    # strict comes from [printer]; the allegro-2 has no circle command, so strict refuses it
    assert result.exit_code == 1, result.output


@pytest.mark.os_agnostic
def test_a_command_line_model_beats_the_named_printer(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    result = _render_named(
        cli_runner, config_cli_context(_lager()), tmp_path, "--printer", "lager", "--model", "allegro"
    )

    assert json.loads(result.stdout)["data"]["model"] == "allegro"


@pytest.mark.os_agnostic
def test_an_unknown_printer_name_lists_the_configured_ones(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    result = _render_named(cli_runner, config_cli_context(_lager()), tmp_path, "--printer", "keller")

    envelope = json.loads(result.stdout)
    assert result.exit_code == 2
    assert envelope["error"] == "ConfigurationError"
    assert envelope["message"] == "no printer named 'keller' in [printers]; configured: lager"


@pytest.mark.os_agnostic
def test_a_printer_section_with_an_invalid_name_is_refused(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    factory = config_cli_context({"printers": {"a:b": {"model": "allegro"}}})

    result = _render_named(cli_runner, factory, tmp_path, "--printer", "lager")

    envelope = json.loads(result.stdout)
    assert result.exit_code == 2
    assert "printers.a:b" in envelope["message"]


@pytest.mark.os_agnostic
def test_a_printer_uri_still_sets_the_address(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
) -> None:
    target = tmp_path / "direct.dpl"
    label = tmp_path / "line.json"
    label.write_text(json.dumps(_VERTICAL_LINE), encoding="utf-8")

    result = cli_runner.invoke(
        cli_mod.cli,
        ["--json", "print", str(label), "--model", "allegro", "--printer", f"file:{target}"],
        obj=config_cli_context(_lager()),
    )

    assert result.exit_code == 0, result.output
    assert b"1X1100000390039L002394" in target.read_bytes()
