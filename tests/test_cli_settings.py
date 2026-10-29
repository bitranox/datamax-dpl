"""The printer settings' calibration and stop-position keys, from the config file to the rendered job."""

from __future__ import annotations

import json
from importlib import resources
from typing import TYPE_CHECKING, Any

import pytest
import tomlkit
from lib_layered_config import Config

from datamax_dpl.adapters import cli as cli_mod
from datamax_dpl.adapters.cli.settings import PrinterOverrides, printer_settings, render_options
from datamax_dpl.domain.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

    from datamax_dpl.domain.render_context import RenderOptions

_CALIBRATION_KEYS = ("scale_across", "scale_along", "offset_across", "offset_along")
_LINE = {"elements": [{"type": "line", "x": 10, "y": 10, "x2": 10, "y2": 110, "thickness": 0.5}]}


def _options(config: dict[str, Any], printer: str | None = None) -> RenderOptions:
    return render_options(printer_settings(Config(config, {}), PrinterOverrides(printer=printer)))


@pytest.mark.os_agnostic
def test_a_configured_stop_position_reaches_the_render_options() -> None:
    assert _options({"printer": {"stop_position": 67.4}}).stop_position_mm == 67.4


@pytest.mark.os_agnostic
def test_a_named_printers_cutter_stop_position_wins_over_the_printer_section() -> None:
    config = {"printer": {"cutter_stop_position": 50}, "printers": {"x": {"cutter_stop_position": 80}}}

    assert _options(config, printer="x").cutter_stop_position_mm == 80
    assert _options(config).cutter_stop_position_mm == 50


@pytest.mark.os_agnostic
def test_unset_calibration_and_stop_keys_leave_the_model_default_in_force() -> None:
    options = _options({"printer": {"model": "allegro-2"}})

    assert [getattr(options, key) for key in _CALIBRATION_KEYS] == [None] * 4
    assert (options.stop_position_mm, options.cutter_stop_position_mm) == (None, None)


@pytest.mark.os_agnostic
def test_a_scale_set_to_the_neutral_value_is_kept_and_not_taken_for_unset() -> None:
    assert _options({"printer": {"model": "allegro-2", "scale_along": 1.0}}).scale_along == 1.0


@pytest.mark.os_agnostic
def test_the_shipped_printer_defaults_set_no_calibration_or_stop_value() -> None:
    """A shipped ``scale_along = 1.0`` would be configured, and hide every model's measured default."""
    shipped = resources.files("datamax_dpl") / "adapters" / "config" / "defaultconfig.d" / "50-printer.toml"
    defaults: dict[str, Any] = tomlkit.parse(shipped.read_text(encoding="utf-8")).unwrap()

    options = _options(defaults)

    assert [getattr(options, key) for key in _CALIBRATION_KEYS] == [None] * 4
    assert (options.stop_position_mm, options.cutter_stop_position_mm) == (None, None)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", ["stop_position", "cutter_stop_position"])
def test_a_stop_position_of_zero_is_refused_with_the_key_named(key: str) -> None:
    with pytest.raises(ConfigurationError, match=rf"^printer\.{key}: Input should be greater than 0$"):
        _options({"printer": {key: 0}})


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", ["stop_position", "cutter_stop_position"])
def test_a_stop_position_past_999_hundredths_of_an_inch_is_refused_with_the_key_named(key: str) -> None:
    # 999/100 in, the most a three-digit <STX>f takes, is 253.746 mm.
    with pytest.raises(ConfigurationError, match=rf"^printer\.{key}: Input should be less than or equal to 253\.746$"):
        _options({"printer": {key: 253.8}})


@pytest.mark.os_agnostic
def test_the_largest_stop_position_dpl_takes_is_accepted() -> None:
    assert _options({"printer": {"stop_position": 253.746}}).stop_position_mm == 253.746


def _render(cli_runner: CliRunner, factory: Callable[[], Any], tmp_path: Path, *extra: str) -> str:
    label = tmp_path / "line.json"
    label.write_text(json.dumps(_LINE), encoding="utf-8")
    result = cli_runner.invoke(cli_mod.cli, [*extra, "--json", "render", str(label), "--model", "dmx-600"], obj=factory)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["data"]["dpl"]


@pytest.mark.os_agnostic
def test_a_configured_stop_position_is_sent_by_render_on_a_model_without_a_default(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    plain = _render(cli_runner, config_cli_context({}), tmp_path)
    stopped = _render(cli_runner, config_cli_context({"printer": {"stop_position": 60}}), tmp_path)

    # 60 mm is 236.22 hundredths of an inch, sent rounded and unscaled
    assert "<STX>f" not in plain
    assert "<STX>f236" in stopped


@pytest.mark.os_agnostic
def test_a_set_override_of_the_stop_position_reaches_render(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    stopped = _render(cli_runner, config_cli_context({}), tmp_path, "--set", "printer.stop_position=60")

    assert "<STX>f236" in stopped


@pytest.mark.os_agnostic
def test_the_config_command_shows_the_configured_stop_positions(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]]
) -> None:
    factory = config_cli_context({"printer": {"stop_position": 67.4, "cutter_stop_position": 80}})

    result = cli_runner.invoke(cli_mod.cli, ["config", "--format", "json", "--section", "printer"], obj=factory)

    assert result.exit_code == 0, result.output
    printer = json.loads(result.stdout)["printer"]
    assert (printer["stop_position"], printer["cutter_stop_position"]) == (67.4, 80)
