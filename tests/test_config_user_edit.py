"""Writing a printer's values into the user configuration layer, on real files."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
import tomlkit

from datamax_dpl.adapters.config.loader import get_config
from datamax_dpl.adapters.config.user_edit import set_user_printer_values, user_layer_files, write_printer_values

if TYPE_CHECKING:
    from pathlib import Path

_COMMENTED = """\
# my printers
[printer]
model = "allegro-2"  # the one in the stock room

[printers.lager]
# measured 2026-10-29
uri = "tcp://192.0.2.60:9100"
scale_along = 0.977
label_width = 100
"""


def _table(path: Path, *keys: str) -> dict[str, object]:
    value: dict[str, object] = tomlkit.parse(path.read_text(encoding="utf-8")).unwrap()
    for key in keys:
        child = value[key]
        assert isinstance(child, dict)
        value = cast("dict[str, object]", child)
    return value


@pytest.mark.os_agnostic
def test_a_missing_file_is_created_with_the_printers_section(tmp_path: Path) -> None:
    base = tmp_path / "user" / "config.toml"

    written = write_printer_values([base], printer="lager", values={"scale_along": 0.9721})

    assert written == base
    assert base.read_text(encoding="utf-8") == "[printers.lager]\nscale_along = 0.9721\n"


@pytest.mark.os_agnostic
def test_comments_and_key_order_survive_and_the_value_is_replaced(tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_COMMENTED, encoding="utf-8")

    write_printer_values([base], printer="lager", values={"scale_along": 0.9721, "scale_across": 0.992})

    text = base.read_text(encoding="utf-8")
    assert text == _COMMENTED.replace("scale_along = 0.977\n", "scale_along = 0.9721\n") + "scale_across = 0.992\n"


@pytest.mark.os_agnostic
def test_no_name_writes_the_printer_section(tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_COMMENTED, encoding="utf-8")

    write_printer_values([base], printer="", values={"scale_along": 0.98})

    assert _table(base, "printer") == {"model": "allegro-2", "scale_along": 0.98}


@pytest.mark.os_agnostic
def test_the_last_file_holding_the_section_gets_the_value_since_it_wins(tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text('[printers.lager]\nmodel = "allegro-2"\n', encoding="utf-8")
    first, second = tmp_path / "config.d" / "50-printer.toml", tmp_path / "config.d" / "60-other.toml"
    first.parent.mkdir()
    first.write_text("[printers.lager]\nscale_along = 0.977\n", encoding="utf-8")
    second.write_text("[printers.kasse]\nscale_along = 1.01\n", encoding="utf-8")

    written = write_printer_values([base, first, second], printer="lager", values={"scale_along": 0.9721})

    assert written == first
    assert _table(first, "printers", "lager") == {"scale_along": 0.9721}
    assert "scale_along" not in base.read_text(encoding="utf-8")


@pytest.mark.os_agnostic
def test_without_the_section_anywhere_the_base_file_gets_it(tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    dot_d = tmp_path / "config.d" / "50-printer.toml"
    dot_d.parent.mkdir()
    dot_d.write_text("[printer]\n# [printers.lager]\n# scale_along = 0.977\n", encoding="utf-8")

    written = write_printer_values([base, dot_d], printer="lager", values={"scale_along": 0.9721})

    assert written == base


@pytest.mark.os_linux
def test_the_user_layer_is_the_base_file_then_its_existing_toml_drop_ins_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    dot_d = tmp_path / "datamax-dpl" / "config.d"
    dot_d.mkdir(parents=True)
    for name in ("60-camera.toml", "50-printer.toml", "70-extra.yaml", "README.md"):
        (dot_d / name).write_text("", encoding="utf-8")

    files = user_layer_files(platform="linux")

    assert files == [tmp_path / "datamax-dpl" / "config.toml", dot_d / "50-printer.toml", dot_d / "60-camera.toml"]


@pytest.mark.os_agnostic
def test_the_written_value_is_what_the_next_configuration_load_reads(clear_config_cache: None) -> None:
    assert get_config().get("printers") is None

    written = set_user_printer_values(printer="lager", values={"scale_along": 0.9721})

    assert written == user_layer_files()[0]
    printers = cast("dict[str, dict[str, object]]", get_config().get("printers"))
    assert printers["lager"] == {"scale_along": 0.9721}
