"""`calibrate print` sends the two calibration labels; `calibrate set` stores what was measured on them."""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any

import pytest
import tomlkit
from lib_layered_config import Config

from datamax_dpl.adapters import cli as cli_mod
from datamax_dpl.adapters.config.user_edit import write_printer_values
from datamax_dpl.composition import AppServices, build_testing

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from click.testing import CliRunner, Result

_FORMAT_START = b"\x02L"
#: The across frame on a 100 x 55 mm label in the Allegro 2's whole-dot unit at feed scale 0.977:
#: the frame stands 2 mm in from every edge, so 96 mm across is 384 units and 51 mm along is
#: 51 / 0.977 mm, 209 units.
_ALLEGRO_FRAME = b"B384209001001"


def _lager(target: Path, **extra: Any) -> dict[str, Any]:
    printer = {"model": "allegro-2", "uri": f"file:{target}", "label_width": 100, "label_height": 55}
    return {"printers": {"lager": {**printer, "scale_along": 0.977, **extra}}}


def _calibrate(cli_runner: CliRunner, factory: Callable[[], Any], *args: str) -> Result:
    return cli_runner.invoke(cli_mod.cli, ["--json", "calibrate", "print", *args], obj=factory)


@pytest.mark.os_agnostic
def test_both_labels_go_to_the_named_printer_in_one_send(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"

    result = _calibrate(cli_runner, config_cli_context(_lager(target)), "--printer", "lager")

    assert result.exit_code == 0, result.output
    sent = target.read_bytes()
    data = json.loads(result.stdout)["data"]
    assert sent.count(_FORMAT_START) == 2
    assert _ALLEGRO_FRAME in sent
    assert data["labels"] == ["across", "along"]
    assert data["name"] == "lager"
    assert data["bytes_sent"] == len(sent)


@pytest.mark.os_agnostic
def test_each_label_names_the_printer_and_the_factor_it_was_printed_with(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"

    _calibrate(cli_runner, config_cli_context(_lager(target, scale_across=0.99)), "--printer", "lager")

    across, along = target.read_bytes().split(_FORMAT_START)[1:]
    assert b"LAGER SCALE 0.9900" in across
    assert b"LAGER SCALE 0.9770" in along


@pytest.mark.os_agnostic
def test_the_printers_scale_shapes_the_along_bar(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    plain, scaled = tmp_path / "plain.dpl", tmp_path / "scaled.dpl"
    _calibrate(cli_runner, config_cli_context(_lager(plain, scale_along=1.0)), "--printer", "lager", "--axis", "along")
    _calibrate(cli_runner, config_cli_context(_lager(scaled, scale_along=0.9)), "--printer", "lager", "--axis", "along")

    # 0.5 mm thick, 200 mm long: 2 x 799 whole-dot units; divided by the 0.9 feed scale, 888 long
    assert b"L002799" in plain.read_bytes()
    assert b"L002888" in scaled.read_bytes()


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("axis", "formats"), [("across", 1), ("along", 1), ("both", 2)])
def test_axis_chooses_which_labels_are_sent(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
    axis: str,
    formats: int,
) -> None:
    target = tmp_path / "out.dpl"

    result = _calibrate(cli_runner, config_cli_context(_lager(target)), "--printer", "lager", "--axis", axis)

    assert result.exit_code == 0, result.output
    assert target.read_bytes().count(_FORMAT_START) == formats
    assert (_ALLEGRO_FRAME in target.read_bytes()) == (axis != "along")


@pytest.mark.os_agnostic
def test_size_options_beat_the_configured_label_size(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"

    args = ("--printer", "lager", "--axis", "across", "--width", "50", "--height", "30")
    _calibrate(cli_runner, config_cli_context(_lager(target)), *args)

    # a 46 x 26 mm frame on a 50 x 30 mm label: 184 units across, 26 / 0.977 mm along is 106
    assert b"B184106001001" in target.read_bytes()


@pytest.mark.os_agnostic
def test_a_missing_label_size_names_the_keys_and_sends_nothing(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"
    config = {"printers": {"lager": {"model": "allegro-2", "uri": f"file:{target}"}}}

    result = _calibrate(cli_runner, config_cli_context(config), "--printer", "lager")

    envelope = json.loads(result.stdout)
    assert result.exit_code == 2
    assert envelope["message"] == "no label width: pass --width in mm, or set label_width in [printers.lager]"
    assert not target.exists()


@pytest.mark.os_agnostic
def test_a_missing_label_height_is_named_too(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"
    config = {"printers": {"lager": {"model": "allegro-2", "uri": f"file:{target}", "label_width": 100}}}

    result = _calibrate(cli_runner, config_cli_context(config), "--printer", "lager")

    assert result.exit_code == 2
    assert "set label_height in [printers.lager]" in json.loads(result.stdout)["message"]


@pytest.mark.os_agnostic
def test_the_along_label_needs_only_the_width(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"
    config = {"printers": {"lager": {"model": "allegro-2", "uri": f"file:{target}", "label_width": 100}}}

    result = _calibrate(cli_runner, config_cli_context(config), "--printer", "lager", "--axis", "along")

    assert result.exit_code == 0, result.output


@pytest.mark.os_agnostic
def test_a_label_too_small_for_calibration_is_refused_with_exit_2(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"

    result = _calibrate(cli_runner, config_cli_context(_lager(target)), "--printer", "lager", "--width", "10")

    assert result.exit_code == 2
    # the across label needs 24 mm: its 20 mm minimum inside a frame 2 mm in from every edge
    assert "under 24 mm" in json.loads(result.stdout)["message"]
    assert not target.exists()


@pytest.mark.os_agnostic
def test_a_label_with_an_error_finding_is_refused_and_nothing_is_sent(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"

    # Ticks past the Allegro 2's 104 mm print width cannot print: an error finding in every policy.
    result = _calibrate(cli_runner, config_cli_context(_lager(target)), "--printer", "lager", "--width", "200")

    envelope = json.loads(result.stdout)
    assert result.exit_code == 1
    assert any(finding["severity"] == "error" for finding in envelope["data"]["findings"])
    assert envelope["data"]["bytes_sent"] == 0
    assert not target.exists()


# ======================== calibrate set ========================

_LAGER_TOML = """\
# stock room
[printers.lager]
model = "allegro-2"  # Allegro 2 behind the print server
scale_along = 0.977
label_width = 100
"""


def _user_layer(
    base: Path, *, override: dict[str, Any] | None = None, profiles: list[str | None] | None = None
) -> Callable[[], AppServices]:
    """Services reading and writing ``base`` as the whole configuration.

    ``override`` stands in for a higher layer; ``profiles`` collects the profile each write was for.
    """

    def read(**_kwargs: Any) -> Config:
        data: dict[str, Any] = tomlkit.parse(base.read_text(encoding="utf-8")).unwrap() if base.exists() else {}
        for section, values in (override or {}).items():
            for name, keys in values.items():
                data.setdefault(section, {}).setdefault(name, {}).update(keys)
        return Config(data, {})

    def write(*, printer: str, values: Mapping[str, float], profile: str | None = None) -> Path:
        if profiles is not None:
            profiles.append(profile)
        return write_printer_values([base], printer=printer, values=values)

    return lambda: dataclasses.replace(build_testing(), get_config=read, set_printer_values=write)


def _set(cli_runner: CliRunner, factory: Callable[[], AppServices], *args: str) -> Result:
    return cli_runner.invoke(cli_mod.cli, ["--json", "calibrate", "set", *args], obj=factory)


def _stored(base: Path, *keys: str) -> object:
    value: Any = tomlkit.parse(base.read_text(encoding="utf-8")).unwrap()
    for key in keys:
        value = value[key]
    return value


@pytest.mark.os_agnostic
def test_a_measured_along_bar_updates_the_printers_feed_scale(cli_runner: CliRunner, tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_LAGER_TOML, encoding="utf-8")

    result = _set(cli_runner, _user_layer(base), "--printer", "lager", "--along", "199")

    assert result.exit_code == 0, result.output
    assert _stored(base, "printers", "lager", "scale_along") == 0.9721
    assert "# Allegro 2 behind the print server" in base.read_text(encoding="utf-8")
    data = json.loads(result.stdout)["data"]
    assert data["file"] == str(base)
    assert data["changes"] == [
        {"key": "scale_along", "measured": 199.0, "nominal": 200.0, "old": 0.977, "new": 0.9721, "effective": 0.9721}
    ]
    assert data["warnings"] == []
    assert result.stderr == ""


@pytest.mark.os_agnostic
def test_a_measured_frame_updates_the_across_scale_against_the_frame_width(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_LAGER_TOML, encoding="utf-8")

    result = _set(cli_runner, _user_layer(base), "--printer", "lager", "--across", "95.23")

    assert result.exit_code == 0, result.output
    assert _stored(base, "printers", "lager", "scale_across") == 0.992
    assert _stored(base, "printers", "lager", "scale_along") == 0.977


@pytest.mark.os_agnostic
def test_both_measurements_at_once_with_a_shorter_bar_and_a_given_width(cli_runner: CliRunner, tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_LAGER_TOML, encoding="utf-8")

    args = ("--printer", "lager", "--across", "45.54", "--width", "50", "--along", "149", "--along-length", "150")
    result = _set(cli_runner, _user_layer(base), *args)

    assert result.exit_code == 0, result.output
    assert _stored(base, "printers", "lager", "scale_across") == 0.99
    assert _stored(base, "printers", "lager", "scale_along") == 0.9705


@pytest.mark.os_agnostic
def test_without_a_printer_name_the_printer_section_is_calibrated(cli_runner: CliRunner, tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text('[printer]\nmodel = "allegro-2"\n', encoding="utf-8")

    result = _set(cli_runner, _user_layer(base), "--along", "195")

    assert result.exit_code == 0, result.output
    assert _stored(base, "printer", "scale_along") == 0.975


@pytest.mark.os_agnostic
def test_a_higher_layer_that_still_wins_is_reported_on_stderr(cli_runner: CliRunner, tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_LAGER_TOML, encoding="utf-8")
    factory = _user_layer(base, override={"printers": {"lager": {"scale_along": 0.95}}})

    result = _set(cli_runner, factory, "--printer", "lager", "--along", "199")

    # The label printed with the factor in force, 0.95, so the new one is 0.95 x 199 / 200.

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["changes"][0]["effective"] == 0.95
    assert len(data["warnings"]) == 1
    assert "scale_along is 0.95, not 0.9452" in result.stderr
    assert _stored(base, "printers", "lager", "scale_along") == 0.9452


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--printer", "tcp://192.0.2.60:9100", "--along", "199"), "configured printer name"),
        (("--printer", "lager"), "--across"),
        (("--printer", "lager", "--along", "400"), "check the measurement"),
        (("--printer", "kasse", "--along", "199"), "no printer named 'kasse'"),
    ],
    ids=["uri", "no-measurement", "out-of-range", "unknown-name"],
)
def test_set_refuses_and_leaves_the_file_alone(
    cli_runner: CliRunner, tmp_path: Path, args: tuple[str, ...], message: str
) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_LAGER_TOML, encoding="utf-8")

    result = _set(cli_runner, _user_layer(base), *args)

    assert result.exit_code == 2, result.output
    assert message in json.loads(result.stdout)["message"]
    assert base.read_text(encoding="utf-8") == _LAGER_TOML


@pytest.mark.os_agnostic
def test_an_across_measurement_without_a_known_width_is_refused(cli_runner: CliRunner, tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text('[printers.lager]\nmodel = "allegro-2"\n', encoding="utf-8")

    result = _set(cli_runner, _user_layer(base), "--printer", "lager", "--across", "99")

    assert result.exit_code == 2
    assert "label_width" in json.loads(result.stdout)["message"]


@pytest.mark.os_agnostic
def test_the_write_goes_to_the_active_profiles_user_layer(cli_runner: CliRunner, tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_LAGER_TOML, encoding="utf-8")
    profiles: list[str | None] = []

    args = ["--json", "--profile", "lager-test", "calibrate", "set", "--printer", "lager", "--along", "199"]
    result = cli_runner.invoke(cli_mod.cli, args, obj=_user_layer(base, profiles=profiles))

    assert result.exit_code == 0, result.output
    assert profiles == ["lager-test"]


@pytest.mark.os_agnostic
def test_the_printers_offset_moves_the_calibration_print_and_is_reported(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    plain_target, moved_target = tmp_path / "plain.dpl", tmp_path / "moved.dpl"

    _calibrate(cli_runner, config_cli_context(_lager(plain_target)), "--printer", "lager", "--axis", "across")
    result = _calibrate(
        cli_runner,
        config_cli_context(_lager(moved_target, offset_along=-0.25)),
        "--printer",
        "lager",
        "--axis",
        "across",
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert (data["offset_across"], data["offset_along"]) == (0.0, -0.25)
    assert b"OFFSET" in moved_target.read_bytes()
    # with the title brought level, only the moved field positions can still differ
    assert moved_target.read_bytes() != plain_target.read_bytes().replace(b"ALONG +0.00", b"ALONG -0.25")


@pytest.mark.os_agnostic
def test_measured_borders_store_the_offsets_that_centre_the_print(cli_runner: CliRunner, tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_LAGER_TOML, encoding="utf-8")

    args = ("--printer", "lager", "--top", "1.8", "--bottom", "2.3", "--left", "1.8", "--right", "2.2")
    result = _set(cli_runner, _user_layer(base), *args)

    assert result.exit_code == 0, result.output
    assert _stored(base, "printers", "lager", "offset_along") == -0.25
    assert _stored(base, "printers", "lager", "offset_across") == 0.2
    offsets = json.loads(result.stdout)["data"]["offsets"]
    assert [(o["key"], o["low"], o["high"], o["old"], o["new"]) for o in offsets] == [
        ("offset_across", 1.8, 2.2, 0.0, 0.2),
        ("offset_along", 2.3, 1.8, 0.0, -0.25),
    ]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("given", "missing"), [(("--top", "1.8"), "--bottom"), (("--right", "2"), "--left")])
def test_a_border_without_its_opposite_side_is_refused(
    cli_runner: CliRunner, tmp_path: Path, given: tuple[str, str], missing: str
) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_LAGER_TOML, encoding="utf-8")

    result = _set(cli_runner, _user_layer(base), "--printer", "lager", *given)

    assert result.exit_code == 2
    assert missing in json.loads(result.stdout)["message"]
    assert "offset" not in base.read_text(encoding="utf-8")
