"""Writing a printer's values into the user configuration layer, on real files."""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING, cast

import pytest
import tomlkit

from datamax_dpl.adapters.config.loader import get_config
from datamax_dpl.adapters.config.sections import ConfigSection
from datamax_dpl.adapters.config.user_edit import set_user_printer_values, user_layer_files, write_printer_values
from datamax_dpl.domain.errors import ConfigurationError
from datamax_dpl.domain.profiles import CalibrationKey

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

    written = write_printer_values([base], printer="lager", values={CalibrationKey.SCALE_ALONG: 0.9721})

    assert written == base
    assert base.read_text(encoding="utf-8") == "[printers.lager]\nscale_along = 0.9721\n"


@pytest.mark.os_agnostic
def test_comments_and_key_order_survive_and_the_value_is_replaced(tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_COMMENTED, encoding="utf-8")

    write_printer_values(
        [base], printer="lager", values={CalibrationKey.SCALE_ALONG: 0.9721, CalibrationKey.SCALE_ACROSS: 0.992}
    )

    text = base.read_text(encoding="utf-8")
    assert text == _COMMENTED.replace("scale_along = 0.977\n", "scale_along = 0.9721\n") + "scale_across = 0.992\n"


@pytest.mark.os_agnostic
def test_no_name_writes_the_printer_section(tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_COMMENTED, encoding="utf-8")

    write_printer_values([base], printer="", values={CalibrationKey.SCALE_ALONG: 0.98})

    assert _table(base, "printer") == {"model": "allegro-2", "scale_along": 0.98}


@pytest.mark.os_agnostic
def test_the_last_file_holding_the_section_gets_the_value_since_it_wins(tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text('[printers.lager]\nmodel = "allegro-2"\n', encoding="utf-8")
    first, second = tmp_path / "config.d" / "50-printer.toml", tmp_path / "config.d" / "60-other.toml"
    first.parent.mkdir()
    first.write_text("[printers.lager]\nscale_along = 0.977\n", encoding="utf-8")
    second.write_text("[printers.kasse]\nscale_along = 1.01\n", encoding="utf-8")

    written = write_printer_values([base, first, second], printer="lager", values={CalibrationKey.SCALE_ALONG: 0.9721})

    assert written == first
    assert _table(first, "printers", "lager") == {"scale_along": 0.9721}
    assert "scale_along" not in base.read_text(encoding="utf-8")


@pytest.mark.os_agnostic
def test_without_the_section_anywhere_the_base_file_gets_it(tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    dot_d = tmp_path / "config.d" / "50-printer.toml"
    dot_d.parent.mkdir()
    dot_d.write_text("[printer]\n# [printers.lager]\n# scale_along = 0.977\n", encoding="utf-8")

    written = write_printer_values([base, dot_d], printer="lager", values={CalibrationKey.SCALE_ALONG: 0.9721})

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

    written = set_user_printer_values(printer="lager", values={CalibrationKey.SCALE_ALONG: 0.9721})

    assert written == user_layer_files()[0]
    printers = cast("dict[str, dict[str, object]]", get_config().get("printers"))
    assert printers["lager"] == {"scale_along": 0.9721}


@pytest.mark.os_agnostic
def test_the_config_sections_are_spelled_as_the_file_spells_them() -> None:
    assert [section.value for section in ConfigSection] == ["printer", "printers", "camera", "preview"]


@pytest.mark.os_agnostic
def test_a_written_file_is_replaced_whole_and_no_temp_file_stays(tmp_path: Path) -> None:
    """A write in place truncates first: a crash then loses every other setting, a camera password included."""
    target = tmp_path / "config.toml"
    target.write_text('[printer]\nmodel = "allegro-2"\n', encoding="utf-8")
    before = target.stat().st_ino
    write_printer_values([target], printer="", values={CalibrationKey.SCALE_ACROSS: 1.01})
    assert target.stat().st_ino != before
    assert _table(target, "printer")["model"] == "allegro-2"
    assert [path.name for path in tmp_path.iterdir()] == ["config.toml"]


@pytest.mark.os_posix
@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
@pytest.mark.parametrize("mode", [0o600, 0o640, 0o644])
def test_a_rewritten_file_keeps_its_mode(tmp_path: Path, mode: int) -> None:
    target = tmp_path / "config.toml"
    target.write_text('[printer]\nmodel = "allegro-2"\n', encoding="utf-8")
    target.chmod(mode)
    write_printer_values([target], printer="", values={CalibrationKey.SCALE_ACROSS: 1.01})
    assert stat.S_IMODE(target.stat().st_mode) == mode


@pytest.mark.os_posix
@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
def test_a_new_file_is_private_since_it_may_come_to_hold_a_password(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "config.toml"
    write_printer_values([target], printer="", values={CalibrationKey.SCALE_ACROSS: 1.01})
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.os_agnostic
def test_a_symlinked_config_stays_a_link_and_its_target_gets_the_value(tmp_path: Path) -> None:
    """A replace on the link's own path would turn a dotfiles link into a plain copy."""
    real = tmp_path / "dotfiles" / "config.toml"
    real.parent.mkdir()
    real.write_text('[printer]\nmodel = "allegro-2"\n', encoding="utf-8")
    link = tmp_path / "config.toml"
    try:
        link.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"this account cannot create a symlink: {exc}")
    write_printer_values([link], printer="", values={CalibrationKey.SCALE_ACROSS: 1.01})
    assert link.is_symlink()
    assert _table(real, "printer")["scale_across"] == 1.01


_ALONG = {CalibrationKey.SCALE_ALONG: 0.98}


@pytest.mark.os_agnostic
def test_a_file_with_a_byte_order_mark_is_read_as_every_loader_reads_it(tmp_path: Path) -> None:
    """Editors on Windows write one, and the file loads everywhere except where it is edited."""
    path = tmp_path / "config.toml"
    path.write_bytes(b"\xef\xbb\xbf" + _COMMENTED.encode("utf-8"))
    write_printer_values([path], printer="", values=_ALONG)
    written = path.read_text(encoding="utf-8")
    assert "scale_along = 0.98" in written
    assert "# the one in the stock room" in written


@pytest.mark.os_agnostic
def test_a_file_that_is_no_toml_is_refused_naming_it(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[printer\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match=r"cannot read .*config\.toml"):
        write_printer_values([path], printer="", values=_ALONG)
    assert path.read_text(encoding="utf-8") == "[printer\n"


@pytest.mark.os_agnostic
def test_a_file_that_cannot_be_written_is_refused_naming_it_not_a_temporary_file(tmp_path: Path) -> None:
    """A file where the directory should be fails on every platform, as root too."""
    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")
    target = blocker / "config.toml"
    with pytest.raises(ConfigurationError) as caught:
        write_printer_values([target], printer="", values=_ALONG)
    assert str(target) in str(caught.value)
    assert ".tmp" not in str(caught.value)


@pytest.mark.os_agnostic
def test_a_failed_replace_leaves_no_temporary_file(tmp_path: Path) -> None:
    """A directory at the target's name fails the final rename, after the temporary file was written."""
    target = tmp_path / "config.toml"
    target.mkdir()
    with pytest.raises(ConfigurationError, match="cannot write"):
        write_printer_values([target], printer="", values=_ALONG)
    assert [path.name for path in tmp_path.iterdir()] == ["config.toml"]
