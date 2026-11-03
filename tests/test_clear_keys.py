"""The clears around each label's image downloads are settings keys, and the messages name them.

``[printer] clear_graphics_before`` and ``clear_graphics_after`` map onto the render options of the
same meaning; a finding that tells a reader to turn the clear before on or off names the key, and
where no clear may go at all, the ways that are left.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.settings import PrinterSettings, render_options
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.label import Image, Label, Qr

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
    assert "turn on the clear before each label ([printer] clear_graphics_before" in message
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
    assert "leave image_module unset, so the images go to module A" in message
    assert "module B takes no clear" in message
    following = _validate(cli_runner, *common)
    assert following.exit_code == 0, following.output
