"""What the settings models refuse: a misspelled key, a section that is no table, a boolean as a number."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import rtoml
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
from datamax_dpl.composition import build_production, build_testing
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
        document = rtoml.load(path)
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


# ======================== A boolean is not a number ========================

PRINTER_NUMBERS = [
    "timeout",
    "status_wait",
    "status_retry_interval",
    "stored_names_wait",
    "upload_check_rounds",
    "max_batch_records",
    "verify_poll_interval",
    "one_way_settle",
    "send_chunk_bytes",
    "scale_across",
    "scale_along",
    "offset_across",
    "offset_along",
    "stop_position",
    "cutter_stop_position",
    "image_memory_bytes",
    "max_format_rows",
    "label_width",
    "label_height",
]
CAMERA_NUMBERS = ["settle_seconds", "timeout"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", PRINTER_NUMBERS)
@pytest.mark.parametrize(("value", "word"), [(True, "true"), (False, "false")])
def test_a_printer_number_refuses_a_boolean(key: str, value: bool, word: str) -> None:
    """`timeout = true` was a 1 second timeout, and `offset_along = false` beat the model's own default."""
    with pytest.raises(ConfigurationError) as caught:
        _printer({key: value})
    assert str(caught.value) == f"printer.{key}: needs a number, got {word}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", CAMERA_NUMBERS)
def test_a_camera_number_refuses_a_boolean(key: str) -> None:
    with pytest.raises(ConfigurationError) as caught:
        camera_settings(Config({"camera": {key: True}}, {}))
    assert str(caught.value) == f"camera.{key}: needs a number, got true"


@pytest.mark.os_agnostic
def test_a_preview_number_refuses_a_boolean() -> None:
    with pytest.raises(ConfigurationError) as caught:
        preview_settings(Config({"preview": {"max_length_mm": True}}, {}))
    assert str(caught.value) == "preview.max_length_mm: needs a number, got true"


@pytest.mark.os_agnostic
def test_the_list_above_is_every_number_of_the_settings() -> None:
    """A numeric field added later must join the refusal, and so this list."""
    numeric = {"float", "int"}
    declared = {
        name
        for name, field in PrinterSettings.model_fields.items()
        if numeric & set(re.findall(r"\bfloat\b|\bint\b", str(field.annotation)))
    }
    assert declared == set(PRINTER_NUMBERS)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "Infinity", float("inf"), float("nan")], ids=repr)
@pytest.mark.parametrize(
    "key", ["timeout", "label_width", "label_height", "status_wait", "scale_along", "offset_along", "stop_position"]
)
def test_a_printer_number_refuses_infinity_as_text_or_number(key: str, value: object) -> None:
    with pytest.raises(ConfigurationError, match=f"printer\\.{key}"):
        _printer({key: value})


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", CAMERA_NUMBERS)
@pytest.mark.parametrize("value", ["inf", "nan"])
def test_a_camera_number_refuses_infinity_as_text(key: str, value: str) -> None:
    with pytest.raises(ConfigurationError, match=f"camera\\.{key}"):
        camera_settings(Config({"camera": {key: value}}, {}))


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", ["label_width", "label_height"])
def test_a_label_size_in_the_settings_keeps_the_label_bound(key: str) -> None:
    with pytest.raises(ConfigurationError, match=f"printer\\.{key}"):
        _printer({key: 1e308})


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("key", "text", "expected"),
    [
        ("timeout", "1.50", 1.5),
        ("timeout", "007", 7.0),
        ("timeout", "1e1", 10.0),
        ("send_chunk_bytes", "007", 7),
        ("stop_position", "2.5", 2.5),
        ("stop_position", "printer", "printer"),
    ],
)
def test_numeric_text_stays_a_number_since_dotenv_delivers_text(key: str, text: str, expected: object) -> None:
    """The env layers keep text that does not read back as itself: 1.50 stays '1.50'."""
    assert getattr(_printer({key: text}), key) == expected


def _from_dotenv(tmp_path: Path, line: str) -> Config:
    """The configuration the production loader builds from a ``.env`` holding ``line``."""
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{line}\n", encoding="utf-8")
    return build_production().get_config(dotenv_path=str(dotenv))


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("clear_config_cache")
@pytest.mark.parametrize(("text", "expected"), [("1.50", 1.5), ("007", 7.0)])
def test_a_dotenv_number_that_arrives_as_text_still_works(tmp_path: Path, text: str, expected: float) -> None:
    """Through the production loader, since only it decides what a .env value arrives as."""
    config = _from_dotenv(tmp_path, f"PRINTER__TIMEOUT={text}")
    # Premise: the value arrives as its text. Without it this proves nothing about numeric text.
    assert config.get("printer", {})["timeout"] == text
    assert printer_settings(config, PrinterOverrides()).timeout == expected


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("clear_config_cache")
def test_a_dotenv_true_is_refused_as_a_number(tmp_path: Path) -> None:
    config = _from_dotenv(tmp_path, "PRINTER__TIMEOUT=true")
    with pytest.raises(ConfigurationError) as caught:
        printer_settings(config, PrinterOverrides())
    assert str(caught.value) == "printer.timeout: needs a number, got true"
