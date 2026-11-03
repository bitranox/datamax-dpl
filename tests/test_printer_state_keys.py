"""The printer state a job sets has settings keys: media, start of print, label length, feed rate, V, which to send.

Every render option that sets persistent printer state is a ``[printer]`` key of the same name, so
``[printers.<name>]`` takes it too, and ``--set`` and ``DATAMAX_DPL___PRINTER__<KEY>`` reach it like
every other key. Left unset, each one takes the model's default, so a job's bytes are what they were
before the keys existed; a label's own value wins over a configured one, and a library caller that
builds :class:`RenderOptions` itself sets them directly. A finding that tells a reader to set one
names the key of the section in use, and following that advice makes the finding go away.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
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
from datamax_dpl.application.use_cases import render_label
from datamax_dpl.composition import build_production, build_testing
from datamax_dpl.domain.enums import FindingCode, MediaSensor, PrinterSetting, SpeedCode
from datamax_dpl.domain.errors import ConfigurationError
from datamax_dpl.domain.label import Label, Text
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from click.testing import CliRunner, Result

pytestmark = pytest.mark.usefixtures("no_ambient_configuration")

REPO = Path(__file__).resolve().parents[1]
STX = b"\x02"
_PRODUCT = Label.model_validate_json((REPO / "examples" / "product-label.json").read_text(encoding="utf-8"))
_TEXT = Label(elements=[Text(x=5, y=5, text="Hi")])
#: Render options that are no printer state: whether the model's image budget applies is decided
#: by the link a command prints over, never configured.
_NOT_PRINTER_STATE = frozenset({"model_image_budget"})
#: The keys this module adds, each with a value that differs from every model's default.
_STATE_KEYS: dict[str, object] = {
    "start_of_print_mm": 20.0,
    "max_label_length_mm": 200.0,
    "feed_rate": "E",
    "label_present": "yes",
    "send_settings": ["media", "stop"],
    "media": "reflective",
}

#: sha256 of the product label's bytes, rendered through the settings with none of the keys set,
#: taken before they existed (2026-11-03): unset keys must leave every job as it was.
_BYTES_BEFORE_THE_KEYS = {
    ("allegro-2", "unknown"): "ff07964ad20dee1d3ea4989027a643acccac9231e29328e2e5a85b435429ca44",
    ("allegro-2", "yes"): "c1c8845eef23482f43397b23685af82a4b8d99a3f154fa9146c52d828bac0e85",
    ("dmx-600", "unknown"): "07302d39fbf5d2b8df94604459d31c1a0e0f6f26ccc0eb2335496419efd1d5a4",
    ("dmx-600", "yes"): "07302d39fbf5d2b8df94604459d31c1a0e0f6f26ccc0eb2335496419efd1d5a4",
    ("prodigy", "unknown"): "9d42e3e3f56a16e1868dcbd6a75f8277e514d270de1e946a47b014fc60dd7606",
    ("prodigy-max-203", "yes"): "c7e0dd1d2ac2d45c038f2d49c60595872db2d63eb5cd94363d0ed807083ba121",
    ("pe42", "yes"): "eaf62df47543cb35aeadc2217fb134ab8124460769e82e862405abf5d196934d",
    ("generic", "unknown"): "9f4a4c8c1cb7d0fbb7b289d9f86b1576940555ad93e10832c6aab1873f6e8c9b",
    ("permissive", "yes"): "e4fb5ea2885de83ce9a1e0a9cb2caab4e13a6038aeeab4e56d73dc0a8005afc9",
}


def _options(config: dict[str, Any], printer: str | None = None) -> RenderOptions:
    return render_options(printer_settings(Config(config, {}), PrinterOverrides(printer=printer)))


def _cli(cli_runner: CliRunner, *args: str, **env: str) -> Result:
    return cli_runner.invoke(cli_mod.cli, ["--json-bare", *args], obj=build_production, env=env)


def _label_file(tmp_path: Path, label: Label = _TEXT) -> str:
    path = tmp_path / "label.json"
    path.write_text(label.model_dump_json(), encoding="utf-8")
    return str(path)


def _rendered(result: Result) -> bytes:
    assert result.exit_code == 0, result.output
    data: str = json.loads(result.stdout)["data"]["dpl_base64"]
    return base64.b64decode(data)


def _messages(result: Result, code: FindingCode) -> list[str]:
    return [f["message"] for f in json.loads(result.stdout)["data"]["findings"] if f["code"] == code.value]


# ======================== Every printer-state option has a key ========================


@pytest.mark.os_agnostic
def test_every_printer_state_render_option_is_a_settings_key_of_the_same_meaning() -> None:
    options = set(RenderOptions.model_fields) - _NOT_PRINTER_STATE
    assert options == set(OPTION_KEYS)
    assert set(OPTION_KEYS.values()) <= set(PrinterSettings.model_fields)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", sorted(_STATE_KEYS))
def test_each_new_key_keeps_the_render_options_name(key: str) -> None:
    assert OPTION_KEYS[key] == key


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("model", "cutter"), sorted(_BYTES_BEFORE_THE_KEYS))
def test_with_no_key_set_every_job_is_byte_for_byte_what_it_was(model: str, cutter: str) -> None:
    options = _options({"printer": {"model": model, "cutter": cutter, "image_module": "A"}})
    digest = hashlib.sha256(render_label(_PRODUCT, options).data).hexdigest()
    assert digest == _BYTES_BEFORE_THE_KEYS[model, cutter]


@pytest.mark.os_agnostic
def test_unset_keys_leave_each_render_option_at_the_model_default() -> None:
    options = _options({"printer": {"model": "allegro-2"}})
    assert [getattr(options, key) for key in sorted(_STATE_KEYS)] == [None] * len(_STATE_KEYS)


# ======================== From the settings to the render options ========================


@pytest.mark.os_agnostic
def test_each_key_reaches_its_render_option() -> None:
    options = _options({"printer": _STATE_KEYS})
    assert options.start_of_print_mm == 20.0
    assert options.max_label_length_mm == 200.0
    assert options.feed_rate is SpeedCode("E")
    assert options.label_present is True
    assert options.send_settings == frozenset({PrinterSetting.MEDIA, PrinterSetting.STOP})
    assert options.media is MediaSensor.REFLECTIVE


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("value", "present"), [("yes", True), ("no", False), ("unknown", None)])
def test_label_present_takes_yes_no_or_unknown_like_cutter(value: str, present: bool | None) -> None:
    assert _options({"printer": {"label_present": value}}).label_present is present


@pytest.mark.os_agnostic
def test_an_empty_send_settings_sends_none_and_is_not_taken_for_unset() -> None:
    assert _options({"printer": {"send_settings": []}}).send_settings == frozenset()


@pytest.mark.os_agnostic
def test_a_named_printer_wins_over_the_printer_section_key_by_key() -> None:
    config = {
        "printer": {"start_of_print_mm": 30, "feed_rate": "C"},
        "printers": {"lager": {"start_of_print_mm": 40}},
    }
    named = _options(config, printer="lager")
    assert (named.start_of_print_mm, named.feed_rate) == (40.0, SpeedCode("C"))
    assert _options(config).start_of_print_mm == 30.0


@pytest.mark.os_agnostic
def test_the_labels_own_media_wins_over_the_configured_one(cli_runner: CliRunner, tmp_path: Path) -> None:
    label = _label_file(tmp_path, Label(media=MediaSensor.GAP, elements=[Text(x=5, y=5, text="Hi")]))
    data = _rendered(_cli(cli_runner, "--set", "printer.media=reflective", "render", label, "--model", "allegro-2"))
    assert STX + b"e" in data
    assert STX + b"r" not in data


@pytest.mark.os_agnostic
def test_a_start_of_print_set_with_set_is_sent(cli_runner: CliRunner, tmp_path: Path) -> None:
    data = _rendered(
        _cli(
            cli_runner,
            "--set",
            "printer.start_of_print_mm=20",
            "render",
            _label_file(tmp_path),
            "--model",
            "allegro-2",
        )
    )
    assert STX + b"O0079" in data
    assert STX + b"O0220" not in data


@pytest.mark.os_agnostic
def test_a_feed_rate_set_in_the_environment_is_sent(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = _cli(
        cli_runner, "render", _label_file(tmp_path), "--model", "allegro-2", DATAMAX_DPL___PRINTER__FEED_RATE="E"
    )
    data = _rendered(result)
    assert STX + b"SE" in data
    assert STX + b"SC" not in data


@pytest.mark.os_agnostic
def test_send_settings_set_in_the_environment_as_a_list_leaves_out_the_rest(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    result = _cli(
        cli_runner,
        "render",
        _label_file(tmp_path),
        "--model",
        "allegro-2",
        DATAMAX_DPL___PRINTER__SEND_SETTINGS='["media"]',
    )
    data = _rendered(result)
    assert STX + b"c0000" in data
    assert STX + b"O" not in data
    assert STX + b"M" not in data


@pytest.mark.os_agnostic
def test_label_present_and_cutter_build_the_software_switches(cli_runner: CliRunner, tmp_path: Path) -> None:
    args = ["--set", "printer.cutter=yes", "--set", "printer.label_present=yes"]
    data = _rendered(_cli(cli_runner, *args, "render", _label_file(tmp_path), "--model", "dmx-600"))
    assert STX + b"V5" in data


# ======================== Bounds, as RenderOptions has them ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("start_of_print_mm", 12.6, "greater than or equal to 12.7"),
        ("start_of_print_mm", 2540, "less than or equal to 2539.746"),
        ("max_label_length_mm", 126.9, "greater than or equal to 127"),
        ("max_label_length_mm", 2540, "less than or equal to 2539.746"),
        ("start_of_print_mm", "inf", "finite number"),
        ("start_of_print_mm", True, "needs a number, got true"),
        ("max_label_length_mm", False, "needs a number, got false"),
        ("feed_rate", "Z", "Input should be"),
        ("media", "magnetic", "Input should be"),
        ("label_present", True, "Input should be"),
        ("send_settings", ["media", "colour"], "Input should be"),
    ],
)
def test_a_value_out_of_bounds_is_refused_naming_its_key(key: str, value: object, reason: str) -> None:
    with pytest.raises(ConfigurationError, match=rf"^printer\.{key}(\.\d+)?: .*{reason}"):
        _options({"printer": {key: value}})


@pytest.mark.os_agnostic
def test_a_named_printers_bad_value_is_refused_naming_its_own_section() -> None:
    with pytest.raises(ConfigurationError, match=r"^printers\.lager\.start_of_print_mm: "):
        _options({"printers": {"lager": {"start_of_print_mm": 1}}}, printer="lager")


@pytest.mark.os_agnostic
def test_a_start_of_print_from_text_is_read_as_a_number() -> None:
    """``.env`` and ``--set`` deliver ``20`` as text; numeric text stays a number, as for every other key."""
    assert _options({"printer": {"start_of_print_mm": "20.5"}}).start_of_print_mm == 20.5


# ======================== The advice names the key, and following it works ========================

_UNKNOWN_ON_GENERIC = {"start_of_print_mm": 20, "max_label_length_mm": 200, "feed_rate": "C"}


def _unknown(options: RenderOptions) -> list[str]:
    result = render_label(_TEXT, options)
    return [f.message for f in result.findings if f.code is FindingCode.PRINTER_SETTINGS_UNKNOWN]


@pytest.mark.os_agnostic
def test_a_library_caller_following_the_advice_gets_no_finding() -> None:
    [message] = _unknown(RenderOptions(model="generic"))
    for field in _UNKNOWN_ON_GENERIC:
        assert f"RenderOptions.{field}" in message
    assert _unknown(RenderOptions.model_validate({"model": "generic", **_UNKNOWN_ON_GENERIC})) == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("section", "printer"), [("printer", []), ("printers.lager", ["--printer", "lager"])])
def test_the_cli_names_the_key_of_the_section_in_use_and_following_it_ends_the_finding(
    cli_runner: CliRunner, tmp_path: Path, section: str, printer: list[str]
) -> None:
    named = ["--set", "printers.lager.uri=tcp://192.0.2.60:9100"]
    command = ["validate", _label_file(tmp_path), "--model", "generic", *printer]
    told = _cli(cli_runner, *named, *command)
    assert told.exit_code == 0, told.output
    [message] = _messages(told, FindingCode.PRINTER_SETTINGS_UNKNOWN)
    header = f"[{section}]"
    advice = [f"--set={section}.{key}={value}" for key, value in _UNKNOWN_ON_GENERIC.items()]
    for key in _UNKNOWN_ON_GENERIC:
        assert f"{header} {key}" in message
    followed = _cli(cli_runner, *named, *advice, *command)
    assert followed.exit_code == 0, followed.output
    assert _messages(followed, FindingCode.PRINTER_SETTINGS_UNKNOWN) == []


@pytest.mark.os_agnostic
def test_an_unknown_label_present_names_its_key_and_setting_it_sends_the_switches(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    command = ["--set", "printer.cutter=yes", "render", _label_file(tmp_path), "--model", "dmx-600"]
    told = _cli(cli_runner, *command)
    [message] = _messages(told, FindingCode.SOFTWARE_SWITCHES_NOT_SENT)
    assert "[printer] label_present" in message
    followed = _cli(cli_runner, "--set", "printer.label_present=no", *command)
    assert _messages(followed, FindingCode.SOFTWARE_SWITCHES_NOT_SENT) == []
    assert STX + b"V1" in _rendered(followed)


@pytest.mark.os_agnostic
def test_an_unknown_cutter_names_its_key_for_the_software_switches(cli_runner: CliRunner, tmp_path: Path) -> None:
    told = _cli(cli_runner, "render", _label_file(tmp_path), "--model", "allegro-2")
    [message] = _messages(told, FindingCode.SOFTWARE_SWITCHES_NOT_SENT)
    assert "[printer] cutter" in message


@pytest.mark.os_agnostic
def test_the_testing_composition_reads_the_keys_too(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = cli_runner.invoke(
        cli_mod.cli,
        ["--json-bare", "--set", "printer.max_label_length_mm=200", "render", _label_file(tmp_path)],
        obj=build_testing,
    )
    assert STX + b"M0787" in _rendered(result)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("setting", "code", "label"),
    [
        ("max_label_length_mm=201", FindingCode.MAX_LABEL_LENGTH_TOO_SHORT, Label(height=200, elements=_TEXT.elements)),
        ("feed_rate=A", FindingCode.FEED_RATE_OUT_OF_RANGE, _TEXT),
        (
            'send_settings=["stop"]',
            FindingCode.LABEL_SETTING_NOT_SENT,
            Label(media=MediaSensor.GAP, elements=_TEXT.elements),
        ),
    ],
)
def test_a_finding_about_a_configured_setting_names_its_key(
    cli_runner: CliRunner, tmp_path: Path, setting: str, code: FindingCode, label: Label
) -> None:
    key = setting.split("=", 1)[0]
    result = _cli(
        cli_runner, "--set", f"printer.{setting}", "validate", _label_file(tmp_path, label), "--model", "dmx-600"
    )
    [message] = _messages(result, code)
    assert f"[printer] {key}" in message


@pytest.mark.os_agnostic
def test_the_shipped_file_documents_each_key_commented_out_and_its_example_is_valid() -> None:
    shipped = REPO / "src" / "datamax_dpl" / "adapters" / "config" / "defaultconfig.d" / "50-printer.toml"
    text = shipped.read_text(encoding="utf-8")
    examples = {key: re.findall(rf"^# ({key} = .+)$", text, flags=re.MULTILINE) for key in _STATE_KEYS}
    assert all(len(found) == 1 for found in examples.values()), examples
    uncommented = rtoml.loads("\n".join(found[0] for found in examples.values()))
    assert set(uncommented) == set(_STATE_KEYS)
    PrinterSettings.model_validate(uncommented)
    assert not set(_STATE_KEYS) & set(rtoml.loads(text)["printer"])
