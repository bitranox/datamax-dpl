"""`calibrate print` sends the two calibration labels; `calibrate set` stores what was measured on them."""

from __future__ import annotations

import dataclasses
import json
import re
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
    lengths = ("--printer", "lager", "--axis", "along", "--along-length", "180")
    _calibrate(cli_runner, config_cli_context(_lager(plain, scale_along=1.0)), *lengths)
    _calibrate(cli_runner, config_cli_context(_lager(scaled, scale_along=0.9)), *lengths)

    # 0.5 mm thick, 180 mm long: 2 x 719 whole-dot units; divided by the 0.9 feed scale, 799 long
    assert b"L002719" in plain.read_bytes()
    assert b"L002799" in scaled.read_bytes()


@pytest.mark.os_agnostic
def test_an_along_bar_past_the_format_length_is_refused_naming_the_along_length_that_fits(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    """At feed scale 0.9 a 200 mm bar needs more rows than one Allegro 2 format holds."""
    target = tmp_path / "out.dpl"
    factory = config_cli_context(_lager(target, scale_along=0.9))

    refused = _calibrate(cli_runner, factory, "--printer", "lager", "--axis", "along", "--along-length", "200")

    assert refused.exit_code == 1, refused.output
    data = json.loads(refused.stdout)["data"]
    assert [f["code"] for f in data["findings"] if f["severity"] == "error"] == ["format_past_max_length"]
    named = re.search(r"--along-length (\d+)", data["hint"])
    assert named is not None, data["hint"]
    fits = int(named.group(1))
    assert fits % 10 == 0
    assert data["along_length_that_fits"] == fits
    assert not target.exists()
    longer = _calibrate(cli_runner, factory, "--printer", "lager", "--axis", "along", "--along-length", str(fits + 10))
    assert longer.exit_code == 1, longer.output
    assert (
        _calibrate(cli_runner, factory, "--printer", "lager", "--axis", "along", "--along-length", str(fits)).exit_code
        == 0
    )
    assert target.read_bytes().count(_FORMAT_START) == 1


def _along_only(target: Path, model: str) -> dict[str, Any]:
    """A printer that sets no calibration of its own, so the model's defaults print."""
    return {"printers": {"lager": {"model": model, "uri": f"file:{target}"}}}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("model", "fits"), [("allegro-2", "190"), ("dmx-600", "200")])
def test_without_an_along_length_the_bar_is_the_longest_that_fits_the_model(
    cli_runner: CliRunner,
    config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]],
    tmp_path: Path,
    model: str,
    fits: str,
) -> None:
    """At most 200 mm; the Allegro 2's 1640 rows at its 0.976 feed scale hold a 190 mm bar."""
    defaulted, given = tmp_path / "defaulted.dpl", tmp_path / "given.dpl"
    size = ("--printer", "lager", "--axis", "along", "--width", "100", "--height", "55")

    result = _calibrate(cli_runner, config_cli_context(_along_only(defaulted, model)), *size)
    _calibrate(cli_runner, config_cli_context(_along_only(given, model)), *size, "--along-length", fits)

    assert result.exit_code == 0, result.output
    assert defaulted.read_bytes() == given.read_bytes()


@pytest.mark.os_agnostic
def test_an_explicit_along_length_that_does_not_fit_is_still_refused_naming_the_one_that_does(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"
    args = ("--printer", "lager", "--axis", "along", "--width", "100", "--along-length", "200")

    refused = _calibrate(cli_runner, config_cli_context(_along_only(target, "allegro-2")), *args)

    assert refused.exit_code == 1, refused.output
    data = json.loads(refused.stdout)["data"]
    assert data["along_length_that_fits"] == 190
    assert "--along-length 190" in data["hint"]
    assert not target.exists()


@pytest.mark.os_agnostic
def test_the_refusal_says_plainly_when_not_even_the_shortest_along_bar_fits(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    factory = config_cli_context(_lager(tmp_path / "out.dpl", max_format_rows=50))

    refused = _calibrate(cli_runner, factory, "--printer", "lager", "--axis", "along")

    data = json.loads(refused.stdout)["data"]
    assert refused.exit_code == 1, refused.output
    assert data["along_length_that_fits"] is None
    assert "20 mm" in data["hint"]
    assert "--along-length" not in data["hint"]


@pytest.mark.os_agnostic
def test_both_labels_with_only_the_along_bar_too_long_name_the_along_length(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    factory = config_cli_context(_lager(tmp_path / "out.dpl", scale_along=0.9))

    refused = _calibrate(cli_runner, factory, "--printer", "lager", "--axis", "both", "--along-length", "200")

    data = json.loads(refused.stdout)["data"]
    assert refused.exit_code == 1, refused.output
    assert data["along_length_that_fits"] is not None
    assert "--along-length" in data["hint"]


@pytest.mark.os_agnostic
def test_both_labels_name_no_along_length_when_the_across_label_is_refused_too(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    """A retry with a shorter along bar would still be refused for the across label."""
    factory = config_cli_context(_lager(tmp_path / "out.dpl", scale_along=0.9))

    args = ("--printer", "lager", "--axis", "both", "--height", "300", "--along-length", "200")
    refused = _calibrate(cli_runner, factory, *args)

    data = json.loads(refused.stdout)["data"]
    assert refused.exit_code == 1, refused.output
    assert len([f for f in data["findings"] if f["severity"] == "error"]) == 2
    assert (data["hint"], data["along_length_that_fits"]) == (None, None)


@pytest.mark.os_agnostic
def test_a_refusal_for_another_reason_names_no_along_length(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    result = _calibrate(
        cli_runner, config_cli_context(_lager(tmp_path / "out.dpl")), "--printer", "lager", "--width", "200"
    )

    data = json.loads(result.stdout)["data"]
    assert result.exit_code == 1
    assert (data["hint"], data["along_length_that_fits"]) == (None, None)


@pytest.mark.os_agnostic
def test_the_human_refusal_names_the_along_length_that_fits(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    factory = config_cli_context(_lager(tmp_path / "out.dpl", scale_along=0.9))

    result = cli_runner.invoke(
        cli_mod.cli,
        ["calibrate", "print", "--printer", "lager", "--axis", "along", "--along-length", "200"],
        obj=factory,
    )

    assert result.exit_code == 1
    assert "REFUSED: nothing sent" in result.stdout
    assert "--along-length" in result.stdout
    assert "calibrate set" in result.stdout


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

    result = _set(cli_runner, _user_layer(base), "--printer", "lager", "--along", "189")

    # against the 190 mm bar 'calibrate print' printed at 0.977: 0.977 x 189 / 190 is 0.9719
    assert result.exit_code == 0, result.output
    assert _stored(base, "printers", "lager", "scale_along") == 0.9719
    assert "# Allegro 2 behind the print server" in base.read_text(encoding="utf-8")
    data = json.loads(result.stdout)["data"]
    assert data["file"] == str(base)
    assert data["changes"] == [
        {"key": "scale_along", "measured": 189.0, "nominal": 190.0, "old": 0.977, "new": 0.9719, "effective": 0.9719}
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
    base.write_text('[printer]\nmodel = "allegro-2"\nlabel_width = 100\n', encoding="utf-8")

    result = _set(cli_runner, _user_layer(base), "--along", "186")

    # the 190 mm bar printed with the Allegro 2's measured 0.976, so the new factor is 0.976 x 186 / 190
    assert result.exit_code == 0, result.output
    assert _stored(base, "printer", "scale_along") == 0.9555


@pytest.mark.os_agnostic
def test_a_higher_layer_that_still_wins_is_reported_on_stderr(cli_runner: CliRunner, tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_LAGER_TOML, encoding="utf-8")
    factory = _user_layer(base, override={"printers": {"lager": {"scale_along": 0.95}}})

    result = _set(cli_runner, factory, "--printer", "lager", "--along", "189")

    # The 190 mm bar printed with the factor in force, 0.95, so the new one is 0.95 x 189 / 190.

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["changes"][0]["effective"] == 0.95
    assert len(data["warnings"]) == 1
    assert "scale_along is 0.95, not 0.945" in result.stderr
    assert _stored(base, "printers", "lager", "scale_along") == 0.945


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
def test_an_along_bar_measured_at_the_length_print_defaults_to_keeps_its_scale(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The Allegro 2 at 0.976 prints a 190 mm bar by default; 190 mm measured means 0.976 was right."""
    base = tmp_path / "config.toml"
    base.write_text('[printers.lager]\nmodel = "allegro-2"\nlabel_width = 100\n', encoding="utf-8")

    result = _set(cli_runner, _user_layer(base), "--printer", "lager", "--along", "190")

    assert result.exit_code == 0, result.output
    (change,) = json.loads(result.stdout)["data"]["changes"]
    assert (change["nominal"], change["new"]) == (190.0, 0.976)


@pytest.mark.os_agnostic
def test_a_model_without_a_format_limit_measures_the_along_bar_against_200_mm_without_a_width(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    base = tmp_path / "config.toml"
    base.write_text('[printers.lager]\nmodel = "dmx-600"\n', encoding="utf-8")

    result = _set(cli_runner, _user_layer(base), "--printer", "lager", "--along", "199")

    assert result.exit_code == 0, result.output
    (change,) = json.loads(result.stdout)["data"]["changes"]
    assert (change["nominal"], change["new"]) == (200.0, 0.995)


@pytest.mark.os_agnostic
def test_an_along_measurement_with_a_format_limit_and_no_known_width_is_refused(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The bar print printed depends on the label width it was built at; set cannot guess it."""
    base = tmp_path / "config.toml"
    base.write_text('[printers.lager]\nmodel = "allegro-2"\n', encoding="utf-8")

    result = _set(cli_runner, _user_layer(base), "--printer", "lager", "--along", "190")

    assert result.exit_code == 2, result.output
    message = json.loads(result.stdout)["message"]
    assert "--along-length" in message
    assert "--width" in message
    assert base.read_text(encoding="utf-8") == '[printers.lager]\nmodel = "allegro-2"\n'


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

    plain = config_cli_context(_lager(plain_target, offset_along=0.0))
    _calibrate(cli_runner, plain, "--printer", "lager", "--axis", "across")
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
    # the label printed with the Allegro 2's measured -0.25 mm along, so the borders move it from there
    assert _stored(base, "printers", "lager", "offset_along") == -0.5
    assert _stored(base, "printers", "lager", "offset_across") == 0.2
    offsets = json.loads(result.stdout)["data"]["offsets"]
    assert [(o["key"], o["low"], o["high"], o["old"], o["new"]) for o in offsets] == [
        ("offset_across", 1.8, 2.2, 0.0, 0.2),
        ("offset_along", 2.3, 1.8, -0.25, -0.5),
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


# ======================== the model's measured defaults ========================

_UNCALIBRATED_LAGER_TOML = """\
[printers.lager]
model = "allegro-2"
label_width = 100
"""


@pytest.mark.os_agnostic
def test_print_reports_and_titles_the_models_defaults_when_the_printer_sets_none(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    target = tmp_path / "out.dpl"
    printer = {"model": "allegro-2", "uri": f"file:{target}", "label_width": 100, "label_height": 55}

    result = _calibrate(cli_runner, config_cli_context({"printers": {"lager": printer}}), "--printer", "lager")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    calibration = [data[key] for key in ("scale_across", "scale_along", "offset_across", "offset_along")]
    assert calibration == [1.0, 0.976, 0.0, -0.25]
    across, along = target.read_bytes().split(_FORMAT_START)[1:]
    assert b"OFFSET ACROSS +0.00 ALONG -0.25" in across
    assert b"LAGER SCALE 0.9760" in along


@pytest.mark.os_agnostic
def test_set_starts_from_the_models_measured_scale_when_the_printer_sets_none(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    base = tmp_path / "config.toml"
    base.write_text(_UNCALIBRATED_LAGER_TOML, encoding="utf-8")

    result = _set(cli_runner, _user_layer(base), "--printer", "lager", "--along", "189")

    # the 190 mm along bar printed with the Allegro 2's measured 0.976: 0.976 x 189 / 190 is 0.9709
    assert result.exit_code == 0, result.output
    assert _stored(base, "printers", "lager", "scale_along") == 0.9709
    data = json.loads(result.stdout)["data"]
    assert data["changes"] == [
        {"key": "scale_along", "measured": 189.0, "nominal": 190.0, "old": 0.976, "new": 0.9709, "effective": 0.9709}
    ]
    assert data["warnings"] == []
    assert result.stderr == ""


@pytest.mark.os_agnostic
def test_a_written_value_no_layer_reads_back_reports_the_models_default_in_force(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """A loaded configuration without the key means the model's default, not an unreadable one."""
    base, elsewhere = tmp_path / "config.toml", tmp_path / "elsewhere.toml"
    base.write_text(_UNCALIBRATED_LAGER_TOML, encoding="utf-8")
    elsewhere.write_text(_UNCALIBRATED_LAGER_TOML, encoding="utf-8")

    def reads_base_writes_elsewhere() -> AppServices:
        return dataclasses.replace(_user_layer(base)(), set_printer_values=_user_layer(elsewhere)().set_printer_values)

    result = _set(cli_runner, reads_base_writes_elsewhere, "--printer", "lager", "--along", "189")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["changes"][0]["effective"] == 0.976
    assert "could not be read back" not in result.stderr
    assert "scale_along is 0.976, not 0.9709" in result.stderr
