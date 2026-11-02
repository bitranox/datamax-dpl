"""Every config section a message names is rendered from ``SectionPath``, spelled as the file spells it.

The rendered forms are pinned here exactly, and so is each refusal, hint and help text that names a
section, through the entry point that produces it: a message that drifted from the file's spelling
sends a user to a key that does not exist.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import pytest
from lib_layered_config import Config

from datamax_dpl.adapters.camera import capture_image
from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.commands.printer import STORED_NAMES_WAIT_LONGER, WAIT_LONGER
from datamax_dpl.adapters.cli.settings import (
    PrinterOverrides,
    PrinterSettings,
    camera_settings,
    printer_settings,
    require_uri,
)
from datamax_dpl.adapters.config.sections import (
    CAMERA_SECTION,
    PRINTER_SECTION,
    ConfigSection,
    SectionPath,
    printer_section,
)
from datamax_dpl.domain.camera import CameraKind, CameraSettings
from datamax_dpl.domain.errors import CameraError, ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path

    import rich_click as click


@pytest.mark.os_agnostic
def test_a_named_printer_renders_its_header_dotted_keys_and_hint() -> None:
    lager = printer_section("lager")

    assert lager == SectionPath(ConfigSection.PRINTERS, "lager")
    assert lager.keys == ("printers", "lager")
    assert lager.dotted == "printers.lager"
    assert lager.header == "[printers.lager]"
    assert lager.key_path("firmware") == "printers.lager.firmware"
    assert lager.setting("uri") == "[printers.lager] uri"


@pytest.mark.os_agnostic
def test_no_printer_name_renders_the_printer_section() -> None:
    plain = printer_section("")

    assert plain is PRINTER_SECTION
    assert plain.keys == ("printer",)
    assert plain.header == "[printer]"
    assert plain.key_path("status_wait") == "printer.status_wait"
    assert plain.setting("stored_names_wait") == "[printer] stored_names_wait"


@pytest.mark.os_agnostic
def test_the_camera_and_printers_sections_render_as_the_file_spells_them() -> None:
    assert CAMERA_SECTION.header == "[camera]"
    assert CAMERA_SECTION.key_path("kind") == "camera.kind"
    assert CAMERA_SECTION.setting("kind") == "[camera] kind"
    assert SectionPath(ConfigSection.PRINTERS).header == "[printers]"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("section", [ConfigSection.PRINTER, ConfigSection.CAMERA], ids=lambda s: s.value)
def test_only_the_printers_section_takes_a_name(section: ConfigSection) -> None:
    with pytest.raises(ValueError, match=r"^only \[printers\] holds named tables, not \[" + section.value + r"\]$"):
        SectionPath(section, "lager")


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("model", "keys"),
    [
        (PrinterSettings, ("uri", "status_wait", "stored_names_wait", "label_width", "label_height", "firmware")),
        (CameraSettings, ("kind", "command", "url", "directory")),
    ],
    ids=["printer", "camera"],
)
def test_every_key_a_hint_names_is_a_setting_of_its_section(model: type[Any], keys: tuple[str, ...]) -> None:
    assert set(keys) <= set(model.model_fields)


def _settings(config: dict[str, Any], printer: str | None = None) -> PrinterSettings:
    return printer_settings(Config(config, {}), PrinterOverrides(printer=printer))


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("config", "printer", "refusal"),
    [
        ({"printer": {"status_wait": -1}}, None, "printer.status_wait: Input should be greater than 0"),
        (
            {"printers": {"lager": {"status_wait": -1}}},
            "lager",
            "printers.lager.status_wait: Input should be greater than 0",
        ),
        (
            {"printers": {"-bad": {"model": "allegro"}}},
            None,
            "printers.-bad: a printer name takes letters, digits, '_' and '-' (not first), no ':'",
        ),
        (
            {"printers": {"lager": {"model": "allegro"}}},
            "keller",
            "no printer named 'keller' in [printers]; configured: lager",
        ),
    ],
    ids=["printer-key", "named-key", "invalid-name", "unknown-name"],
)
def test_a_printer_refusal_names_the_section_as_the_file_spells_it(
    config: dict[str, Any], printer: str | None, refusal: str
) -> None:
    with pytest.raises(ConfigurationError, match=f"^{re.escape(refusal)}$"):
        _settings(config, printer=printer)


@pytest.mark.os_agnostic
def test_a_camera_refusal_names_the_camera_key() -> None:
    refusal = "camera.kind: Input should be 'none', 'command', 'http' or 'file'"

    with pytest.raises(ConfigurationError, match=f"^{re.escape(refusal)}$"):
        camera_settings(Config({"camera": {"kind": "bogus"}}, {}))


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["", "lager"])
def test_no_uri_names_the_printer_section_uri(name: str) -> None:
    # The printer section's uri also serves a named printer that sets none of its own.
    with pytest.raises(ConfigurationError) as caught:
        require_uri(PrinterSettings(name=name))

    assert str(caught.value) == "no printer given: pass --printer NAME or URI, or set [printer] uri"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("kind", "refusal"),
    [
        (CameraKind.NONE, "no camera configured: set [camera] kind"),
        (CameraKind.COMMAND, "no camera command configured: set [camera] command"),
        (CameraKind.HTTP, "no camera URL configured: set [camera] url"),
        (CameraKind.FILE, "no camera directory configured: set [camera] directory"),
    ],
    ids=lambda value: value.value if isinstance(value, CameraKind) else "",
)
def test_an_unconfigured_camera_names_the_key_to_set(kind: CameraKind, refusal: str, tmp_path: Path) -> None:
    with pytest.raises(CameraError) as caught:
        capture_image(CameraSettings(kind=kind, timeout=0.5), output_dir=tmp_path)

    assert str(caught.value) == refusal


@pytest.mark.os_agnostic
def test_the_wait_hints_name_the_printer_section_keys() -> None:
    assert WAIT_LONGER == "wait longer with [printer] status_wait or --timeout"
    assert STORED_NAMES_WAIT_LONGER == "wait longer with [printer] stored_names_wait"


def _command(*path: str) -> click.Command:
    command: Any = cli_mod.cli
    for name in path:
        command = command.commands[name]
    return command


def _help(command: click.Command, flag: str) -> str | None:
    (param,) = [param for param in command.params if flag in param.opts]
    return getattr(param, "help", None)


@pytest.mark.os_agnostic
def test_the_help_texts_name_the_sections_as_the_file_spells_them() -> None:
    render, status = _command("render"), _command("status")

    assert _help(render, "--model") == "Printer model profile (see 'datamax-dpl models'); default [printer] model."
    assert _help(render, "--cutter") == "Whether the printer has a working cutter; default [printer] cutter."
    assert _help(status, "--printer") == (
        "Configured printer name ([printers.NAME]) or URI, e.g. tcp://192.0.2.60:9100; default [printer] uri."
    )
    assert _help(status, "--timeout") == (
        "Seconds to wait for the printer; default [printer] timeout. For status, memory and verify, the"
        " whole wait for the printer instead of [printer] status_wait."
    )
    assert _help(_command("calibrate", "set"), "--printer") == (
        "Configured printer name ([printers.NAME]); default the [printer] section."
    )
    assert _command("capture").help == "Take one photo with the configured camera ([camera] section)."
