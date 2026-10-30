"""The printer settings' calibration, stop-position and image-module keys, from the config file to the rendered job."""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import TYPE_CHECKING, Any

import pytest
import tomlkit
from lib_layered_config import Config

from datamax_dpl.adapters import cli as cli_mod
from datamax_dpl.adapters.cli.settings import PrinterOverrides, printer_settings, render_options
from datamax_dpl.domain.enums import ImageModule
from datamax_dpl.domain.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

    from datamax_dpl.domain.render_context import RenderOptions

_CALIBRATION_KEYS = ("scale_across", "scale_along", "offset_across", "offset_along")
_LINE = {"elements": [{"type": "line", "x": 10, "y": 10, "x2": 10, "y2": 110, "thickness": 0.5}]}
_LOGO = {"elements": [{"type": "image", "x": 1, "y": 1, "name": "LOGO", "bitmap": ["#......#"] * 8}]}


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
    # A shipped module would send every model's images to that one module, and on a DMX 600/800
    # (built-in RAM is E) a shipped A would name a plug-in module the printer may not have.
    assert options.image_module is None
    # A shipped limit would refuse long labels on every model, not only the one it was measured on.
    assert options.max_format_rows is None


@pytest.mark.os_agnostic
def test_a_configured_max_format_rows_reaches_the_render_options() -> None:
    assert _options({"printer": {"max_format_rows": 2000}}).max_format_rows == 2000


@pytest.mark.os_agnostic
def test_a_named_printers_max_format_rows_wins_over_the_printer_section() -> None:
    config = {"printer": {"max_format_rows": 1700}, "printers": {"x": {"max_format_rows": 1800}}}

    assert _options(config, printer="x").max_format_rows == 1800
    assert _options(config).max_format_rows == 1700


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("section", "printer"), [("printer", None), ("printers.x", "x")])
def test_a_max_format_rows_of_zero_is_refused_with_the_key_named(section: str, printer: str | None) -> None:
    config = {"printer": {"max_format_rows": 0}} if printer is None else {"printers": {"x": {"max_format_rows": 0}}}

    with pytest.raises(
        ConfigurationError, match=rf"^{re.escape(section)}\.max_format_rows: Input should be greater than 0$"
    ):
        _options(config, printer=printer)


@pytest.mark.os_agnostic
def test_a_configured_image_module_reaches_the_render_options() -> None:
    assert _options({"printer": {"image_module": "E"}}).image_module is ImageModule.E


@pytest.mark.os_agnostic
def test_a_named_printers_image_module_wins_over_the_printer_section() -> None:
    config = {"printer": {"image_module": "A"}, "printers": {"x": {"image_module": "E"}}}

    assert _options(config, printer="x").image_module is ImageModule.E
    assert _options(config).image_module is ImageModule.A


@pytest.mark.os_agnostic
def test_an_unset_image_module_leaves_the_models_module_in_force() -> None:
    assert _options({"printer": {"model": "dmx-600"}}).image_module is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("section", "printer"), [("printer", None), ("printers.x", "x")])
def test_an_image_module_outside_a_to_e_is_refused_with_the_key_named(section: str, printer: str | None) -> None:
    config = {"printer": {"image_module": "F"}} if printer is None else {"printers": {"x": {"image_module": "F"}}}

    with pytest.raises(
        ConfigurationError, match=rf"^{re.escape(section)}\.image_module: Input should be 'A', 'B', 'C', 'D' or 'E'$"
    ):
        _options(config, printer=printer)


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


@pytest.mark.os_agnostic
def test_a_configured_stop_position_of_printer_reaches_the_render_options() -> None:
    assert _options({"printer": {"stop_position": "printer"}}).stop_position_mm == "printer"


@pytest.mark.os_agnostic
def test_a_named_printers_stop_position_and_the_printer_sections_win_over_each_other_by_section() -> None:
    config = {"printer": {"stop_position": 60}, "printers": {"x": {"stop_position": "printer"}}}
    flipped = {"printer": {"stop_position": "printer"}, "printers": {"x": {"stop_position": 60}}}

    assert _options(config, printer="x").stop_position_mm == "printer"
    assert _options(config).stop_position_mm == 60
    assert _options(flipped, printer="x").stop_position_mm == 60


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("section", "printer"), [("printer", None), ("printers.x", "x")])
def test_a_stop_position_word_other_than_printer_is_refused_with_the_key_named(
    section: str, printer: str | None
) -> None:
    config = {"printer": {"stop_position": "paper"}}
    if printer is not None:
        config = {"printers": {"x": {"stop_position": "paper"}}}

    with pytest.raises(ConfigurationError, match=rf"^{re.escape(section)}\.stop_position: Input should be 'printer'$"):
        _options(config, printer=printer)


@pytest.mark.os_agnostic
def test_the_cutter_stop_position_takes_no_printer_value() -> None:
    """A job that cuts needs the stop where the cutter is; unset already sends none (decision D37)."""
    with pytest.raises(ConfigurationError, match=r"^printer\.cutter_stop_position: Input should be a valid number"):
        _options({"printer": {"cutter_stop_position": "printer"}})


def _render(
    cli_runner: CliRunner,
    factory: Callable[[], Any],
    tmp_path: Path,
    *extra: str,
    label: dict[str, Any] = _LINE,
    model: str = "dmx-600",
) -> str:
    path = tmp_path / "label.json"
    path.write_text(json.dumps(label), encoding="utf-8")
    result = cli_runner.invoke(cli_mod.cli, [*extra, "--json", "render", str(path), "--model", model], obj=factory)
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
@pytest.mark.parametrize("printer_section", [{"stop_position": "printer"}, {}])
def test_a_configured_stop_position_of_printer_keeps_the_allegro_2s_own_stop(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
    printer_section: dict[str, Any],
) -> None:
    """Without the config value the Allegro 2 sends its measured 67.4 mm; with it, no stop at all."""
    dpl = _render(cli_runner, config_cli_context({"printer": printer_section}), tmp_path, model="allegro-2")

    assert ("<STX>f" in dpl) is (printer_section == {})


@pytest.mark.os_agnostic
def test_a_set_override_of_printer_reaches_render(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    factory = config_cli_context({"printer": {"stop_position": 60}})
    dpl = _render(cli_runner, factory, tmp_path, "--set", "printer.stop_position=printer", model="allegro-2")

    assert "<STX>f" not in dpl


@pytest.mark.os_agnostic
def test_the_labels_stop_position_wins_over_a_configured_printer(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    label = {**_LINE, "stop_position": 50}
    factory = config_cli_context({"printer": {"stop_position": "printer"}})
    configured = _render(cli_runner, factory, tmp_path, label=label, model="allegro-2")
    plain = _render(cli_runner, config_cli_context({}), tmp_path, label=label, model="allegro-2")

    assert "<STX>f" in plain
    assert configured == plain


@pytest.mark.os_agnostic
def test_a_labels_printer_wins_over_a_configured_stop_position(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    label = {**_LINE, "stop_position": "printer"}
    dpl = _render(
        cli_runner, config_cli_context({"printer": {"stop_position": 60}}), tmp_path, label=label, model="allegro-2"
    )

    assert "<STX>f" not in dpl


@pytest.mark.os_agnostic
def test_the_config_command_shows_the_configured_stop_positions(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]]
) -> None:
    factory = config_cli_context({"printer": {"stop_position": 67.4, "cutter_stop_position": 80}})

    result = cli_runner.invoke(cli_mod.cli, ["config", "--format", "json", "--section", "printer"], obj=factory)

    assert result.exit_code == 0, result.output
    printer = json.loads(result.stdout)["printer"]
    assert (printer["stop_position"], printer["cutter_stop_position"]) == (67.4, 80)


@pytest.mark.os_agnostic
def test_a_configured_image_module_lets_a_prodigy_download_its_image(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    """The Prodigy has no built-in module, so only the configured one can take the download."""
    factory = config_cli_context({"printer": {"image_module": "B"}})

    dpl = _render(cli_runner, factory, tmp_path, label=_LOGO, model="prodigy")

    assert "<STX>IBAFLOGO" in dpl


@pytest.mark.os_agnostic
def test_a_prodigy_without_a_configured_image_module_is_refused_naming_the_setting(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    path = tmp_path / "label.json"
    path.write_text(json.dumps(_LOGO), encoding="utf-8")

    result = cli_runner.invoke(
        cli_mod.cli, ["--json", "render", str(path), "--model", "prodigy"], obj=config_cli_context({})
    )

    assert result.exit_code == 1, result.output
    [finding] = [f for f in json.loads(result.stdout)["data"]["findings"] if f["code"] == "image_module_unknown"]
    assert "image_module" in finding["message"]


@pytest.mark.os_agnostic
def test_a_set_override_of_the_image_module_wins_over_the_models(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    plain = _render(cli_runner, config_cli_context({}), tmp_path, label=_LOGO)
    moved = _render(cli_runner, config_cli_context({}), tmp_path, "--set", "printer.image_module=C", label=_LOGO)

    assert "<STX>IEAFLOGO" in plain
    assert "<STX>ICAFLOGO" in moved


_LONG_LINE = {"elements": [{"type": "line", "x": 5, "y": 2, "x2": 5, "y2": 215, "thickness": 0.5}]}


@pytest.mark.os_agnostic
def test_an_allegro_2_refuses_a_line_past_its_format_length_naming_the_setting(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    path = tmp_path / "label.json"
    path.write_text(json.dumps(_LONG_LINE), encoding="utf-8")

    result = cli_runner.invoke(
        cli_mod.cli, ["--json", "render", str(path), "--model", "allegro-2"], obj=config_cli_context({})
    )

    assert result.exit_code == 1, result.output
    [finding] = [f for f in json.loads(result.stdout)["data"]["findings"] if f["code"] == "format_past_max_length"]
    assert "max_format_rows" in finding["message"]


@pytest.mark.os_agnostic
def test_a_configured_max_format_rows_lets_an_allegro_2_render_the_long_line(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    factory = config_cli_context({"printer": {"max_format_rows": 10000}})

    dpl = _render(cli_runner, factory, tmp_path, label=_LONG_LINE, model="allegro-2")

    assert "<STX>L" in dpl
