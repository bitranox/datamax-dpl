"""Values the code once fixed, each now a ``[printer]`` key whose default is what the code did before.

Every key here is tried at a value other than its default, distinct from the other keys' values, so a
key that reached the wrong place fails; and at its default, which must leave the job and the exchange
with the printer as they were.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import TYPE_CHECKING, Any

import pytest
import tomlkit
from lib_layered_config import Config
from printer_server import ImageModules, ModulePrinter

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.settings import PrinterOverrides, PrinterSettings, printer_settings
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.errors import ConfigurationError
from datamax_dpl.domain.label import Image, Label, Text
from datamax_dpl.domain.reset_label import CALIBRATION_LABEL_TEXT
from datamax_dpl.domain.timing import TIMEOUT_SECONDS

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner, Result

pytestmark = pytest.mark.usefixtures("no_ambient_configuration")

URI = "tcp://192.0.2.60:9100"
#: What one Allegro 2 put in front of its first answer after a power cycle.
POWER_ON = b"R\x11"
TEXT_LABEL = Label(width=100, height=60, elements=[Text(x=5, y=5, text="Hello")])
_PATTERN = ["".join("#" if (row * 7 + column * 13) % 5 == 0 else "." for column in range(64)) for row in range(64)]
IMAGE_LABEL = Label(width=100, height=60, elements=[Image(x=1, y=1, name="LOGO", bitmap=_PATTERN)])


def _settings(config: dict[str, Any], printer: str | None = None) -> PrinterSettings:
    return printer_settings(Config(config, {}), PrinterOverrides(printer=printer))


def _label_file(tmp_path: Path, label: Label = TEXT_LABEL) -> str:
    path = tmp_path / "label.json"
    path.write_text(json.dumps(label.model_dump(mode="json", exclude_none=True)), encoding="utf-8")
    return str(path)


def _cli(runner: CliRunner, printer: ModulePrinter | None, *args: str) -> Result:
    return runner.invoke(cli_mod.cli, ["--json-bare", *args], obj=lambda: build_testing(printer=printer))


def _codes(result: Result) -> list[str]:
    return [finding["code"] for finding in json.loads(result.stdout)["data"]["findings"]]


def _shipped_printer_toml() -> str:
    shipped = resources.files("datamax_dpl") / "adapters" / "config" / "defaultconfig.d" / "50-printer.toml"
    return shipped.read_text(encoding="utf-8")


# ======================== timeout: a named default ========================


@pytest.mark.os_agnostic
def test_the_timeout_default_is_the_named_constant_and_the_shipped_file_says_the_same() -> None:
    text = _shipped_printer_toml()
    [documented] = re.findall(r"^# Default: (\S+)\n# Environment Variable: DATAMAX_DPL___PRINTER__TIMEOUT=", text, re.M)

    assert PrinterSettings().timeout == TIMEOUT_SECONDS == 10.0
    assert tomlkit.parse(text).unwrap()["printer"]["timeout"] == TIMEOUT_SECONDS
    assert float(documented) == TIMEOUT_SECONDS


# ======================== reset_check: the <SOH>A read before a job ========================


@pytest.mark.os_agnostic
def test_reset_check_defaults_on_and_the_job_asks_for_the_status_once(cli_runner: CliRunner, tmp_path: Path) -> None:
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)

    result = _cli(cli_runner, printer, "print", _label_file(tmp_path), "--model", "allegro-2", "--printer", URI)

    assert PrinterSettings().reset_check is True
    assert result.exit_code == 0, result.output
    assert modules.status_requests == 1
    assert "printer_reset_calibrated" in _codes(result)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("setting", "printer_arg"),
    [
        (["--set", "printer.reset_check=false"], URI),
        (["--set", f"printers.lager.uri={URI}", "--set", "printers.lager.reset_check=false"], "lager"),
    ],
)
def test_reset_check_off_asks_nothing_and_sends_the_job_alone(
    cli_runner: CliRunner, tmp_path: Path, setting: list[str], printer_arg: str
) -> None:
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)
    args = [*setting, "print", _label_file(tmp_path), "--model", "allegro-2", "--printer", printer_arg]

    result = _cli(cli_runner, printer, *args)

    assert result.exit_code == 0, result.output
    assert modules.status_requests == 0
    assert CALIBRATION_LABEL_TEXT.encode("ascii") not in printer.data
    assert not [code for code in _codes(result) if code.startswith("printer_reset")]


@pytest.mark.os_agnostic
def test_reset_check_off_leaves_a_report_a_names_read_carries_unacted_on(cli_runner: CliRunner, tmp_path: Path) -> None:
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)
    settings = ["--set", "printer.reset_check=false", "--set", "printer.clear_graphics_before=false"]
    args = [*settings, "print", _label_file(tmp_path, IMAGE_LABEL), "--model", "allegro-2", "--printer", URI]

    result = _cli(cli_runner, printer, *args)

    assert result.exit_code == 0, result.output
    assert (modules.status_requests, modules.requests) == (0, 2)
    assert CALIBRATION_LABEL_TEXT.encode("ascii") not in printer.data
    assert not [code for code in _codes(result) if code.startswith("printer_reset")]


@pytest.mark.os_agnostic
def test_reset_check_takes_only_a_boolean_and_names_its_key() -> None:
    with pytest.raises(ConfigurationError, match=r"^printer\.reset_check: Input should be a valid boolean"):
        _settings({"printer": {"reset_check": "sometimes"}})


# ======================== upload_check_rounds = 0: no read after the upload ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("rounds", "requests", "lost_image_printed"), [(None, 2, False), ("0", 0, True)])
def test_upload_check_rounds_0_reaches_the_print_and_reads_nothing_after_the_upload(
    cli_runner: CliRunner, tmp_path: Path, rounds: str | None, requests: int, lost_image_printed: bool
) -> None:
    """The default reads the lost upload, clears and uploads again; 0 sends the format after the lost one."""
    modules = ImageModules(lose={0})
    printer = ModulePrinter(modules)
    setting = [] if rounds is None else ["--set", f"printer.upload_check_rounds={rounds}"]
    args = [*setting, "print", _label_file(tmp_path, IMAGE_LABEL), "--model", "allegro-2", "--printer", URI]

    result = _cli(cli_runner, printer, *args)

    assert result.exit_code == 0, result.output
    assert modules.requests == requests
    assert bool(modules.placed_missing) is lost_image_printed
