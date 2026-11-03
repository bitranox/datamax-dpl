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
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest
from printer_server import ImageModules, ModulePrinter, answering, answering_over, module_printer, silent, trickling

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory import open_recording_printer
from datamax_dpl.application.ports import PrinterEndpoint
from datamax_dpl.application.upload_check import CheckedJob, UploadCheck, UploadRefusedError, print_checked
from datamax_dpl.application.use_cases import (
    AnswerWait,
    BatchReport,
    check_batch,
    read_stored_names,
    record_renders,
    render_batch,
    render_label,
)
from datamax_dpl.composition import build_production
from datamax_dpl.domain.enums import FallbackPolicy, FindingCode, ImageModule, MemoryKind, Severity
from datamax_dpl.domain.errors import LabelRejectedError
from datamax_dpl.domain.held_images import READ_AT_SEND, HeldImages, held_images_from
from datamax_dpl.domain.label import Image, Label, Qr
from datamax_dpl.domain.memory import parse_module_directory
from datamax_dpl.domain.profiles import MODEL_PROFILES
from datamax_dpl.domain.raster import Bitmap
from datamax_dpl.domain.render import RenderResult, build_job, build_jobs, render
from datamax_dpl.domain.render_context import RenderOptions, drawn_image_name
from datamax_dpl.domain.status import MEMORY_DIRECTORY_REQUESTS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from click.testing import CliRunner
    from printer_server import Play, PrinterServer

    from datamax_dpl.application.use_cases import RecordRender

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
_WG = MEMORY_DIRECTORY_REQUESTS[MemoryKind.GRAPHIC]
_HOLDS_LOGO = b"MODULE: A\r" + _L + b"\rAVAILABLE BYTES IN MODULE : 00049083\r"
#: How ``print`` and ``verify`` say to lengthen the names read, as their wait carries it.
_WAIT_LONGER = "wait longer with [printer] stored_names_wait"
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
#: A content name: ``D`` and seven base-62 digits of the image's hash.
_CONTENT_NAME = re.compile(rb"D[0-9A-Za-z]{7}")


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
    result = _job(Label(elements=[_LOGO]), HeldImages(listed=frozenset({LOGO_SENT.swapcase()})), **_NO_CLEAR)
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
    assert result.image_module is ImageModule.A
    assert '"image_module":"A"' in result.model_dump_json()


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


# ======================== a batch with no clear: nothing carries ========================


@pytest.mark.os_agnostic
def test_a_batch_with_no_clear_downloads_a_name_again_as_nothing_is_kept_between_labels() -> None:
    """Whether the printer still holds it is for the read before the second label to say."""
    label = Label.model_validate({"elements": [{"type": "qr", "x": 5, "y": 5, "data": "{code}"}]})
    batch = render_batch(label, [{"code": "SAME"}, {"code": "SAME"}], _options(**_NO_CLEAR), read_names_at_send=True)
    assert batch.data.count(STX + b"IA") == 2
    assert [len(result.downloads) for result in batch.results] == [1, 1]
    assert [result.names_unread for result in batch.results] == [True, True]


#: Two bitmaps whose content names collide, found by brute force over 2**24 bitmaps of 24x1 dots
#: (rows 979256 and 3075105): a birthday search over the 62**7 names needs about 2**21 of them.
_COLLIDING = (["....###.####...#..###..."], ["..#.###.###.##....#....#"])
_COLLIDED = drawn_image_name(Bitmap.from_rows(_COLLIDING[0]))


@pytest.mark.os_agnostic
def test_two_bitmaps_of_one_content_name_in_one_label_are_told_apart_by_the_salt() -> None:
    first, second = _COLLIDING
    label = Label(elements=[Image(x=1, y=1, name="A", bitmap=first), Image(x=1, y=9, name="B", bitmap=second)])
    salted = drawn_image_name(Bitmap.from_rows(second), salt=1)
    assert drawn_image_name(Bitmap.from_rows(second)) == _COLLIDED
    assert _job(label, **_NO_CLEAR).downloads == (_COLLIDED, salted)


@pytest.mark.os_agnostic
def test_a_later_label_s_other_bitmap_under_a_colliding_name_takes_that_name_as_nothing_is_kept() -> None:
    """The price of keeping nothing: a content name is taken to name its picture across labels.

    One collision needs about 2**21 bitmaps (above); within one label the salt still tells them apart.
    """
    first, second = _COLLIDING
    labels = [
        Label(elements=[Image(x=1, y=1, name="A", bitmap=first)]),
        Label(elements=[Image(x=1, y=1, name="B", bitmap=second)]),
    ]
    one, two = build_jobs(labels, _options(**_NO_CLEAR))
    assert one.downloads == two.downloads == (_COLLIDED,)


@pytest.mark.os_agnostic
def test_every_label_of_a_batch_goes_by_the_listing_it_is_given_and_says_each_held_name() -> None:
    first, again = build_jobs([Label(elements=[_LOGO])] * 2, _options(**_NO_CLEAR), held=_LOGO_HELD)
    assert (first.downloads, again.downloads) == ((), ())
    assert _L in _format(again.data)
    assert [_found(result, FindingCode.IMAGE_NAME_HELD) for result in (first, again)] == [[(Severity.WARNING, 0)]] * 2


@pytest.mark.os_agnostic
def test_the_prodigy_s_remove_after_takes_only_the_names_the_label_downloaded() -> None:
    options = {"model": "prodigy", "image_module": ImageModule.B, "clear_graphics_before_label": False}
    result = _job(Label(elements=[_ICON, _LOGO]), HeldImages(listed=frozenset({ICON_SENT})), **options)
    assert result.downloads == (LOGO_SENT,)
    assert STX + b"R" + _I not in result.data
    assert result.closing == STX + b"R" + _L + b"\r"


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
        _HOLDS_LOGO + b"MODULE: B\rICON\rAVAILABLE BYTES IN MODULE:00001000\r", kind=MemoryKind.GRAPHIC
    )
    assert held_images_from(directory, module=ImageModule.A).listed == {LOGO_SENT}
    assert held_images_from(directory, module=ImageModule.B).listed == {"ICON"}


@pytest.mark.os_agnostic
def test_a_directory_that_does_not_list_the_job_s_module_leaves_its_names_unread() -> None:
    """An answer cut before module B's block reads as B unknown, never as B holding nothing."""
    directory = parse_module_directory(
        b"MODULE: A\rOTHER\rAVAILABLE BYTES IN MODULE: 00049000\r", kind=MemoryKind.GRAPHIC
    )
    assert directory.parsed

    held = held_images_from(directory, module=ImageModule.B)

    assert held.listed is None
    assert held.unread_because == "the printer's directory answer did not list module B"


@pytest.mark.os_agnostic
def test_a_no_modules_available_answer_leaves_the_names_unread_and_says_what_the_manual_reads_into_it() -> None:
    directory = parse_module_directory(b"No Modules Available\r", kind=MemoryKind.GRAPHIC)
    assert directory.parsed

    held = held_images_from(directory, module=ImageModule.B)

    assert held.listed is None
    assert held.unread_because == (
        'the printer answered "No Modules Available" (the manual: corrupt or worn-out module)'
    )


@pytest.mark.os_agnostic
def test_a_module_the_directory_lists_with_no_names_holds_none() -> None:
    directory = parse_module_directory(
        b"MODULE: A\rOTHER\rAVAILABLE BYTES IN MODULE: 00049000\rMODULE: B\rAVAILABLE BYTES IN MODULE: 00049088\r",
        kind=MemoryKind.GRAPHIC,
    )
    assert held_images_from(directory, module=ImageModule.B).listed == frozenset()


@pytest.mark.os_agnostic
def test_an_unreadable_directory_gives_no_names() -> None:
    directory = parse_module_directory(b"MODULE: A\rLOGO\r", kind=MemoryKind.GRAPHIC)
    held = held_images_from(directory, module=ImageModule.A)
    assert held.listed is None
    assert "could not be read" in held.unread_because


@pytest.mark.os_agnostic
def test_the_default_module_gives_no_names_whatever_the_directory_lists() -> None:
    directory = parse_module_directory(_HOLDS_LOGO, kind=MemoryKind.GRAPHIC)
    held = held_images_from(directory, module=ImageModule.C)
    assert held.listed is None
    assert held.unread_because == "C is the printer's default module; which letter it is was not read"


@pytest.mark.os_agnostic
def test_a_names_answer_that_never_ends_is_cut_at_the_wait(serve: Callable[..., PrinterServer]) -> None:
    """An answer that starts in time but never falls silent ends at the wait, not at 120 s."""
    server = serve(trickling(_WG, _HOLDS_LOGO, every=0.2, for_seconds=8.0))
    started = time.monotonic()
    held = _read_names(
        server.address,
        wait=AnswerWait(seconds=1.5, wait_longer=_WAIT_LONGER),
    )
    waited = time.monotonic() - started
    server.join()

    assert held.listed is None
    assert held.unread_because == f"the answer was still arriving at the end of the 1.5 s wait; {_WAIT_LONGER}"
    assert waited < 4.0, waited


@pytest.mark.os_agnostic
def test_a_wait_shorter_than_the_ending_silence_still_reads_an_answer_that_comes_at_once(
    serve: Callable[..., PrinterServer],
) -> None:
    """The wait bounds the answer's last byte; the 1 s of silence that ends the answer comes on top."""
    server = serve(answering(_WG, _HOLDS_LOGO))
    held = _read_names(
        server.address,
        wait=AnswerWait(seconds=0.5),
    )
    server.join()

    assert held.listed == {LOGO_SENT}, held.unread_because


@pytest.mark.os_agnostic
def test_an_answer_whose_last_byte_comes_inside_the_wait_is_read(serve: Callable[..., PrinterServer]) -> None:
    """Last byte at 0.7 s of a 1.5 s wait: read, although its ending silence runs to 1.7 s."""
    server = serve(answering_over(_WG, _HOLDS_LOGO, seconds=0.7))
    held = _read_names(
        server.address,
        wait=AnswerWait(seconds=1.5),
    )
    server.join()

    assert held.listed == {LOGO_SENT}, held.unread_because


@pytest.mark.os_agnostic
def test_an_answer_still_arriving_when_the_wait_ends_is_unread(serve: Callable[..., PrinterServer]) -> None:
    """Last byte at 3 s of a 1 s wait: unread, and the read gives up at the wait plus the silence."""
    server = serve(answering_over(_WG, _HOLDS_LOGO, seconds=3.0))
    started = time.monotonic()
    held = _read_names(
        server.address,
        wait=AnswerWait(seconds=1.0, wait_longer=_WAIT_LONGER),
    )
    waited = time.monotonic() - started
    server.join()

    assert held.listed is None
    assert held.unread_because == f"the answer was still arriving at the end of the 1 s wait; {_WAIT_LONGER}"
    assert waited < 2.9, waited


@pytest.mark.os_agnostic
def test_a_printer_that_never_answers_the_names_request_is_unread_with_the_wait_named(
    serve: Callable[..., PrinterServer],
) -> None:
    server = serve(silent)
    held = _read_names(
        server.address,
        wait=AnswerWait(seconds=0.5, wait_longer=_WAIT_LONGER),
    )
    server.join()

    assert held.listed is None
    assert held.unread_because.startswith(f"reading them failed: {server.address}: no answer in "), held.unread_because
    assert _WAIT_LONGER in held.unread_because
    assert "did not end" not in held.unread_because


def _read_names(uri: str, *, wait: AnswerWait) -> HeldImages:
    """Read module A's names on a channel of the read's own, closing it after."""
    read = read_stored_names(
        None,
        module=ImageModule.A,
        endpoint=PrinterEndpoint(uri=uri, timeout=1.0, open_printer=build_production().open_printer),
        wait=wait,
    )
    if read.channel is not None:
        read.channel.close()
    return read.held


def _print(printer: ModulePrinter, label: Label, options: RenderOptions) -> RenderResult:
    """Print as ``print`` does over a two-way link: the names are read right before the downloads."""
    job = CheckedJob(
        renders=record_renders(label, None, options), check=UploadCheck(wait=AnswerWait(seconds=1.0)), reset=None
    )
    result = render_label(label, options, held=READ_AT_SEND)
    opener = open_recording_printer(printer)
    return print_checked(
        result, job, endpoint=PrinterEndpoint(uri="tcp://192.0.2.60:9100", timeout=1.0, open_printer=opener)
    ).result


#: A plug-in module: no clear may go, not even round 2's, so only a read can tell what it holds.
_PLUG_IN = _options(**_NO_CLEAR, image_module=ImageModule.B)


def _print_batch(printer: ModulePrinter, label: Label, records: int, options: RenderOptions) -> None:
    """Print ``records`` labels as ``print --data`` does over a two-way link."""
    rows: list[dict[str, str]] = [{}] * records
    report = check_batch(label, rows, options, read_names_at_send=True)
    job = CheckedJob(renders=report.renders(), check=UploadCheck(wait=AnswerWait(seconds=1.0)), reset=None)
    print_checked(
        report,
        job,
        endpoint=PrinterEndpoint(
            uri="tcp://192.0.2.60:9100", timeout=1.0, open_printer=open_recording_printer(printer)
        ),
    )


@pytest.mark.os_agnostic
def test_a_job_with_a_clear_before_reads_no_names_before_its_downloads() -> None:
    """The one directory request is the upload check's, after the download."""
    printer = ModulePrinter(ImageModules(held=[("A", LOGO_SENT, 2)]))
    _print(printer, Label(elements=[_LOGO]), _options(model="allegro-2"))
    assert printer.queries == [_WG]
    assert printer.data.index(STX + b"IAAF" + _L + b"\r") < printer.data.index(_WG)


@pytest.mark.os_agnostic
def test_a_job_with_no_clear_reads_the_names_before_its_downloads_and_leaves_out_the_held_one() -> None:
    modules = ImageModules(held=[("A", LOGO_SENT, 2)])
    printer = ModulePrinter(modules)
    result = _print(printer, Label(elements=[_ICON, _LOGO]), _options(**_NO_CLEAR))
    assert printer.queries == [_WG, _WG]
    assert printer.data.index(_WG) < printer.data.index(STX + b"IAAF" + _I + b"\r")
    assert STX + b"IAAF" + _L not in printer.data
    assert _L in _format(printer.data)
    assert (modules.downloaded_held, modules.placed_missing) == ([], [])
    assert _found(result, FindingCode.IMAGE_NAME_HELD) == [(Severity.WARNING, 1)]


@pytest.mark.os_agnostic
def test_a_job_without_downloads_reads_no_names_even_with_no_clear() -> None:
    printer = ModulePrinter(ImageModules(held=[("A", LOGO_SENT, 2)]))
    _print(printer, Label(elements=[_STORED]), _options(**_NO_CLEAR))
    assert printer.queries == []


@pytest.mark.os_agnostic
def test_a_first_label_whose_read_gets_no_answer_keeps_every_download_and_is_still_sent() -> None:
    """Nothing of the job went out before it: no name of its can have been left by this job."""
    modules = ImageModules(silent=[0])
    printer = ModulePrinter(modules)
    result = _print(printer, Label(elements=[_LOGO]), _options(**_NO_CLEAR))
    assert printer.queries == [_WG, _WG]
    assert modules.downloads == [LOGO_SENT]
    assert modules.placed_missing == []
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_NAMES_UNREAD]
    assert "no answer" in finding.message


@pytest.mark.os_agnostic
def test_every_label_of_a_batch_reads_the_names_right_before_its_downloads() -> None:
    """The second label finds the name the first one stored, and so places it without a download."""
    modules = ImageModules()
    printer = ModulePrinter(modules)
    _print_batch(printer, Label(elements=[_LOGO]), 2, _options(**_NO_CLEAR))
    assert printer.queries == [_WG, _WG, _WG]
    assert modules.downloads == [LOGO_SENT]
    assert (modules.downloaded_held, modules.placed_missing) == ([], [])


@pytest.mark.os_agnostic
def test_a_name_another_program_removed_between_two_labels_is_downloaded_again() -> None:
    """What the printer holds, not what the batch sent, decides: a list kept by the job would skip it."""
    modules = ImageModules()
    printer = ModulePrinter(modules)
    label = Label(elements=[_LOGO])
    report = check_batch(label, [{}, {}], _options(**_NO_CLEAR), read_names_at_send=True)
    renders = iter(report.renders())
    first = next(renders)

    def emptied_after_the_first() -> Iterator[RecordRender]:
        yield first
        modules.stored.clear()
        yield from renders

    job = CheckedJob(renders=emptied_after_the_first(), check=UploadCheck(wait=AnswerWait(seconds=1.0)), reset=None)
    print_checked(
        report,
        job,
        endpoint=PrinterEndpoint(
            uri="tcp://192.0.2.60:9100", timeout=1.0, open_printer=open_recording_printer(printer)
        ),
    )

    assert modules.downloads == [LOGO_SENT, LOGO_SENT]
    assert modules.placed_missing == []


@pytest.mark.os_agnostic
def test_a_later_label_whose_read_gets_no_answer_uploads_none_of_its_names_until_a_read_answers() -> None:
    """Request 2 is the second label's read before its downloads; the next read, round 2's, answers."""
    modules = ImageModules(silent=[2])
    printer = ModulePrinter(modules)
    _print_batch(printer, Label(elements=[_LOGO]), 2, _PLUG_IN)
    assert modules.downloads == [LOGO_SENT]
    assert (modules.downloaded_held, modules.placed_missing) == ([], [])


@pytest.mark.os_agnostic
def test_a_later_label_no_read_of_which_answers_is_refused_and_downloads_nothing() -> None:
    modules = ImageModules(silent=range(2, 20))
    printer = ModulePrinter(modules)
    with pytest.raises(UploadRefusedError) as refused:
        _print_batch(printer, Label(elements=[_LOGO]), 2, _PLUG_IN)
    report = refused.value.result
    assert isinstance(report, BatchReport)
    messages = " ".join(reported.finding.message for reported in report.findings)
    assert "round 1 skipped: the names read before the label's downloads got no answer" in messages
    assert modules.downloads == [LOGO_SENT]
    assert modules.downloaded_held == []


@pytest.mark.os_agnostic
def test_a_label_for_the_default_module_is_refused_on_a_two_way_link_and_its_names_are_not_asked() -> None:
    """Nothing the directory could say would name the module, so it is not asked."""
    printer = ModulePrinter(ImageModules())
    with pytest.raises(UploadRefusedError):
        _print(printer, Label(elements=[_LOGO]), _options(model="allegro-2", image_module=ImageModule.C))
    assert printer.queries == []
    assert STX + b"IC" not in printer.data


# ======================== the CLI over a real channel ========================


def _run(runner: CliRunner, args: list[str], *, stdin: str | None = None, **env: str) -> Any:
    return runner.invoke(cli_mod.cli, args, obj=build_production, env=env, input=stdin)


class _LabelSource(NamedTuple):
    """A label file's JSON, written where a test asks."""

    text: str

    def write(self, tmp_path: Path) -> str:
        path = tmp_path / "label.json"
        path.write_text(self.text, encoding="utf-8")
        return str(path)


#: One code per record: each record's picture differs, so each downloads its own.
_QR_LABEL = _LabelSource(json.dumps({"elements": [{"type": "qr", "x": 5, "y": 5, "data": "{code}"}]}))
_TWO_CODES = '[{"code": "A1"}, {"code": "B2"}]'


def _label_file(tmp_path: Path, *elements: Image) -> str:
    path = tmp_path / "label.json"
    path.write_text(
        json.dumps({"elements": [element.model_dump(mode="json") for element in elements]}), encoding="utf-8"
    )
    return str(path)


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_with_the_clear_on_reads_no_directory_before_the_downloads(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[[Play], PrinterServer]
) -> None:
    """The one directory request is the upload check's, after the download; none comes before the job."""
    server = serve(module_printer(ImageModules()))

    result = _run(
        cli_runner, ["print", _label_file(tmp_path, _LOGO), "--model", "allegro-2", "--printer", server.address]
    )
    server.join()

    assert result.exit_code == 0, result.output
    received = bytes(server.received)
    download = received.index(STX + b"IAAF" + _L + b"\r")
    assert received.count(STX + b"W") == 1
    assert received.index(_WG) > download


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_to_a_plug_in_module_reads_the_names_and_leaves_out_the_held_download(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[..., PrinterServer]
) -> None:
    modules = ImageModules(held=[("B", LOGO_SENT, 2)])
    server = serve(module_printer(modules))
    args = ["print", _label_file(tmp_path, _ICON, _LOGO), "--model", "allegro-2", "--printer", server.address]

    result = _run(cli_runner, ["--json", *args], DATAMAX_DPL___PRINTER__IMAGE_MODULE="B")
    server.join()

    assert result.exit_code == 0, result.output
    # The names are read on the job's own connection, right before the downloads.
    [job] = (bytes(connection) for connection in server.connections)
    assert job.index(_WG) < job.index(STX + b"IBAF" + _I + b"\r")
    assert STX + b"IBAF" + _L not in job
    assert STX + b"q" not in job
    codes = [finding["code"] for finding in json.loads(result.stdout)["data"]["findings"]]
    assert "image_name_held" in codes
    # The upload check then finds both: the stored LOGO and the ICON just downloaded.
    assert modules.names("B") == [LOGO_SENT, ICON_SENT]


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_with_data_on_stdin_and_no_clear_reads_the_records_once_and_sends_each_label(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[..., PrinterServer]
) -> None:
    modules = ImageModules()
    server = serve(module_printer(modules))
    args = ["print", _QR_LABEL.write(tmp_path), "--data", "-", "--model", "allegro-2", "--printer", server.address]

    result = _run(cli_runner, args, stdin=_TWO_CODES, DATAMAX_DPL___PRINTER__IMAGE_MODULE="B")
    server.join()

    assert result.exit_code == 0, result.output
    assert len(modules.downloads) == 2
    assert (modules.downloaded_held, modules.placed_missing) == ([], [])


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
@pytest.mark.parametrize("command", ["print", "render", "validate"])
def test_a_no_clear_image_batch_no_printer_is_read_for_is_refused_and_writes_nothing(
    cli_runner: CliRunner, tmp_path: Path, command: str
) -> None:
    """Each label's names must be read right before its downloads; a file or stdout cannot be read."""
    target = tmp_path / "job.dpl"
    where = (
        ["--printer", f"file:{target}"] if command == "print" else ["-o", str(target)] if command == "render" else []
    )
    args = ["--json", command, _QR_LABEL.write(tmp_path), "--data", "-", "--model", "allegro-2", *where]

    result = _run(cli_runner, args, stdin=_TWO_CODES, DATAMAX_DPL___PRINTER__IMAGE_MODULE="B")

    assert result.exit_code != 0, result.output
    assert not target.exists()
    findings = json.loads(result.stdout)["data"]["findings"]
    [refusal] = {finding["message"] for finding in findings if finding["code"] == "batch_image_names_unread"}
    assert "two-way printer" in refusal
    assert "clear_graphics_before_label" in refusal


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
@pytest.mark.parametrize("command", ["print", "render", "validate"])
def test_a_one_record_no_clear_image_batch_is_judged_as_a_single_label_is(
    cli_runner: CliRunner, tmp_path: Path, command: str
) -> None:
    """No earlier label of the batch can have left a name, so it says its names were not read and goes out."""
    target = tmp_path / "job.dpl"
    where = (
        ["--printer", f"file:{target}"] if command == "print" else ["-o", str(target)] if command == "render" else []
    )
    args = ["--json", command, _QR_LABEL.write(tmp_path), "--data", "-", "--model", "allegro-2", *where]

    result = _run(cli_runner, args, stdin='[{"code": "A1"}]', DATAMAX_DPL___PRINTER__IMAGE_MODULE="B")

    assert result.exit_code == 0, result.output
    assert target.exists() == (command != "validate")
    codes = {finding["code"] for finding in json.loads(result.stdout)["data"]["findings"]}
    assert "batch_image_names_unread" not in codes
    assert "image_names_unread" in codes


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_validate_of_a_no_clear_image_batch_with_a_two_way_printer_named_is_not_refused(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Print would read that printer before each label, so the batch is not refused; validate reads nothing."""
    args = ["--json", "validate", _QR_LABEL.write(tmp_path), "--data", "-", "--model", "allegro-2"]

    result = _run(
        cli_runner,
        args,
        stdin=_TWO_CODES,
        DATAMAX_DPL___PRINTER__IMAGE_MODULE="B",
        DATAMAX_DPL___PRINTER__URI="tcp://192.0.2.60:9100",
    )

    assert result.exit_code == 0, result.output
    codes = {finding["code"] for finding in json.loads(result.stdout)["data"]["findings"]}
    assert "batch_image_names_unread" not in codes


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_a_single_label_printed_to_a_file_with_no_clear_says_the_link_is_one_way(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    target = tmp_path / "job.dpl"
    args = ["--json", "print", _label_file(tmp_path, _LOGO), "--model", "allegro-2", "--printer", f"file:{target}"]

    result = _run(cli_runner, args, DATAMAX_DPL___PRINTER__IMAGE_MODULE="B")

    assert result.exit_code == 0, result.output
    assert STX + b"IBAF" + _L + b"\r" in target.read_bytes()
    findings = json.loads(result.stdout)["data"]["findings"]
    [unread] = [finding["message"] for finding in findings if finding["code"] == "image_names_unread"]
    assert "one-way" in unread


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
def test_print_says_the_names_answer_was_still_arriving_at_the_end_of_stored_names_wait(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[..., PrinterServer]
) -> None:
    server = serve(trickling(_WG, _HOLDS_LOGO, every=0.2, for_seconds=4.0), module_printer(ImageModules()))
    args = ["--json", "print", _label_file(tmp_path, _LOGO), "--model", "allegro-2", "--printer", server.address]

    result = _run(
        cli_runner, args, DATAMAX_DPL___PRINTER__IMAGE_MODULE="B", DATAMAX_DPL___PRINTER__STORED_NAMES_WAIT="1"
    )
    server.join()

    assert result.exit_code == 0, result.output
    findings = json.loads(result.stdout)["data"]["findings"]
    [unread] = [finding["message"] for finding in findings if finding["code"] == "image_names_unread"]
    assert (
        "the answer was still arriving at the end of the 1 s wait; wait longer with [printer] stored_names_wait"
        in unread
    )


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_reads_the_stored_names_for_stored_names_wait_and_then_sends_the_job(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[..., PrinterServer]
) -> None:
    """A printer silent to the read is waited for stored_names_wait (1 s here), not status_wait (20 s).

    The channel that did not answer is given up; the job goes on a new one, whose upload check reads once.
    """
    modules = ImageModules(silent=[0])
    server = serve(module_printer(modules), module_printer(modules))
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
    unanswered, job = (bytes(connection) for connection in server.connections)
    assert unanswered.endswith(_WG)
    assert STX + b"I" not in unanswered
    assert STX + b"IBAF" + _L + b"\r" in job
    assert job.count(_WG) == 1
    [finding] = [f for f in json.loads(result.stdout)["data"]["findings"] if f["code"] == "image_names_unread"]
    assert "reading them failed" in finding["message"]
    assert "stored_names_wait" in finding["message"]
