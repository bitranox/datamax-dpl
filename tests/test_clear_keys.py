"""The clears around each label's image downloads are settings keys, and the messages name them.

``[printer] clear_graphics_before`` and ``clear_graphics_after`` map onto the render options of the
same meaning; a finding that tells a reader to turn the clear before on or off names the key, and
where no clear may go at all, the ways that are left.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.settings import PrinterSettings, render_options
from datamax_dpl.application.use_cases import check_batch
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.enums import FindingCode, ImageModule
from datamax_dpl.domain.label import Image, Label, Qr, Text
from datamax_dpl.domain.profiles import list_profiles
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner, Result

pytestmark = pytest.mark.usefixtures("no_ambient_configuration")

#: An image the printer must already store: the clear before the label would remove it.
_STORED_ONLY = Label(elements=[Image(x=1, y=1, name="LOGO")])
#: A picture of each record's value, downloaded with no clear before it once that clear is off.
_QR = Label(elements=[Qr(x=1, y=1, data="{a}")])


def _files(tmp_path: Path, label: Label) -> tuple[str, str]:
    label_path = tmp_path / "label.json"
    label_path.write_text(label.model_dump_json(), encoding="utf-8")
    data_path = tmp_path / "data.csv"
    data_path.write_text("a\nA\nB\n", encoding="utf-8")
    return str(label_path), str(data_path)


def _validate(cli_runner: CliRunner, *args: str) -> Result:
    return cli_runner.invoke(cli_mod.cli, ["--json-bare", *args], obj=build_testing)


def _findings(result: Result, code: str) -> list[str]:
    return [f["message"] for f in json.loads(result.stdout)["data"]["findings"] if f["code"] == code]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("before", [True, False])
@pytest.mark.parametrize("after", [True, False])
def test_the_clear_keys_map_onto_the_render_options(before: bool, after: bool) -> None:
    options = render_options(PrinterSettings(clear_graphics_before=before, clear_graphics_after=after))
    assert (options.clear_graphics_before_label, options.clear_graphics_after_label) == (before, after)


@pytest.mark.os_agnostic
def test_both_clears_default_on() -> None:
    options = render_options(PrinterSettings())
    assert (options.clear_graphics_before_label, options.clear_graphics_after_label) == (True, True)


@pytest.mark.os_agnostic
def test_a_stored_only_image_is_refused_naming_the_key_and_prints_with_it_off(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    label, _ = _files(tmp_path, _STORED_ONLY)
    refused = _validate(cli_runner, "validate", label, "--model", "allegro-2")
    assert refused.exit_code == 1, refused.output
    [message] = _findings(refused, "stored_image_cleared")
    assert "[printer] clear_graphics_before" in message
    kept = _validate(
        cli_runner, "--set", "printer.clear_graphics_before=false", "validate", label, "--model", "allegro-2"
    )
    assert kept.exit_code == 0, kept.output
    assert _findings(kept, "image_must_be_stored")


@pytest.mark.os_agnostic
def test_a_batch_on_the_built_in_module_is_told_to_turn_the_clear_before_on_and_passes_with_it(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    label, data = _files(tmp_path, _QR)
    common = ["validate", label, "--data", data, "--model", "allegro-2"]
    refused = _validate(cli_runner, "--set", "printer.clear_graphics_before=false", *common)
    assert refused.exit_code == 1, refused.output
    [message, *_] = _findings(refused, "batch_image_names_unread")
    assert (
        "turn on the clear before each label "
        "(RenderOptions.clear_graphics_before_label; [printer] clear_graphics_before)" in message
    )
    following = _validate(cli_runner, "--set", "printer.clear_graphics_before=true", *common)
    assert following.exit_code == 0, following.output


@pytest.mark.os_agnostic
def test_a_batch_on_a_plug_in_module_is_told_the_ways_left_and_passes_with_one(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """No clear may go to a plug-in module, so turning the clear on cannot help there."""
    label, data = _files(tmp_path, _QR)
    common = ["validate", label, "--data", data, "--model", "allegro-2"]
    refused = _validate(cli_runner, "--set", "printer.image_module=B", *common)
    assert refused.exit_code == 1, refused.output
    [message, *_] = _findings(refused, "batch_image_names_unread")
    assert "two-way printer (tcp or serial)" in message
    assert (
        "leave the image module unset (RenderOptions.image_module; [printer] image_module), "
        "so the images go to module A" in message
    )
    assert "module B takes no clear" in message
    following = _validate(cli_runner, *common)
    assert following.exit_code == 0, following.output


def _key_to_set(message: str, key: str) -> str:
    """The ``--set`` path of the key ``message`` names: ``[printers.lager] k`` gives ``printers.lager.k``."""
    match = re.search(rf"\[([a-z0-9_.]+)\] {key}\b", message)
    assert match is not None, message
    return f"{match[1]}.{key}"


@pytest.mark.os_agnostic
def test_a_named_printer_is_told_its_own_key_and_following_it_passes(cli_runner: CliRunner, tmp_path: Path) -> None:
    """``--printer lager`` reads ``[printers.lager]``, where a key it sets wins over ``[printer]``."""
    label, data = _files(tmp_path, _QR)
    lager = ["--set", "printers.lager.model=allegro-2", "--set", "printers.lager.clear_graphics_before=false"]
    command = ["validate", label, "--data", data, "--printer", "lager"]
    refused = _validate(cli_runner, *lager, *command)
    assert refused.exit_code == 1, refused.output
    [message, *_] = _findings(refused, "batch_image_names_unread")
    key = _key_to_set(message, "clear_graphics_before")
    assert key == "printers.lager.clear_graphics_before"
    following = _validate(cli_runner, *lager, "--set", f"{key}=true", *command)
    assert following.exit_code == 0, following.output


def _advice(options: RenderOptions) -> str | None:
    """The way out a two-record no-clear batch is told, or None where it is not refused for its names."""
    report = check_batch(_QR, [{"a": "A"}, {"a": "B"}], options)
    told = [k.finding.message for k in report.findings if k.finding.code is FindingCode.BATCH_IMAGE_NAMES_UNREAD]
    return told[0].split("which is read before each label, or ", 1)[1] if told else None


def _refused_for_names(options: RenderOptions) -> bool:
    report = check_batch(_QR, [{"a": "A"}, {"a": "B"}], options)
    return any(k.finding.code is FindingCode.BATCH_IMAGE_NAMES_UNREAD for k in report.findings) or not report.ok


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", [profile.id for profile in list_profiles()])
@pytest.mark.parametrize("module", [None, *ImageModule], ids=lambda m: "unset" if m is None else m.value)
def test_the_way_out_a_refusal_names_works_on_every_model_and_module(model: str, module: ImageModule | None) -> None:
    """The advice and the clear ask one question, so following the advice is never refused again."""
    advice = _advice(RenderOptions(model=model, image_module=module, clear_graphics_before_label=False))
    if advice is None:
        return
    turned_on = RenderOptions(model=model, image_module=module, clear_graphics_before_label=True)
    left_unset = RenderOptions(model=model, clear_graphics_before_label=True)
    if advice.startswith("turn on the clear before each label"):
        assert not _refused_for_names(turned_on)
    elif "unset" in advice:
        assert not _refused_for_names(left_unset)
    else:
        assert advice.startswith("use a model that can clear its image module"), advice
        assert _refused_for_names(turned_on)
        assert _refused_for_names(left_unset)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", ["generic", "prodigy-plus"])
def test_a_model_with_no_clear_is_told_to_use_one_that_has_it(
    model: str, cli_runner: CliRunner, tmp_path: Path
) -> None:
    """generic has a built-in module A but no clear for it, so leaving the module unset would not help."""
    label, data = _files(tmp_path, _QR)
    refused = _validate(
        cli_runner, "--set", "printer.image_module=B", "validate", label, "--data", data, "--model", model
    )
    assert refused.exit_code == 1, refused.output
    [message, *_] = _findings(refused, "batch_image_names_unread")
    assert "unset" not in message
    assert message.endswith("use a model that can clear its image module: this one has no clear for it")


#: A stored image, and a value per record so that the batch has two labels.
_STORED_AND_TEXT = Label(elements=[Image(x=1, y=1, name="LOGO"), Text(x=1, y=40, text="{a}")])


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "command",
    [
        pytest.param(("render", "-o", "{out}"), id="render-file"),
        pytest.param(("render", "-o", "-"), id="render-stdout"),
        pytest.param(("validate",), id="validate"),
        pytest.param(("print", "--printer", "file:{out}"), id="print-one-way"),
        pytest.param(("print", "--printer", "tcp://192.0.2.60:9100"), id="print-two-way"),
    ],
)
def test_a_stored_image_the_clear_after_removed_is_refused_from_the_second_record(
    command: tuple[str, ...], cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The clear after record 0 empties the module, so record 1 would place an image that is gone."""
    label, data = _files(tmp_path, _STORED_AND_TEXT)
    out = tmp_path / "job.dpl"
    verb, *rest = [word.format(out=out) for word in command]
    before_off = ["--set", "printer.clear_graphics_before=false"]
    refused = cli_runner.invoke(
        cli_mod.cli, [*before_off, verb, label, "--data", data, "--model", "allegro-2", *rest], obj=build_testing
    )
    assert refused.exit_code == 1, refused.output
    assert not out.exists()
    both_off = [*before_off, "--set", "printer.clear_graphics_after=false"]
    kept = cli_runner.invoke(
        cli_mod.cli, [*both_off, verb, label, "--data", data, "--model", "allegro-2", *rest], obj=build_testing
    )
    assert kept.exit_code == 0, kept.output


@pytest.mark.os_agnostic
def test_the_refusal_names_record_one_and_both_keys(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _STORED_AND_TEXT)
    command = ["validate", label, "--data", data, "--model", "allegro-2"]
    refused = _validate(cli_runner, "--set", "printer.clear_graphics_before=false", *command)
    found = json.loads(refused.stdout)["data"]["findings"]
    [record] = [f["record"] for f in found if f["code"] == "stored_image_cleared"]
    assert record == 1
    [message] = _findings(refused, "stored_image_cleared")
    assert "image LOGO has no bitmap, and the clear after the batch's previous label" in message
    assert "[printer] clear_graphics_before" in message
    assert "[printer] clear_graphics_after" in message


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("model", "module"),
    [
        pytest.param("prodigy", "A", id="prodigy-removes-only-its-own-downloads"),
        pytest.param("allegro-2", "B", id="plug-in-module-never-cleared"),
    ],
)
def test_a_clear_after_that_leaves_stored_images_lets_the_batch_pass(
    model: str, module: str, cli_runner: CliRunner, tmp_path: Path
) -> None:
    label, data = _files(tmp_path, _STORED_AND_TEXT)
    settings = ["--set", "printer.clear_graphics_before=false", "--set", f"printer.image_module={module}"]
    kept = _validate(cli_runner, *settings, "validate", label, "--data", data, "--model", model)
    assert kept.exit_code == 0, kept.output
    assert not _findings(kept, "stored_image_cleared")
