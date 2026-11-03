"""Values the code once fixed, each now a ``[printer]`` key whose default is what the code did before.

Every key here is tried at a value other than its default, distinct from the other keys' values, so a
key that reached the wrong place fails; and at its default, which must leave the job and the exchange
with the printer as they were.
"""

from __future__ import annotations

import re
from importlib import resources
from typing import Any

import pytest
import tomlkit
from lib_layered_config import Config

from datamax_dpl.adapters.cli.settings import PrinterOverrides, PrinterSettings, printer_settings
from datamax_dpl.domain.timing import TIMEOUT_SECONDS

pytestmark = pytest.mark.usefixtures("no_ambient_configuration")


def _settings(config: dict[str, Any], printer: str | None = None) -> PrinterSettings:
    return printer_settings(Config(config, {}), PrinterOverrides(printer=printer))


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
