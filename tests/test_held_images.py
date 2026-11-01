"""A label no clear precedes never downloads an image name the printer already holds.

One Allegro 2 printed nothing for a format that downloaded a name it held (0 of 3 rounds,
2026-11-01) and printed a format that only referred to the held name (3 of 3). So where no clear
runs before a label's downloads (the clear key is off, the module is a plug-in one a clear would
wear, or the model has no clear) the job leaves out each held name's download and places the
stored image. ``print`` and ``verify`` read the names (``<STX>W``) before rendering; every offline
render knows none and keeps every download. A clear (``<STX>q``) goes only to a model's built-in
RAM (manual, Appendix L p.189), and a stored-only image the clear before removes is refused.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from printer_server import answering, silent

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
from datamax_dpl.application.use_cases import (
    print_result,
    render_batch,
    render_label,
    stored_image_names,
    with_stored_names,
)
from datamax_dpl.composition import build_production
from datamax_dpl.domain.enums import FallbackPolicy, FindingCode, ImageModule, Severity
from datamax_dpl.domain.errors import LabelRejectedError
from datamax_dpl.domain.held_images import HeldImages, held_images_from
from datamax_dpl.domain.label import Image, Label, Qr
from datamax_dpl.domain.memory import parse_module_directory
from datamax_dpl.domain.profiles import MODEL_PROFILES
from datamax_dpl.domain.render import RenderResult, build_job, render
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.status import MEMORY_DIRECTORY_REQUESTS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner
    from printer_server import Play, PrinterServer

STX = b"\x02"
_BITMAP = ["#.#.#.#.", ".#.#.#.#"]
_LOGO = Image(x=1, y=1, name="LOGO", bitmap=_BITMAP)
_ICON = Image(x=1, y=9, name="ICON", bitmap=_BITMAP)
_STORED = Image(x=1, y=1, name="LOGO")
_WG = MEMORY_DIRECTORY_REQUESTS["graphic"]
_HOLDS_LOGO = b"MODULE: A\rLOGO\rAVAILABLE BYTES IN MODULE : 00049083\r"
_LOGO_HELD = HeldImages(listed=frozenset({"LOGO"}))
_NOTHING_HELD = HeldImages(listed=frozenset())
#: Allegro 2 with no clear at all: no clear before, none after that would empty the module.
_NO_CLEAR: dict[str, Any] = {
    "model": "allegro-2",
    "clear_graphics_before_label": False,
    "clear_graphics_after_label": False,
}


def _options(**fields: Any) -> RenderOptions:
    return RenderOptions.model_validate(fields)


def _job(label: Label, held: HeldImages | None = None, **options: Any) -> RenderResult:
    return build_job(label, _options(**options), held=held)


def _found(result: RenderResult, code: FindingCode) -> list[tuple[Severity, int | None]]:
    return [(finding.severity, finding.element) for finding in result.findings if finding.code is code]


def _format(data: bytes) -> bytes:
    """The format: from ``<STX>L`` to its end, where the placing records stand."""
    return data[data.index(STX + b"L\r") :]


# ======================== which modules a clear goes to ========================


@pytest.mark.os_agnostic
def test_the_built_in_ram_modules_are_the_manual_s_appendix_l() -> None:
    ram = {model: profile.ram_modules for model, profile in MODEL_PROFILES.items()}
    assert ram["allegro-2"] == ram["dmx-430"] == ram["titan-6200"] == ram["prodigy-max-203"] == {"A"}
    assert ram["dmx-600"] == ram["dmx-800"] == {"E"}
    assert ram["prodigy"] == ram["prodigy-plus"] == ram["prodigy-152"] == ram["prodigy-plus-152"] == frozenset()


@pytest.mark.os_agnostic
def test_every_model_s_own_image_module_is_one_of_its_ram_modules_where_it_has_one() -> None:
    for profile in MODEL_PROFILES.values():
        assert profile.image_module is None or profile.image_module in profile.ram_modules, profile.id


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("model", "module"), [("allegro-2", ImageModule.B), ("dmx-600", ImageModule.A)])
def test_a_plug_in_module_gets_no_clear_whatever_the_keys_say(model: str, module: ImageModule) -> None:
    result = _job(Label(elements=[_LOGO]), model=model, image_module=module)
    assert STX + b"q" not in result.data
    assert _found(result, FindingCode.GRAPHICS_CLEAR_SKIPPED) == [(Severity.INFO, None)]


@pytest.mark.os_agnostic
def test_the_plain_prodigy_keeps_its_per_name_removes_on_a_plug_in_module() -> None:
    result = _job(Label(elements=[_LOGO]), model="prodigy", image_module=ImageModule.B)
    assert result.data.count(STX + b"RLOGO\r") == 2
    assert STX + b"q" not in result.data
    assert FindingCode.GRAPHICS_CLEAR_SKIPPED not in [f.code for f in result.findings]
    assert FindingCode.IMAGE_NAMES_UNREAD not in [f.code for f in result.findings]


# ======================== a held name's download is left out ========================


@pytest.mark.os_agnostic
def test_a_held_name_is_not_downloaded_and_the_format_still_places_it() -> None:
    held = _job(Label(elements=[_LOGO]), _LOGO_HELD, **_NO_CLEAR)
    unread = _job(Label(elements=[_LOGO]), **_NO_CLEAR)
    assert STX + b"I" not in held.data
    assert STX + b"IAAFLOGO\r" in unread.data
    assert _format(held.data) == _format(unread.data)
    assert held.downloads == ()
    assert _found(held, FindingCode.IMAGE_NAME_HELD) == [(Severity.WARNING, 0)]
    assert FindingCode.IMAGE_NAMES_UNREAD not in [f.code for f in held.findings]


@pytest.mark.os_agnostic
def test_only_the_held_name_is_left_out_and_the_finding_names_its_element() -> None:
    result = _job(Label(elements=[_ICON, _LOGO]), _LOGO_HELD, **_NO_CLEAR)
    assert STX + b"IAAFICON\r" in result.data
    assert STX + b"IAAFLOGO\r" not in result.data
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_NAME_HELD]
    assert (finding.element, "image LOGO" in finding.message) == (1, True)


@pytest.mark.os_agnostic
def test_a_held_drawn_image_is_not_downloaded_either() -> None:
    first = _job(Label(elements=[Qr(x=5, y=5, data="SAME")]), **_NO_CLEAR)
    [name] = first.downloads
    again = _job(Label(elements=[Qr(x=5, y=5, data="SAME")]), HeldImages(listed=frozenset({name})), **_NO_CLEAR)
    assert again.downloads == ()
    assert STX + b"I" not in again.data


@pytest.mark.os_agnostic
def test_a_name_the_printer_does_not_list_is_downloaded_with_no_finding() -> None:
    result = _job(Label(elements=[_LOGO]), _NOTHING_HELD, **_NO_CLEAR)
    assert STX + b"IAAFLOGO\r" in result.data
    held_codes = {FindingCode.IMAGE_NAME_HELD, FindingCode.IMAGE_NAMES_UNREAD}
    assert not held_codes & {f.code for f in result.findings}


@pytest.mark.os_agnostic
def test_a_left_out_download_costs_nothing_against_the_image_budget() -> None:
    result = _job(Label(elements=[_LOGO]), _LOGO_HELD, image_memory_bytes=1, **_NO_CLEAR)
    assert result.image_bytes == 0
    assert FindingCode.IMAGE_MEMORY_EXCEEDED not in [f.code for f in result.findings]


@pytest.mark.os_agnostic
def test_with_a_clear_before_a_held_name_is_downloaded_after_the_clear() -> None:
    result = _job(Label(elements=[_LOGO]), _LOGO_HELD, model="allegro-2")
    assert result.data.index(STX + b"qA") < result.data.index(STX + b"IAAFLOGO\r")
    assert not {FindingCode.IMAGE_NAME_HELD, FindingCode.IMAGE_NAMES_UNREAD} & {f.code for f in result.findings}
    assert not result.names_unread


@pytest.mark.os_agnostic
def test_a_plug_in_module_s_held_name_is_left_out_although_the_clear_keys_are_on() -> None:
    result = _job(Label(elements=[_LOGO]), _LOGO_HELD, model="dmx-600", image_module=ImageModule.A)
    assert STX + b"I" not in result.data
    assert _found(result, FindingCode.IMAGE_NAME_HELD) == [(Severity.WARNING, 0)]


# ======================== names that were not read ========================


@pytest.mark.os_agnostic
def test_unknown_names_keep_every_download_and_say_so_once() -> None:
    result = _job(Label(elements=[_LOGO, _ICON]), **_NO_CLEAR)
    assert result.downloads == ("LOGO", "ICON")
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_NAMES_UNREAD]
    assert (finding.severity, finding.element) == (Severity.WARNING, None)
    assert "prints nothing" in finding.message
    assert "the printer was not asked" in finding.message
    assert result.names_unread
    assert result.image_module == "A"


@pytest.mark.os_agnostic
def test_the_reason_the_names_are_unknown_is_quoted() -> None:
    result = _job(Label(elements=[_LOGO]), HeldImages(unread_because="the link to the printer is one-way"), **_NO_CLEAR)
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_NAMES_UNREAD]
    assert "(the link to the printer is one-way)" in finding.message


@pytest.mark.os_agnostic
def test_a_label_without_downloads_needs_no_names() -> None:
    result = _job(Label(elements=[_STORED]), **_NO_CLEAR)
    assert not result.names_unread
    assert FindingCode.IMAGE_NAMES_UNREAD not in [f.code for f in result.findings]


@pytest.mark.os_agnostic
def test_a_model_that_can_clear_nothing_weighs_held_names_too() -> None:
    result = _job(Label(elements=[_LOGO]), _LOGO_HELD, model="generic")
    assert STX + b"I" not in result.data
    assert _found(result, FindingCode.IMAGE_NAME_HELD) == [(Severity.WARNING, 0)]


# ======================== the severity key ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize("severity", list(Severity))
@pytest.mark.parametrize("held", [_LOGO_HELD, HeldImages()], ids=["held", "unread"])
def test_both_findings_take_the_configured_severity_under_either_policy(severity: Severity, held: HeldImages) -> None:
    for policy in FallbackPolicy:
        result = _job(Label(elements=[_LOGO]), held, held_image_severity=severity, policy=policy, **_NO_CLEAR)
        codes = {FindingCode.IMAGE_NAME_HELD, FindingCode.IMAGE_NAMES_UNREAD}
        assert [f.severity for f in result.findings if f.code in codes] == [severity]
        assert result.ok is (severity is not Severity.ERROR)


@pytest.mark.os_agnostic
def test_an_error_severity_refuses_the_label() -> None:
    with pytest.raises(LabelRejectedError, match="were not read"):
        render(Label(elements=[_LOGO]), _options(held_image_severity=Severity.ERROR, **_NO_CLEAR))


# ======================== a batch with no clear ========================


@pytest.mark.os_agnostic
def test_a_batch_with_no_clear_downloads_a_name_once() -> None:
    label = Label.model_validate({"elements": [{"type": "qr", "x": 5, "y": 5, "data": "{code}"}]})
    batch = render_batch(label, [{"code": "SAME"}, {"code": "SAME"}], _options(**_NO_CLEAR))
    assert batch.data.count(STX + b"IA") == 1
    assert [len(result.downloads) for result in batch.results] == [1, 0]
    # The second record's name is the batch's own: neither held nor unread.
    assert [f.code for f in batch.results[1].findings if f.code is FindingCode.IMAGE_NAMES_UNREAD] == []


@pytest.mark.os_agnostic
def test_a_batch_whose_clear_after_empties_the_module_downloads_again() -> None:
    label = Label.model_validate({"elements": [{"type": "image", "x": 1, "y": 1, "name": "LOGO", "bitmap": _BITMAP}]})
    options = _options(model="allegro-2", clear_graphics_before_label=False)
    batch = render_batch(label, [{}, {}], options, held=_LOGO_HELD)
    assert [result.downloads for result in batch.results] == [(), ("LOGO",)]
    # After the first record's qA the module is known empty: the second record needs no names.
    assert not batch.results[1].names_unread


# ======================== a stored-only image ========================


@pytest.mark.os_agnostic
def test_a_stored_only_image_the_clear_before_removes_is_refused_naming_the_key() -> None:
    result = _job(Label(elements=[_STORED]), model="allegro-2")
    [finding] = [f for f in result.findings if f.code is FindingCode.STORED_IMAGE_CLEARED]
    assert (finding.severity, finding.element) == (Severity.ERROR, 0)
    assert "clear_graphics_before_label" in finding.message
    assert not result.ok


@pytest.mark.os_agnostic
def test_a_stored_only_image_prints_as_before_with_the_clear_before_off() -> None:
    result = _job(Label(elements=[_STORED]), model="allegro-2", clear_graphics_before_label=False)
    assert result.ok, result.findings
    assert _found(result, FindingCode.IMAGE_MUST_BE_STORED) == [(Severity.INFO, 0)]


@pytest.mark.os_agnostic
def test_a_stored_only_image_on_a_module_the_clear_skips_prints_as_before() -> None:
    result = _job(Label(elements=[_STORED]), model="allegro-2", image_module=ImageModule.B)
    assert result.ok, result.findings
    assert FindingCode.STORED_IMAGE_CLEARED not in [f.code for f in result.findings]


@pytest.mark.os_agnostic
def test_a_stored_only_reference_to_a_name_the_label_downloads_is_not_refused() -> None:
    result = _job(Label(elements=[_STORED, _LOGO]), model="allegro-2")
    assert result.ok, result.findings


@pytest.mark.os_agnostic
def test_the_prodigy_s_remove_takes_only_the_label_s_names_so_a_stored_image_prints() -> None:
    result = _job(Label(elements=[_STORED]), model="prodigy", image_module=ImageModule.B)
    assert result.ok, result.findings


# ======================== reading the names ========================


@pytest.mark.os_agnostic
def test_the_directory_s_names_of_the_job_s_module_are_the_held_ones() -> None:
    directory = parse_module_directory(
        _HOLDS_LOGO + b"MODULE: B\rICON\rAVAILABLE BYTES IN MODULE:00001000\r", kind="graphic"
    )
    assert held_images_from(directory, module="A").listed == {"LOGO"}
    assert held_images_from(directory, module="B").listed == {"ICON"}


@pytest.mark.os_agnostic
def test_an_unreadable_directory_gives_no_names() -> None:
    directory = parse_module_directory(b"MODULE: A\rLOGO\r", kind="graphic")
    held = held_images_from(directory, module="A")
    assert held.listed is None
    assert "could not be read" in held.unread_because


def _print(printer: RecordingPrinter, label: Label, options: RenderOptions) -> RenderResult:
    """Render and print as ``print`` does, reading the names through ``printer`` where needed."""
    opener = open_recording_printer(printer)

    def read(module: str) -> HeldImages:
        return stored_image_names(module=module, uri="tcp://192.0.2.60:9100", timeout=1.0, open_printer=opener)

    result = with_stored_names(lambda held: render_label(label, options, held=held), read=read)
    print_result(result, uri="tcp://192.0.2.60:9100", timeout=1.0, open_printer=opener)
    return result


@pytest.mark.os_agnostic
def test_a_job_with_a_clear_before_reads_no_names() -> None:
    printer = RecordingPrinter(answers={_WG: _HOLDS_LOGO})
    _print(printer, Label(elements=[_LOGO]), _options(model="allegro-2"))
    assert printer.queries == []
    assert STX + b"W" not in printer.data
    assert STX + b"IAAFLOGO\r" in printer.data


@pytest.mark.os_agnostic
def test_a_job_with_no_clear_reads_the_names_and_leaves_out_the_held_download() -> None:
    printer = RecordingPrinter(answers={_WG: _HOLDS_LOGO})
    result = _print(printer, Label(elements=[_ICON, _LOGO]), _options(**_NO_CLEAR))
    assert printer.queries == [_WG]
    assert STX + b"IAAFLOGO\r" not in printer.data
    assert STX + b"IAAFICON\r" in printer.data
    assert b"LOGO" in _format(printer.data)
    assert _found(result, FindingCode.IMAGE_NAME_HELD) == [(Severity.WARNING, 1)]


@pytest.mark.os_agnostic
def test_a_job_without_downloads_reads_no_names_even_with_no_clear() -> None:
    printer = RecordingPrinter(answers={_WG: _HOLDS_LOGO})
    _print(printer, Label(elements=[_STORED]), _options(**_NO_CLEAR))
    assert printer.queries == []


@pytest.mark.os_agnostic
def test_a_one_way_link_reads_nothing_and_keeps_every_download() -> None:
    printer = RecordingPrinter(bidirectional=False)
    result = _print(printer, Label(elements=[_LOGO]), _options(**_NO_CLEAR))
    assert STX + b"W" not in printer.data
    assert STX + b"IAAFLOGO\r" in printer.data
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_NAMES_UNREAD]
    assert "one-way" in finding.message


@pytest.mark.os_agnostic
def test_a_read_that_gets_no_answer_keeps_every_download_and_the_job_is_still_sent() -> None:
    printer = RecordingPrinter()
    result = _print(printer, Label(elements=[_LOGO]), _options(**_NO_CLEAR))
    assert printer.queries == [_WG]
    assert STX + b"IAAFLOGO\r" in printer.data
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_NAMES_UNREAD]
    assert "reading them failed" in finding.message
    assert "no answer" in finding.message


# ======================== the CLI over a real channel ========================


def _run(runner: CliRunner, args: list[str], **env: str) -> Any:
    return runner.invoke(cli_mod.cli, args, obj=build_production, env=env)


def _label_file(tmp_path: Path, *elements: Image) -> str:
    path = tmp_path / "label.json"
    path.write_text(
        json.dumps({"elements": [element.model_dump(mode="json") for element in elements]}), encoding="utf-8"
    )
    return str(path)


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_with_the_clear_on_sends_no_directory_request(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[[Play], PrinterServer]
) -> None:
    server = serve(silent)

    result = _run(
        cli_runner, ["print", _label_file(tmp_path, _LOGO), "--model", "allegro-2", "--printer", server.address]
    )
    server.join()

    assert result.exit_code == 0, result.output
    assert STX + b"W" not in bytes(server.received)
    assert STX + b"IAAFLOGO\r" in bytes(server.received)


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_to_a_plug_in_module_reads_the_names_and_leaves_out_the_held_download(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[..., PrinterServer]
) -> None:
    holds = b"MODULE: B\rLOGO\rAVAILABLE BYTES IN MODULE : 00049083\r"
    server = serve(answering(_WG, holds), silent)
    args = ["print", _label_file(tmp_path, _ICON, _LOGO), "--model", "allegro-2", "--printer", server.address]

    result = _run(cli_runner, ["--json", *args], DATAMAX_DPL___PRINTER__IMAGE_MODULE="B")
    server.join()

    assert result.exit_code == 0, result.output
    directory, job = (bytes(connection) for connection in server.connections)
    assert directory == _WG
    assert STX + b"IBAFICON\r" in job
    assert STX + b"IBAFLOGO\r" not in job
    assert STX + b"q" not in job
    codes = [finding["code"] for finding in json.loads(result.stdout)["data"]["findings"]]
    assert "image_name_held" in codes
