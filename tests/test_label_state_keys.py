"""Seven label settings have settings keys: heat, the three speeds, both offsets and the present distance.

Each key in ``[printer]`` or ``[printers.<name>]`` acts as if the label had set the same value in
millimetres: the same commands, range checks and findings. Unset, the printer keeps its own value, as
before the keys existed, and no model default is sent; a value in the label itself still wins.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import TYPE_CHECKING, Any

import pytest
import rtoml
from lib_layered_config import Config

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.settings import (
    OPTION_KEYS,
    PrinterOverrides,
    PrinterSettings,
    printer_settings,
    render_options,
)
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.enums import FindingCode, Severity, SpeedCode, Unit
from datamax_dpl.domain.errors import ConfigurationError
from datamax_dpl.domain.label import Label, Text
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.render import RenderResult, build_job
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.reset_label import calibration_job
from datamax_dpl.domain.units import MM_PER_INCH

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner, Result

pytestmark = pytest.mark.usefixtures("no_ambient_configuration")

#: Has every one of the seven: heat to 30, print and slew C-S, backup C-I, present distance.
MODEL = "pe42"
TEXT = Text(x=5, y=5, text="Hi")
PLAIN = Label(width=100, height=60, elements=[TEXT])

#: Each key with a value no other key has and no model sends by default, and the label field and
#: render option it stands for. Lengths are in mm, as a label in mm writes them.
KEYS: dict[str, tuple[object, str, str]] = {
    "heat": (17, "heat", "heat"),
    "print_speed": ("D", "print_speed", "print_speed"),
    "slew_speed": ("G", "slew_speed", "slew_speed"),
    "backup_speed": ("E", "backup_speed", "backup_speed"),
    "column_offset": (3.0, "column_offset", "column_offset_mm"),
    "row_offset": (4.5, "row_offset", "row_offset_mm"),
    "present_distance": (6.0, "present_distance", "present_distance_mm"),
}


def _settings(config: dict[str, Any], printer: str | None = None) -> PrinterSettings:
    return printer_settings(Config(config, {}), PrinterOverrides(printer=printer))


def _options(config: dict[str, Any], printer: str | None = None) -> RenderOptions:
    return render_options(_settings(config, printer))


def _label_value(key: str) -> object:
    value, _, _ = KEYS[key]
    return SpeedCode(value) if key.endswith("_speed") else value


def _labelled(key: str, *, unit: Unit = Unit.MM) -> Label:
    """The plain label with ``key`` set in the label itself."""
    _, field, _ = KEYS[key]
    return PLAIN.model_copy(update={field: _label_value(key), "unit": unit})


def _all_seven_labelled() -> Label:
    return PLAIN.model_copy(update={field: _label_value(key) for key, (_, field, _) in KEYS.items()})


def _codes(result: RenderResult) -> list[FindingCode]:
    return [finding.code for finding in result.findings]


def _cli(runner: CliRunner, *args: str) -> Result:
    return runner.invoke(cli_mod.cli, ["--json-bare", *args], obj=lambda: build_testing(printer=None))


def _shipped_printer_toml() -> str:
    shipped = resources.files("datamax_dpl") / "adapters" / "config" / "defaultconfig.d" / "50-printer.toml"
    return shipped.read_text(encoding="utf-8")


# ======================== unset: the printer keeps its own ========================


@pytest.mark.os_agnostic
def test_every_key_is_unset_by_default_and_reaches_an_unset_render_option() -> None:
    settings = PrinterSettings()
    options = RenderOptions()

    assert {key: getattr(settings, key) for key in KEYS} == dict.fromkeys(KEYS)
    assert {option: getattr(options, option) for _, _, option in KEYS.values()} == {
        option: None for _, _, option in KEYS.values()
    }


@pytest.mark.os_agnostic
def test_with_no_key_set_the_job_sends_none_of_the_seven_commands() -> None:
    """No model default goes out for an unset key: no H, P, S, B, C or R line, no <STX>Kf."""
    job = build_job(PLAIN, _options({"printer": {"model": MODEL}})).data
    state_line = re.compile(rb"H\d\d|[PSp][A-Z]|[CR]\d{4}")

    assert not [line for line in job.split(b"\r") if state_line.fullmatch(line)]
    assert b"\x02Kf" not in job
    # The pattern finds each command where a label sets it, so its silence above means something.
    assert (
        len(
            [
                line
                for line in build_job(_all_seven_labelled(), RenderOptions(model=MODEL)).data.split(b"\r")
                if state_line.fullmatch(line)
            ]
        )
        == 6
    )
    assert job == build_job(PLAIN, RenderOptions(model=MODEL)).data


# ======================== each key: as if the label had set it in mm ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", sorted(KEYS))
def test_each_key_sends_what_the_label_setting_it_in_mm_sends(key: str) -> None:
    value, _, _ = KEYS[key]
    configured = build_job(PLAIN, _options({"printer": {"model": MODEL, key: value}}))

    assert configured.data == build_job(_labelled(key), RenderOptions(model=MODEL)).data
    assert configured.data != build_job(PLAIN, RenderOptions(model=MODEL)).data


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", sorted(KEYS))
def test_a_named_printer_section_sets_each_key_too(key: str) -> None:
    value, _, _ = KEYS[key]
    config = {"printer": {"model": MODEL}, "printers": {"lager": {key: value}}}

    assert (
        build_job(PLAIN, _options(config, "lager")).data == build_job(_labelled(key), RenderOptions(model=MODEL)).data
    )
    assert getattr(_settings(config), key) is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", sorted(KEYS))
def test_the_library_sets_each_key_on_its_render_option(key: str) -> None:
    _, _, option = KEYS[key]
    options = RenderOptions.model_validate({"model": MODEL, option: _label_value(key)})

    assert OPTION_KEYS[option] == key
    assert build_job(PLAIN, options).data == build_job(_labelled(key), RenderOptions(model=MODEL)).data


@pytest.mark.os_agnostic
def test_all_seven_keys_together_send_what_a_label_with_all_seven_sends() -> None:
    config = {"printer": {"model": MODEL, **{key: value for key, (value, _, _) in KEYS.items()}}}

    assert build_job(PLAIN, _options(config)).data == build_job(_all_seven_labelled(), RenderOptions(model=MODEL)).data


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", ["column_offset", "row_offset", "present_distance"])
@pytest.mark.parametrize("unit", [Unit.INCH, Unit.DOTS])
def test_a_configured_length_is_millimetres_whatever_unit_the_label_uses(key: str, unit: Unit) -> None:
    """A label in inches or dots that sets the same length in its own unit sends the same job."""
    value, field, _ = KEYS[key]
    assert isinstance(value, float)
    inches = value / MM_PER_INCH
    in_unit = PLAIN.model_copy(update={"unit": unit})
    same_length = in_unit.model_copy(update={field: inches if unit is Unit.INCH else inches * get_profile(MODEL).dpi})

    configured = build_job(in_unit, _options({"printer": {"model": MODEL, key: value}})).data

    assert configured == build_job(same_length, RenderOptions(model=MODEL)).data
    assert configured != build_job(in_unit, RenderOptions(model=MODEL)).data


# ======================== the label's own value wins ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("key", "label_value", "command"),
    [
        ("heat", 12, b"\rH12\r"),
        ("print_speed", SpeedCode("K"), b"\rPK\r"),
        ("slew_speed", SpeedCode("K"), b"\rSK\r"),
        ("backup_speed", SpeedCode("C"), b"\rpC\r"),
        ("column_offset", 1.0, b"\rC0010\r"),
        ("row_offset", 1.0, b"\rR0010\r"),
        ("present_distance", 10.0, b"\x02Kf0039"),
    ],
)
def test_a_value_in_the_label_wins_over_the_configured_one(key: str, label_value: object, command: bytes) -> None:
    value, field, _ = KEYS[key]
    label = PLAIN.model_copy(update={field: label_value})
    job = build_job(label, _options({"printer": {"model": MODEL, key: value}})).data

    assert command in job
    assert job == build_job(label, RenderOptions(model=MODEL)).data


# ======================== the same range checks and findings as the label's ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("model", "key", "value", "code"),
    [
        ("prodigy", "heat", 25, FindingCode.HEAT_OUT_OF_RANGE),
        ("allegro-2", "print_speed", "K", FindingCode.SPEED_OUT_OF_RANGE),
        ("prodigy", "backup_speed", "C", FindingCode.SPEED_UNSUPPORTED),
        ("allegro-2", "present_distance", 6.0, FindingCode.PRESENT_DISTANCE_UNSUPPORTED),
        (MODEL, "present_distance", 3000.0, FindingCode.PRESENT_DISTANCE_TOO_LARGE),
        (MODEL, "column_offset", 2000.0, FindingCode.DISTANCE_TOO_LARGE),
    ],
)
def test_a_configured_value_meets_the_label_value_s_range_check(model: str, key: str, value: object, code: str) -> None:
    configured = build_job(PLAIN, _options({"printer": {"model": model, key: value}}))
    labelled = build_job(_labelled_with(key, value), RenderOptions(model=model))

    assert code in _codes(configured)
    assert configured.data == labelled.data
    [finding] = [f for f in configured.findings if f.code == code]
    [label_finding] = [f for f in labelled.findings if f.code == code]
    assert finding.severity is label_finding.severity


def _labelled_with(key: str, value: object) -> Label:
    _, field, _ = KEYS[key]
    return PLAIN.model_copy(update={field: SpeedCode(value) if key.endswith("_speed") else value})


@pytest.mark.os_agnostic
def test_a_finding_about_a_configured_value_names_its_option_and_the_cli_names_its_key(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    configured = build_job(PLAIN, _options({"printer": {"model": "prodigy", "heat": 25}}))
    [finding] = [f for f in configured.findings if f.code is FindingCode.HEAT_OUT_OF_RANGE]
    path = tmp_path / "label.json"
    path.write_text(PLAIN.model_dump_json(), encoding="utf-8")

    result = _cli(
        cli_runner, "--set", "printers.lager.heat=25", "render", str(path), "--model", "prodigy", "--printer", "lager"
    )

    assert "RenderOptions.heat" in finding.message
    [message] = [
        f["message"] for f in json.loads(result.stdout)["data"]["findings"] if f["code"] == "heat_out_of_range"
    ]
    assert "[printers.lager] heat" in message


@pytest.mark.os_agnostic
def test_a_configured_present_distance_sent_as_a_stop_replaces_the_configured_stop() -> None:
    """On a model that sends a present distance as a stop position, one stop command goes out, not two."""
    options = _options({"printer": {"model": "allegro-2", "present_distance": 6.0, "stop_position": 30.0}})
    labelled = PLAIN.model_copy(update={"present_distance": 6.0})

    job = build_job(PLAIN, options).data

    assert job.count(b"\x02f") == 1
    assert job == build_job(labelled, RenderOptions(model="allegro-2", stop_position_mm=30.0)).data


@pytest.mark.os_agnostic
def test_send_settings_without_stop_leaves_a_configured_present_distance_out_and_says_so() -> None:
    options = _options({"printer": {"model": MODEL, "present_distance": 6.0, "send_settings": ["media"]}})

    result = build_job(PLAIN, options)

    assert b"\x02Kf" not in result.data
    [finding] = [f for f in result.findings if f.code is FindingCode.LABEL_SETTING_NOT_SENT]
    assert "RenderOptions.present_distance_mm" in finding.message
    assert finding.severity is Severity.INFO


# ======================== the calibration label ========================


@pytest.mark.os_agnostic
def test_the_calibration_label_keeps_the_configured_printer_settings_and_drops_the_offsets() -> None:
    """As it keeps the label's heat, speeds and present distance and drops its format offsets."""
    config = {"printer": {"model": MODEL, **{key: value for key, (value, _, _) in KEYS.items()}}}

    configured = calibration_job(PLAIN, _options(config), text="Feed")

    assert configured.data == calibration_job(_all_seven_labelled(), RenderOptions(model=MODEL), text="Feed").data
    assert b"\rH17\r" in configured.data
    assert not re.search(rb"\r[CR]\d{4}\r", configured.data)


# ======================== settings: the label's types and bounds ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("heat", 31, "less than or equal to 30"),
        ("heat", -1, "greater than or equal to 0"),
        ("heat", True, "needs a number"),
        ("print_speed", "Z", "Input should be"),
        ("slew_speed", 3, "Input should be"),
        ("backup_speed", "", "Input should be"),
        ("column_offset", -0.5, "greater than or equal to 0"),
        ("row_offset", float("inf"), "finite number|less than or equal to"),
        ("column_offset", True, "needs a number"),
        ("present_distance", 0, "greater than 0"),
        ("present_distance", 1e308, "less than or equal to 100000"),
    ],
)
def test_a_bad_value_is_refused_naming_its_key(key: str, value: object, reason: str) -> None:
    with pytest.raises(ConfigurationError, match=rf"^printer\.{key}: .*({reason})"):
        _settings({"printer": {key: value}})


@pytest.mark.os_agnostic
def test_lengths_and_heat_read_numeric_text_as_every_key_does() -> None:
    settings = _settings({"printer": {"heat": "17", "column_offset": "3.0", "present_distance": "6"}})

    assert (settings.heat, settings.column_offset, settings.present_distance) == (17, 3.0, 6.0)


@pytest.mark.os_agnostic
def test_the_shipped_file_documents_each_key_commented_out_with_a_valid_example() -> None:
    text = _shipped_printer_toml()
    examples = {key: re.findall(rf"^# ({key} = .+)$", text, flags=re.MULTILINE) for key in KEYS}
    variables = {
        key: re.findall(rf"^# Environment Variable: DATAMAX_DPL___PRINTER__{key.upper()}=", text, re.M) for key in KEYS
    }

    assert all(len(found) == 1 for found in examples.values()), examples
    assert all(len(found) == 1 for found in variables.values()), variables
    uncommented = rtoml.loads("\n".join(found[0] for found in examples.values()))
    PrinterSettings.model_validate(uncommented)
    assert not set(KEYS) & set(rtoml.loads(text)["printer"])
