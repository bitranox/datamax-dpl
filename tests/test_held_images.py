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
import re
import time
from typing import TYPE_CHECKING, Any

import pytest
from printer_server import answering, silent, trickling

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
from datamax_dpl.application.use_cases import (
    AnswerWait,
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
from datamax_dpl.domain.raster import Bitmap
from datamax_dpl.domain.render import RenderResult, build_job, build_jobs, render
from datamax_dpl.domain.render_context import RenderOptions, drawn_image_name
from datamax_dpl.domain.status import MEMORY_DIRECTORY_REQUESTS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner
    from printer_server import Play, PrinterServer

STX = b"\x02"
_BITMAP = ["#.#.#.#.", ".#.#.#.#"]
_ICON_BITMAP = ["##..##..", "..##..##"]
_LOGO = Image(x=1, y=1, name="LOGO", bitmap=_BITMAP)
_ICON = Image(x=1, y=9, name="ICON", bitmap=_ICON_BITMAP)
_STORED = Image(x=1, y=1, name="LOGO")
#: The printer names the two images go under: their content names, never LOGO or ICON.
LOGO_SENT = drawn_image_name(Bitmap.from_rows(_BITMAP))
ICON_SENT = drawn_image_name(Bitmap.from_rows(_ICON_BITMAP))
_L = LOGO_SENT.encode("ascii")
_I = ICON_SENT.encode("ascii")
_WG = MEMORY_DIRECTORY_REQUESTS["graphic"]
_HOLDS_LOGO = b"MODULE: A\r" + _L + b"\rAVAILABLE BYTES IN MODULE : 00049083\r"
_LOGO_HELD = HeldImages(listed=frozenset({LOGO_SENT}))
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


#: The name of every image download in a job: ``<STX>I``, module, ``A``, ``F``, the name, ``<CR>``.
_DOWNLOAD_NAME = re.compile(rb"\x02I[A-E]AF([^\r]*)\r")
#: A content name: ``D`` and seven hex digits of the image's hash.
_CONTENT_NAME = re.compile(rb"D[0-9A-F]{7}")


# ======================== every bitmap goes under its content name ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "options",
    [
        {"model": "allegro-2"},
        {"model": "allegro-2", "image_memory_bytes": 1},
        {"model": "dmx-600"},
        {"model": "prodigy", "image_module": ImageModule.B},
        _NO_CLEAR,
    ],
    ids=["clear", "halved", "dmx-600", "prodigy", "no-clear"],
)
def test_no_image_is_downloaded_under_the_element_s_own_name(options: dict[str, Any]) -> None:
    result = _job(Label(elements=[_LOGO, _ICON, _STORED]), **options)
    names = _DOWNLOAD_NAME.findall(result.data)
    assert names
    assert all(_CONTENT_NAME.fullmatch(name) for name in names), names
    assert not {b"LOGO", b"ICON"} & set(names)
    assert STX + b"RLOGO" not in result.data


@pytest.mark.os_agnostic
def test_two_images_of_one_name_and_different_bitmaps_download_under_two_content_names() -> None:
    other = Image(x=1, y=9, name="LOGO", bitmap=_ICON_BITMAP)
    result = _job(Label(elements=[_LOGO, other]), model="allegro-2")
    assert result.downloads == (LOGO_SENT, ICON_SENT)
    assert _DOWNLOAD_NAME.findall(result.data) == [_L, _I]
    assert _L in _format(result.data)
    assert _I in _format(result.data)
    assert result.ok, result.findings


@pytest.mark.os_agnostic
def test_a_stored_only_reference_to_a_name_the_label_downloads_places_that_download() -> None:
    result = _job(Label(elements=[_STORED, _LOGO]), model="allegro-2")
    assert result.downloads == (LOGO_SENT,)
    assert _format(result.data).count(_L) == 2
    assert b"LOGO" not in _format(result.data)


@pytest.mark.os_agnostic
def test_a_stored_only_reference_no_element_downloads_places_its_own_name() -> None:
    result = _job(Label(elements=[_STORED, _ICON]), model="allegro-2", clear_graphics_before_label=False)
    assert result.downloads == (ICON_SENT,)
    assert re.search(rb"\r1Y11\d{11}LOGO\r", _format(result.data))


@pytest.mark.os_agnostic
def test_a_printer_holding_the_element_s_own_name_does_not_stop_its_download() -> None:
    result = _job(Label(elements=[_LOGO]), HeldImages(listed=frozenset({"LOGO"})), **_NO_CLEAR)
    assert result.downloads == (LOGO_SENT,)
    assert FindingCode.IMAGE_NAME_HELD not in [f.code for f in result.findings]


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
    assert result.data.count(STX + b"R" + _L + b"\r") == 2
    assert STX + b"q" not in result.data
    assert FindingCode.GRAPHICS_CLEAR_SKIPPED not in [f.code for f in result.findings]
    assert FindingCode.IMAGE_NAMES_UNREAD not in [f.code for f in result.findings]


# ======================== a held name's download is left out ========================


@pytest.mark.os_agnostic
def test_a_held_name_is_not_downloaded_and_the_format_still_places_it() -> None:
    held = _job(Label(elements=[_LOGO]), _LOGO_HELD, **_NO_CLEAR)
    unread = _job(Label(elements=[_LOGO]), **_NO_CLEAR)
    assert STX + b"I" not in held.data
    assert STX + b"IAAF" + _L + b"\r" in unread.data
    assert _format(held.data) == _format(unread.data)
    assert held.downloads == ()
    assert _found(held, FindingCode.IMAGE_NAME_HELD) == [(Severity.WARNING, 0)]
    assert FindingCode.IMAGE_NAMES_UNREAD not in [f.code for f in held.findings]
    assert not held.names_unread


@pytest.mark.os_agnostic
def test_only_the_held_name_is_left_out_and_the_finding_names_its_element() -> None:
    result = _job(Label(elements=[_ICON, _LOGO]), _LOGO_HELD, **_NO_CLEAR)
    assert STX + b"IAAF" + _I + b"\r" in result.data
    assert STX + b"IAAF" + _L not in result.data
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_NAME_HELD]
    assert (finding.element, f"image LOGO (sent as {LOGO_SENT})" in finding.message) == (1, True)


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
    assert STX + b"IAAF" + _L + b"\r" in result.data
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
    assert result.data.index(STX + b"qA") < result.data.index(STX + b"IAAF" + _L + b"\r")
    assert not {FindingCode.IMAGE_NAME_HELD, FindingCode.IMAGE_NAMES_UNREAD} & {f.code for f in result.findings}
    assert not result.names_unread


@pytest.mark.os_agnostic
def test_a_plug_in_module_s_held_name_is_left_out_although_the_clear_keys_are_on() -> None:
    result = _job(Label(elements=[_LOGO]), _LOGO_HELD, model="dmx-600", image_module=ImageModule.A)
    assert STX + b"I" not in result.data
    assert _found(result, FindingCode.IMAGE_NAME_HELD) == [(Severity.WARNING, 0)]


@pytest.mark.os_agnostic
def test_two_images_of_one_name_and_one_bitmap_download_once_and_both_are_placed() -> None:
    result = _job(Label(elements=[_LOGO, _LOGO.model_copy(update={"y": 9})]), model="allegro-2")
    [name] = result.downloads
    assert result.data.count(STX + b"IA") == 1
    assert _format(result.data).count(name.encode("ascii")) == 2


@pytest.mark.os_agnostic
def test_a_name_listed_only_in_another_letter_case_is_another_name() -> None:
    """One Allegro 2 kept a name as sent and printed no image for it in another case (2026-11-01)."""
    result = _job(Label(elements=[_LOGO]), HeldImages(listed=frozenset({LOGO_SENT.lower()})), **_NO_CLEAR)
    assert STX + b"IAAF" + _L + b"\r" in result.data
    assert not {FindingCode.IMAGE_NAME_HELD, FindingCode.IMAGE_NAMES_UNREAD} & {f.code for f in result.findings}
    assert not result.names_unread


# ======================== names that were not read ========================


@pytest.mark.os_agnostic
def test_unknown_names_keep_every_download_and_say_so_once() -> None:
    result = _job(Label(elements=[_LOGO, _ICON]), **_NO_CLEAR)
    assert result.downloads == (LOGO_SENT, ICON_SENT)
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


#: Two bitmaps whose content names collide, found by brute force over 2**20 bitmaps of 16x2 dots.
_COLLIDING = (["................", ".##.#.#.#......#"], ["................", "#.#..#.##...#.#."])
_COLLIDED = drawn_image_name(Bitmap.from_rows(_COLLIDING[0]))


def _colliding_labels() -> list[Label]:
    first, second = _COLLIDING
    return [
        Label(elements=[Image(x=1, y=1, name="A", bitmap=first)]),
        Label(elements=[Image(x=1, y=1, name="B", bitmap=second)]),
    ]


@pytest.mark.os_agnostic
def test_a_later_record_s_other_bitmap_under_a_name_the_batch_sent_is_salted_and_downloaded() -> None:
    assert drawn_image_name(Bitmap.from_rows(_COLLIDING[1])) == _COLLIDED
    salted = drawn_image_name(Bitmap.from_rows(_COLLIDING[1]), salt=1)
    first, second = build_jobs(_colliding_labels(), _options(**_NO_CLEAR))
    assert first.downloads == (_COLLIDED,)
    assert second.downloads == (salted,)
    assert _DOWNLOAD_NAME.findall(second.data) == [salted.encode("ascii")]
    assert salted.encode("ascii") in _format(second.data)
    assert _COLLIDED.encode("ascii") not in _format(second.data)
    assert FindingCode.IMAGE_NAME_HELD not in [f.code for f in second.findings]


@pytest.mark.os_agnostic
def test_a_later_record_of_the_same_bitmap_still_places_the_name_the_batch_sent() -> None:
    first, again = build_jobs([_colliding_labels()[0]] * 2, _options(**_NO_CLEAR))
    assert (first.downloads, again.downloads) == ((_COLLIDED,), ())
    assert _COLLIDED.encode("ascii") in _format(again.data)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "options",
    [
        {"model": "allegro-2", "clear_graphics_after_label": False},
        {"model": "prodigy", "image_module": ImageModule.B, "clear_graphics_after_label": False},
    ],
    ids=["clear-module", "prodigy-remove"],
)
def test_a_clear_before_each_record_frees_the_name_the_batch_sent(options: dict[str, Any]) -> None:
    _, second = build_jobs(_colliding_labels(), _options(**options))
    assert second.downloads == (_COLLIDED,)


@pytest.mark.os_agnostic
def test_a_batch_whose_clear_after_empties_the_module_downloads_again() -> None:
    label = Label.model_validate({"elements": [{"type": "image", "x": 1, "y": 1, "name": "LOGO", "bitmap": _BITMAP}]})
    options = _options(model="allegro-2", clear_graphics_before_label=False)
    batch = render_batch(label, [{}, {}], options, held=_LOGO_HELD)
    assert [result.downloads for result in batch.results] == [(), (LOGO_SENT,)]
    # After the first record's qA the module is known empty: the second record needs no names.
    assert not batch.results[1].names_unread


@pytest.mark.os_agnostic
def test_a_prodigy_batch_downloads_again_after_the_remove_after_the_label() -> None:
    label = Label.model_validate({"elements": [{"type": "image", "x": 1, "y": 1, "name": "LOGO", "bitmap": _BITMAP}]})
    options = _options(model="prodigy", image_module=ImageModule.B, clear_graphics_before_label=False)
    batch = render_batch(label, [{}, {}], options, held=_NOTHING_HELD)
    assert [result.downloads for result in batch.results] == [(LOGO_SENT,), (LOGO_SENT,)]
    assert [result.data.endswith(STX + b"R" + _L + b"\r") for result in batch.results] == [True, True]


@pytest.mark.os_agnostic
def test_the_prodigy_s_remove_after_takes_only_the_names_the_label_downloaded() -> None:
    options = {"model": "prodigy", "image_module": ImageModule.B, "clear_graphics_before_label": False}
    result = _job(Label(elements=[_ICON, _LOGO]), HeldImages(listed=frozenset({ICON_SENT})), **options)
    assert result.downloads == (LOGO_SENT,)
    assert STX + b"R" + _I not in result.data
    assert result.closing == STX + b"R" + _L + b"\r"


@pytest.mark.os_agnostic
def test_a_prodigy_batch_keeps_a_held_name_it_never_removed() -> None:
    label = Label.model_validate({"elements": [{"type": "image", "x": 1, "y": 1, "name": "LOGO", "bitmap": _BITMAP}]})
    options = _options(model="prodigy", image_module=ImageModule.B, clear_graphics_before_label=False)
    batch = render_batch(label, [{}, {}], options, held=_LOGO_HELD)
    assert [result.downloads for result in batch.results] == [(), ()]
    assert STX + b"R" not in batch.data


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
    assert held_images_from(directory, module="A").listed == {LOGO_SENT}
    assert held_images_from(directory, module="B").listed == {"ICON"}


@pytest.mark.os_agnostic
def test_an_unreadable_directory_gives_no_names() -> None:
    directory = parse_module_directory(b"MODULE: A\rLOGO\r", kind="graphic")
    held = held_images_from(directory, module="A")
    assert held.listed is None
    assert "could not be read" in held.unread_because


@pytest.mark.os_agnostic
def test_the_default_module_gives_no_names_whatever_the_directory_lists() -> None:
    directory = parse_module_directory(_HOLDS_LOGO, kind="graphic")
    held = held_images_from(directory, module="C")
    assert held.listed is None
    assert held.unread_because == "C is the printer's default module; which letter it is was not read"


@pytest.mark.os_agnostic
def test_a_names_answer_that_never_ends_is_cut_at_the_wait(serve: Callable[..., PrinterServer]) -> None:
    """An answer that starts in time but never falls silent ends at the wait, not at 120 s."""
    server = serve(trickling(_WG, _HOLDS_LOGO, every=0.2, for_seconds=8.0))
    started = time.monotonic()
    held = stored_image_names(
        module="A",
        uri=server.address,
        timeout=1.0,
        open_printer=build_production().open_printer,
        wait=AnswerWait(seconds=1.5),
    )
    waited = time.monotonic() - started
    server.join()

    assert held.listed is None
    assert held.unread_because.startswith("reading them failed"), held.unread_because
    assert waited < 4.0, waited


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
    assert STX + b"IAAF" + _L + b"\r" in printer.data


@pytest.mark.os_agnostic
def test_a_job_with_no_clear_reads_the_names_and_leaves_out_the_held_download() -> None:
    printer = RecordingPrinter(answers={_WG: _HOLDS_LOGO})
    result = _print(printer, Label(elements=[_ICON, _LOGO]), _options(**_NO_CLEAR))
    assert printer.queries == [_WG]
    assert STX + b"IAAF" + _L not in printer.data
    assert STX + b"IAAF" + _I + b"\r" in printer.data
    assert _L in _format(printer.data)
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
    assert STX + b"IAAF" + _L + b"\r" in printer.data
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_NAMES_UNREAD]
    assert "one-way" in finding.message


@pytest.mark.os_agnostic
def test_a_read_that_gets_no_answer_keeps_every_download_and_the_job_is_still_sent() -> None:
    printer = RecordingPrinter()
    result = _print(printer, Label(elements=[_LOGO]), _options(**_NO_CLEAR))
    assert printer.queries == [_WG]
    assert STX + b"IAAF" + _L + b"\r" in printer.data
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_NAMES_UNREAD]
    assert "reading them failed" in finding.message
    assert "no answer" in finding.message


@pytest.mark.os_agnostic
def test_a_label_for_the_default_module_keeps_its_download_and_says_its_names_are_unread() -> None:
    printer = RecordingPrinter(answers={_WG: b"MODULE: A\rLOGO\rAVAILABLE BYTES IN MODULE : 00048631\r"})
    result = _print(printer, Label(elements=[_LOGO]), _options(model="allegro-2", image_module=ImageModule.C))
    assert STX + b"IC" in printer.data
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_NAMES_UNREAD]
    assert "C is the printer's default module" in finding.message
    # Nothing the directory could say would name the module, so it is not asked.
    assert printer.queries == []


# ======================== the CLI over a real channel ========================


def _run(runner: CliRunner, args: list[str], *, stdin: str | None = None, **env: str) -> Any:
    return runner.invoke(cli_mod.cli, args, obj=build_production, env=env, input=stdin)


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
    assert STX + b"IAAF" + _L + b"\r" in bytes(server.received)


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_to_a_plug_in_module_reads_the_names_and_leaves_out_the_held_download(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[..., PrinterServer]
) -> None:
    holds = b"MODULE: B\r" + _L + b"\rAVAILABLE BYTES IN MODULE : 00049083\r"
    server = serve(answering(_WG, holds), silent)
    args = ["print", _label_file(tmp_path, _ICON, _LOGO), "--model", "allegro-2", "--printer", server.address]

    result = _run(cli_runner, ["--json", *args], DATAMAX_DPL___PRINTER__IMAGE_MODULE="B")
    server.join()

    assert result.exit_code == 0, result.output
    directory, job = (bytes(connection) for connection in server.connections)
    assert directory == _WG
    assert STX + b"IBAF" + _I + b"\r" in job
    assert STX + b"IBAF" + _L not in job
    assert STX + b"q" not in job
    codes = [finding["code"] for finding in json.loads(result.stdout)["data"]["findings"]]
    assert "image_name_held" in codes


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_with_data_on_stdin_and_no_clear_reads_the_records_once_and_sends_the_job(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    target = tmp_path / "job.dpl"
    label = tmp_path / "label.json"
    label.write_text(json.dumps({"elements": [{"type": "qr", "x": 5, "y": 5, "data": "{code}"}]}), encoding="utf-8")
    args = ["print", str(label), "--data", "-", "--model", "allegro-2", "--printer", f"file:{target}"]

    result = _run(cli_runner, args, stdin='[{"code": "A1"}, {"code": "B2"}]', DATAMAX_DPL___PRINTER__IMAGE_MODULE="B")

    assert result.exit_code == 0, result.output
    assert target.read_bytes().count(STX + b"IB") == 2


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_a_refused_print_to_a_file_creates_no_file(cli_runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "job.dpl"
    args = ["print", _label_file(tmp_path, _LOGO), "--model", "allegro-2", "--printer", f"file:{target}"]

    result = _run(
        cli_runner,
        args,
        DATAMAX_DPL___PRINTER__IMAGE_MODULE="B",
        DATAMAX_DPL___PRINTER__HELD_IMAGE_SEVERITY="error",
    )

    assert result.exit_code != 0, result.output
    assert not target.exists()


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_reads_the_stored_names_for_stored_names_wait_and_then_sends_the_job(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[..., PrinterServer]
) -> None:
    """A printer silent to the read is waited for stored_names_wait (1 s here), not status_wait (20 s)."""
    server = serve(silent, silent)
    args = ["--json", "print", _label_file(tmp_path, _LOGO), "--model", "allegro-2", "--printer", server.address]

    started = time.monotonic()
    result = _run(
        cli_runner,
        args,
        DATAMAX_DPL___PRINTER__IMAGE_MODULE="B",
        DATAMAX_DPL___PRINTER__STORED_NAMES_WAIT="1",
        DATAMAX_DPL___PRINTER__STATUS_WAIT="20",
    )
    waited = time.monotonic() - started
    server.join()

    assert result.exit_code == 0, result.output
    assert 0.9 <= waited < 10, waited
    directory, job = (bytes(connection) for connection in server.connections)
    assert directory == _WG
    assert STX + b"IBAF" + _L + b"\r" in job
    [finding] = [f for f in json.loads(result.stdout)["data"]["findings"] if f["code"] == "image_names_unread"]
    assert "reading them failed" in finding["message"]
    assert "stored_names_wait" in finding["message"]
