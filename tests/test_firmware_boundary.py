"""Firmware is parsed once, where it enters: the render options, the printer settings and ``--firmware``.

The text users write (``"2.01"``, a TOML or environment number) becomes a ``Firmware`` there, and a
malformed one is refused there with the key it came from, rather than as a nameless ``ValueError``
later while a label renders.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, cast

import pytest
from lib_layered_config import Config
from pydantic import ValidationError

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.commands.model_options import command_overrides, firmware_option
from datamax_dpl.adapters.cli.settings import PrinterOverrides, PrinterSettings, printer_settings, render_options
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.errors import ConfigurationError
from datamax_dpl.domain.profiles import Firmware
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner

_V2_01 = Firmware(major=2, minor=1)
_CIRCLE = {"elements": [{"type": "circle", "x": 20, "y": 20, "radius": 5}]}


def _settings(config: dict[str, Any], printer: str | None = None) -> PrinterSettings:
    return printer_settings(Config(config, {}), PrinterOverrides(printer=printer))


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("given", "parsed"),
    [
        ("2.01", _V2_01),
        (" 3.2 ", Firmware(major=3, minor=20)),
        ("", None),
        ("  ", None),
        (None, None),
        (_V2_01, _V2_01),
    ],
)
def test_render_options_hold_the_parsed_firmware(given: str | Firmware | None, parsed: Firmware | None) -> None:
    firmware = RenderOptions.model_validate({"firmware": given}).firmware

    assert firmware == parsed
    assert type(firmware) is type(parsed)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("given", ["x", "2", "1.2.3", "2.0001", "v2.01"])
def test_render_options_refuse_a_malformed_firmware_when_built(given: str) -> None:
    with pytest.raises(ValidationError) as caught:
        RenderOptions.model_validate({"firmware": given})

    (error,) = caught.value.errors()
    assert (error["loc"], error["type"]) == (("firmware",), "value_error")
    assert error["msg"] == f"Value error, firmware must look like 2.01, got {given!r}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("given", [2.01, 3, True, ["2.01"]])
def test_render_options_take_firmware_text_not_a_number(given: object) -> None:
    """A non-text value is refused as pydantic refuses it for a plain ``str`` field, type and message alike."""
    with pytest.raises(ValidationError) as caught:
        RenderOptions.model_validate({"firmware": given})

    (error,) = caught.value.errors()
    assert (error["loc"], error["type"], error["msg"]) == (
        ("firmware",),
        "string_type",
        "Input should be a valid string",
    )


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("given", "parsed"), [("2.01", _V2_01), (2.01, _V2_01), (2.1, Firmware(major=2, minor=10))])
def test_a_configured_firmware_reaches_the_render_options_parsed(given: object, parsed: Firmware) -> None:
    firmware = render_options(_settings({"printer": {"firmware": given}})).firmware

    assert firmware == parsed
    assert type(firmware) is Firmware


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("config", "printer", "refusal"),
    [
        ({"printer": {"firmware": "bad"}}, None, "printer.firmware: firmware must look like 2.01, got 'bad'"),
        ({"printer": {"firmware": 3}}, None, "printer.firmware: firmware must look like 2.01, got '3'"),
        ({"printer": {"firmware": True}}, None, "printer.firmware: Input should be a valid string"),
        (
            {"printers": {"lager": {"firmware": "1.2.3"}}},
            "lager",
            "printers.lager.firmware: firmware must look like 2.01, got '1.2.3'",
        ),
    ],
)
def test_a_malformed_configured_firmware_is_refused_naming_its_key(
    config: dict[str, Any], printer: str | None, refusal: str
) -> None:
    with pytest.raises(ConfigurationError, match=f"^{re.escape(refusal)}$"):
        _settings(config, printer=printer)


@pytest.mark.os_agnostic
def test_the_firmware_flag_wins_over_the_configured_one() -> None:
    overrides = command_overrides({"firmware": "3.2"})
    settings = printer_settings(Config({"printer": {"firmware": "2.01"}}, {}), overrides)

    assert settings.firmware == Firmware(major=3, minor=20)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("given", [None, ""])
def test_no_firmware_flag_keeps_the_configured_one(given: str | None) -> None:
    overrides = command_overrides({"firmware": given})
    settings = printer_settings(Config({"printer": {"firmware": "2.01"}}, {}), overrides)

    assert settings.firmware == _V2_01


@pytest.mark.os_agnostic
def test_a_malformed_firmware_flag_is_a_configuration_error_naming_the_flag() -> None:
    with pytest.raises(ConfigurationError, match=r"^--firmware: firmware must look like 2\.01, got 'two'$"):
        firmware_option("two")


@pytest.mark.os_agnostic
def test_a_malformed_firmware_flag_exits_2_in_the_shape_asked_for(cli_runner: CliRunner, tmp_path: Path) -> None:
    label = tmp_path / "c.json"
    label.write_text(json.dumps(_CIRCLE), encoding="utf-8")
    args = ["--json", "validate", str(label), "--model", "dmx-400", "--firmware", "bad"]

    result = cli_runner.invoke(cli_mod.cli, args, obj=build_testing)

    assert result.exit_code == 2
    document = cast("dict[str, Any]", json.loads(result.stdout))
    assert (document["error"], document["message"]) == (
        "ConfigurationError",
        "--firmware: firmware must look like 2.01, got 'bad'",
    )


@pytest.mark.os_agnostic
def test_a_malformed_configured_firmware_stops_a_command_that_renders_nothing(cli_runner: CliRunner) -> None:
    args = ["--json", "--set", "printer.firmware=bad", "status", "--printer", "tcp://192.0.2.60:9100"]

    result = cli_runner.invoke(cli_mod.cli, args, obj=build_testing)

    assert result.exit_code == 2
    assert json.loads(result.stdout)["message"] == "printer.firmware: firmware must look like 2.01, got 'bad'"
