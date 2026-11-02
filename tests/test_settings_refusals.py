"""What the settings models refuse: a misspelled key, a section that is no table, a boolean as a number."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from lib_layered_config import Config

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.settings import (
    PreviewSettings,
    PrinterOverrides,
    PrinterSettings,
    camera_settings,
    preview_settings,
    printer_settings,
)
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.camera import CameraSettings
from datamax_dpl.domain.errors import ConfigurationError

if TYPE_CHECKING:
    from click.testing import CliRunner

REPO = Path(__file__).resolve().parent.parent
MODELS: dict[str, Any] = {"printer": PrinterSettings, "camera": CameraSettings, "preview": PreviewSettings}


def _printer(section: dict[str, Any], *, name: str | None = None) -> PrinterSettings:
    data = {"printers": {name: section}} if name else {"printer": section}
    return printer_settings(Config(data, {}), PrinterOverrides(printer=name))


@pytest.mark.os_agnostic
def test_a_misspelled_printer_key_is_refused_naming_it() -> None:
    with pytest.raises(ConfigurationError) as caught:
        _printer({"timout": 5})
    assert str(caught.value) == "printer.timout: Extra inputs are not permitted"


@pytest.mark.os_agnostic
def test_a_misspelled_key_of_a_named_printer_is_refused_naming_it() -> None:
    with pytest.raises(ConfigurationError) as caught:
        _printer({"modle": "generic"}, name="lager")
    assert str(caught.value) == "printers.lager.modle: Extra inputs are not permitted"


@pytest.mark.os_agnostic
def test_a_misspelled_camera_key_is_refused_naming_it() -> None:
    with pytest.raises(ConfigurationError) as caught:
        camera_settings(Config({"camera": {"timout": 5}}, {}))
    assert str(caught.value) == "camera.timout: Extra inputs are not permitted"


@pytest.mark.os_agnostic
def test_a_misspelled_preview_key_is_refused_naming_it() -> None:
    with pytest.raises(ConfigurationError) as caught:
        preview_settings(Config({"preview": {"max_length": 5}}, {}))
    assert str(caught.value) == "preview.max_length: Extra inputs are not permitted"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("value", [5, "text", True, ["a"]], ids=repr)
def test_a_section_that_is_no_table_is_refused_naming_it(value: object) -> None:
    """``printer = 5`` was dropped silently, so every key the user meant to set stayed at its default."""
    with pytest.raises(ConfigurationError, match=r"^printer: needs a table of settings"):
        printer_settings(Config({"printer": value}, {}), PrinterOverrides())
    with pytest.raises(ConfigurationError, match=r"^camera: needs a table of settings"):
        camera_settings(Config({"camera": value}, {}))
    with pytest.raises(ConfigurationError, match=r"^preview: needs a table of settings"):
        preview_settings(Config({"preview": value}, {}))
    with pytest.raises(ConfigurationError, match=r"^printers: needs a table of settings"):
        printer_settings(Config({"printers": value}, {}), PrinterOverrides())


@pytest.mark.os_agnostic
def test_a_named_printer_that_is_no_table_is_refused_naming_it() -> None:
    with pytest.raises(ConfigurationError, match=r"^printers\.lager: needs a table of settings"):
        printer_settings(Config({"printers": {"lager": 5}}, {}), PrinterOverrides(printer="lager"))


@pytest.mark.os_agnostic
def test_the_cli_reports_a_misspelled_key_with_exit_2(cli_runner: CliRunner, tmp_path: Path) -> None:
    label = tmp_path / "label.json"
    label.write_text('{"elements": [{"type": "text", "x": 1, "y": 1, "text": "A"}]}', encoding="utf-8")
    result = cli_runner.invoke(cli_mod.cli, ["--set", "printer.timout=5", "validate", str(label)], obj=build_testing)
    assert result.exit_code == 2, result.output
    assert "printer.timout: Extra inputs are not permitted" in result.stderr


@pytest.mark.os_agnostic
def test_every_key_the_shipped_files_name_is_declared() -> None:
    """Forbidding undeclared keys holds only while what the package ships declares none itself."""
    shipped = sorted((REPO / "src").rglob("defaultconfig.d/*.toml"))
    assert shipped
    seen = 0
    for path in shipped:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        for section, model in MODELS.items():
            for key in document.get(section, {}):
                seen += 1
                assert key in model.model_fields, f"{path.name}: {section}.{key}"
    assert seen


@pytest.mark.os_agnostic
def test_every_key_env_example_names_is_declared() -> None:
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    found = re.findall(r"^#?\s*(?:[A-Z_]+___)?(PRINTER|CAMERA|PREVIEW)__([A-Z_0-9]+)=", text, flags=re.MULTILINE)
    assert found
    for section, key in found:
        assert key.lower() in MODELS[section.lower()].model_fields, f"{section}__{key}"
