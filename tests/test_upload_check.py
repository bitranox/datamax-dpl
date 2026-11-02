"""print and verify send a label's format only once the printer lists every image it downloaded.

A download into a full image module is lost with no error (one Allegro 2, 2026-10-31), and the
format then prints without it. So on a two-way link each label that downloads an image goes out
in two parts, with the graphic directory (``<STX>W``) read between them: missing names get a
clear and a second upload, then the images halved and a third upload, then a refusal with the
format never sent and the module cleared again. The printer here is :class:`ImageModules`, which
stores a download only while its module has room and answers the directory from what it holds.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import pytest
from printer_server import ImageModules, ModulePrinter, module_printer, module_printer_closing

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
from datamax_dpl.application.upload_check import (
    CheckedJob,
    Link,
    UploadCheck,
    UploadRefusedError,
    print_checked,
    send_checked,
)
from datamax_dpl.application.use_cases import AnswerWait, BatchResult, record_renders, render_batch, render_label
from datamax_dpl.application.verify import verify_print
from datamax_dpl.composition import build_production
from datamax_dpl.domain.camera import CameraKind, CameraSettings, CapturedImage
from datamax_dpl.domain.enums import FallbackPolicy, FindingCode, ImageModule, MediaType, MemoryKind, Severity
from datamax_dpl.domain.errors import ConfigurationError, LabelRejectedError, PrinterTimeoutError, TransportError
from datamax_dpl.domain.held_images import HeldImages
from datamax_dpl.domain.label import Image, Label, Qr, Text
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.raster import Bitmap
from datamax_dpl.domain.render import RenderedRecord, RenderResult, build_jobs, build_record
from datamax_dpl.domain.render_context import RenderOptions, drawn_image_name
from datamax_dpl.domain.status import MEMORY_DIRECTORY_REQUESTS, STATUS_REQUEST

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from click.testing import CliRunner
    from printer_server import Play, PrinterServer

    from datamax_dpl.application.ports import PrinterTransport

STX = b"\x02"
WG = MEMORY_DIRECTORY_REQUESTS[MemoryKind.GRAPHIC]
FORMAT = STX + b"L\r"
URI = "tcp://192.0.2.60:9100"
#: 64 x 64 dots: 512 bytes of dot data, 128 once halved (the fake counts dot data, not content).
_PATTERN = ["".join("#" if (row * 7 + column * 13) % 5 == 0 else "." for column in range(64)) for row in range(64)]
LOGO = Image(x=1, y=1, name="LOGO", bitmap=_PATTERN)
#: Room for the halved LOGO and not for the whole one.
HALF_ROOM = 300
ALLEGRO = RenderOptions(model="allegro-2")


def _check(rounds: int = 3) -> UploadCheck:
    return UploadCheck(wait=AnswerWait(seconds=1.0), rounds=rounds)


def _job(
    label: Label, options: RenderOptions, records: Sequence[Mapping[str, str]] | None, rounds: int
) -> tuple[RenderResult | BatchResult, CheckedJob]:
    result = render_label(label, options) if records is None else render_batch(label, records, options)
    return result, CheckedJob(renders=record_renders(label, records, options), check=_check(rounds))


def _print(
    modules: ImageModules,
    label: Label,
    options: RenderOptions = ALLEGRO,
    *,
    records: Sequence[Mapping[str, str]] | None = None,
    rounds: int = 3,
) -> tuple[ModulePrinter, RenderResult | BatchResult]:
    """Print with the upload check; return the printer and the job as it went out."""
    printer = ModulePrinter(modules)
    result, job = _job(label, options, records, rounds)
    report = print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
    assert report.bytes_sent == sum(len(chunk) for chunk in printer.sent if chunk != WG)
    return printer, report.result


def _single(result: RenderResult | BatchResult) -> RenderResult:
    assert isinstance(result, RenderResult)
    return result


def _refused(
    modules: ImageModules, label: Label, options: RenderOptions = ALLEGRO, *, rounds: int = 3
) -> tuple[ModulePrinter, UploadRefusedError]:
    printer = ModulePrinter(modules)
    result, job = _job(label, options, None, rounds)
    with pytest.raises(UploadRefusedError) as caught:
        print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
    assert caught.value.bytes_sent == sum(len(chunk) for chunk in printer.sent if chunk != WG)
    return printer, caught.value


def _upload_codes(result: RenderResult) -> list[tuple[FindingCode, Severity]]:
    checked = (FindingCode.IMAGE_UPLOAD_RETRIED, FindingCode.IMAGE_UPLOAD_REFUSED)
    return [(f.code, f.severity) for f in result.findings if f.code in checked]


def _message(result: RenderResult, code: FindingCode) -> str:
    [finding] = [f for f in result.findings if f.code is code]
    return finding.message


@pytest.mark.os_agnostic
def test_a_label_whose_images_are_listed_sends_its_format_once_after_one_read() -> None:
    printer, result = _print(ImageModules(), Label(elements=[LOGO]))

    upload, request, rest = printer.sent
    assert request == WG
    assert upload.endswith(b"FFFF\r") and FORMAT not in upload
    assert rest.startswith(FORMAT) and rest.endswith(STX + b"qA")
    assert upload + rest == render_label(Label(elements=[LOGO]), ALLEGRO).data
    assert _upload_codes(_single(result)) == []


@pytest.mark.os_agnostic
def test_a_download_lost_once_is_cleared_and_uploaded_again_before_the_format() -> None:
    printer, sent = _print(ImageModules(lose={0}), Label(elements=[LOGO]))

    upload, request, clear, again, second, rest = printer.sent
    assert (request, clear, second) == (WG, STX + b"qA", WG)
    assert again == upload
    assert rest.startswith(FORMAT)
    assert printer.data.count(FORMAT) == 1
    result = _single(sent)
    assert _upload_codes(result) == [(FindingCode.IMAGE_UPLOAD_RETRIED, Severity.INFO)]
    message = _message(result, FindingCode.IMAGE_UPLOAD_RETRIED)
    assert "round 2: cleared module A" in message
    assert "listed every image" in message


@pytest.mark.os_agnostic
def test_images_too_large_after_the_clear_are_sent_halved_and_the_format_places_the_halved_one() -> None:
    label = Label(elements=[LOGO])
    [full] = render_label(label, ALLEGRO).downloads
    modules = ImageModules(capacity=HALF_ROOM)

    printer, sent = _print(modules, label)

    result = _single(sent)
    assert result.halved_elements == (0,)
    [halved] = result.downloads
    assert halved != full
    # Stored, then removed by the clear after the label.
    assert modules.downloads == [full, full, halved]
    assert modules.names("A") == []
    [rest] = [chunk for chunk in printer.sent if chunk.startswith(FORMAT)]
    assert halved.encode("ascii") in rest
    assert full.encode("ascii") not in rest
    assert _upload_codes(result) == [(FindingCode.IMAGE_UPLOAD_RETRIED, Severity.INFO)] * 2
    assert "round 3" in [f.message for f in result.findings if f.code is FindingCode.IMAGE_UPLOAD_RETRIED][1]
    assert FindingCode.IMAGE_HALVED in [f.code for f in result.findings]


@pytest.mark.os_agnostic
def test_images_never_listed_refuse_the_label_send_no_format_and_clear_the_module_again() -> None:
    printer, refused = _refused(ImageModules(capacity=1), Label(elements=[LOGO]))

    assert FORMAT not in printer.data
    assert printer.sent[-1] == STX + b"qA"
    assert printer.queries == [WG, WG, WG]
    result = _single(refused.result)
    assert not result.ok
    assert _upload_codes(result)[-1] == (FindingCode.IMAGE_UPLOAD_REFUSED, Severity.ERROR)
    message = _message(result, FindingCode.IMAGE_UPLOAD_REFUSED)
    assert "after 3 rounds" in message
    assert result.downloads[0] in message
    assert refused.findings == result.errors


@pytest.mark.os_agnostic
def test_a_read_that_gets_no_answer_counts_as_missing_in_every_round() -> None:
    printer, refused = _refused(ImageModules(answers=False), Label(elements=[LOGO]))

    assert FORMAT not in printer.data
    assert printer.queries == [WG, WG, WG]
    message = _message(_single(refused.result), FindingCode.IMAGE_UPLOAD_REFUSED)
    assert "could not be read" in message
    assert "no answer" in message


@pytest.mark.os_agnostic
def test_upload_check_rounds_1_refuses_after_the_first_read_with_no_clear_and_no_halving() -> None:
    printer, refused = _refused(ImageModules(capacity=1), Label(elements=[LOGO]), rounds=1)

    assert printer.queries == [WG]
    _upload, request, clear = printer.sent
    assert (request, clear) == (WG, STX + b"qA")
    assert "after 1 round " in _message(_single(refused.result), FindingCode.IMAGE_UPLOAD_REFUSED)


@pytest.mark.os_agnostic
def test_a_plug_in_module_is_never_cleared_in_any_round() -> None:
    options = RenderOptions(model="allegro-2", image_module=ImageModule.B)

    printer, refused = _refused(ImageModules(capacity=1), Label(elements=[LOGO]), options)

    assert STX + b"q" not in printer.data
    assert printer.queries == [WG, WG, WG]
    retried = [f.message for f in _single(refused.result).findings if f.code is FindingCode.IMAGE_UPLOAD_RETRIED]
    assert all("sent no clear (module B may not be cleared)" in message for message in retried)


#: A second image for a label with two downloads, a different picture from LOGO.
ICON = Image(x=1, y=40, name="ICON", bitmap=[row[::-1] for row in _PATTERN])
PLUG_IN = RenderOptions(model="allegro-2", image_module=ImageModule.B)


@pytest.mark.os_agnostic
def test_a_module_that_may_not_be_cleared_gets_again_only_the_downloads_its_directory_did_not_list() -> None:
    label = Label(elements=[LOGO, ICON])
    logo, icon = render_label(label, PLUG_IN).downloads
    modules = ImageModules(lose={1})

    printer, sent = _print(modules, label, PLUG_IN)

    assert modules.downloads == [logo, icon, icon]
    assert modules.downloaded_held == []
    assert STX + b"q" not in printer.data
    assert printer.data.count(FORMAT) == 1
    [rest] = [chunk for chunk in printer.sent if chunk.startswith(FORMAT)]
    assert logo.encode("ascii") in rest and icon.encode("ascii") in rest
    assert _upload_codes(_single(sent)) == [(FindingCode.IMAGE_UPLOAD_RETRIED, Severity.INFO)]


@pytest.mark.os_agnostic
def test_the_halving_round_on_such_a_module_leaves_out_a_qr_code_its_directory_listed() -> None:
    label = Label(elements=[Qr(x=5, y=150, data="CODE"), LOGO])
    qr, _logo = render_label(label, PLUG_IN).downloads
    modules = ImageModules(lose={1, 2})

    _, sent = _print(modules, label, PLUG_IN)

    result = _single(sent)
    assert result.halved_elements == (1,)
    assert modules.downloads.count(qr) == 1
    assert modules.downloaded_held == []


@pytest.mark.os_agnostic
def test_a_batch_on_such_a_module_carries_what_the_directory_listed_to_the_next_record() -> None:
    label = Label(elements=[LOGO, ICON])
    records = [{"n": "1"}, {"n": "2"}]
    modules = ImageModules(lose={1, 2})

    printer, sent = _print(modules, label, PLUG_IN, records=records)

    assert isinstance(sent, BatchResult)
    assert sent.results[0].halved_elements == (0, 1)
    assert modules.downloaded_held == []
    assert printer.data.count(FORMAT) == 2


@pytest.mark.os_agnostic
@pytest.mark.parametrize("rounds", [2, 3])
def test_such_a_module_reads_the_names_again_in_round_2_after_a_read_with_no_answer_and_prints_at_full_size(
    rounds: int,
) -> None:
    """LOGO arrives, but the read after round 1 gets no answer: round 2 reads again, uploading nothing first.

    The read lists LOGO, so the label places it at full size with nothing left to download.
    """
    label = Label(elements=[LOGO])
    [logo] = render_label(label, PLUG_IN_TWO_WAY).downloads
    modules = ImageModules(silent={0})
    printer = ModulePrinter(modules)
    result = render_label(label, PLUG_IN_TWO_WAY, held=READ_EMPTY)
    job = CheckedJob(renders=record_renders(label, None, PLUG_IN_TWO_WAY), check=_check(rounds), held=READ_EMPTY)

    report = print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    sent = _single(report.result)
    assert (sent.halved_elements, sent.downloads) == ((), ())
    assert modules.downloads == [logo]
    assert modules.downloaded_held == []
    assert modules.placed_missing == []
    assert printer.queries == [WG, WG]
    upload, first_read, second_read, rest = printer.sent
    assert (first_read, second_read) == (WG, WG)
    assert logo.encode("ascii") in upload
    assert rest.count(FORMAT) == 1 and logo.encode("ascii") in rest
    assert _message(sent, FindingCode.IMAGE_UPLOAD_RETRIED) == (
        "round 2: sent no clear (module B may not be cleared), read module B again first as the read before it gave "
        "no listing (module B then listed every image), then sent nothing of the images again: the module holds each "
        "one already"
    )


@pytest.mark.os_agnostic
def test_such_a_module_skips_round_2_when_the_names_read_again_get_no_answer_and_halves_under_new_names() -> None:
    label = Label(elements=[LOGO])
    [logo] = render_label(label, PLUG_IN).downloads
    modules = ImageModules(silent={0, 1})

    printer, sent = _print(modules, label, PLUG_IN)

    result = _single(sent)
    assert modules.downloaded_held == []
    assert modules.downloads.count(logo) == 1
    assert result.halved_elements == (0,)
    # Round 1's read, the names read again in round 2, round 3's read.
    assert printer.queries == [WG, WG, WG]
    assert printer.data.count(FORMAT) == 1
    first, second = (f.message for f in result.findings if f.code is FindingCode.IMAGE_UPLOAD_RETRIED)
    assert first.startswith(f"round 2 skipped, it would download again {logo}:")
    assert f"; the names read again got no answer either (reading them failed: {URI}: no answer in " in first
    assert second.startswith("round 3:")


@pytest.mark.os_agnostic
def test_such_a_module_refuses_after_a_read_with_no_answer_when_nothing_can_be_halved() -> None:
    label = Label(elements=[Qr(x=5, y=150, data="CODE")])

    printer, refused = _refused(ImageModules(silent={0, 1}), label, PLUG_IN)

    result = _single(refused.result)
    assert FORMAT not in printer.data
    assert printer.queries == [WG, WG]
    assert len(printer.sent) == 3  # the one upload, its read, the names read again; no clear may go
    message = _message(result, FindingCode.IMAGE_UPLOAD_REFUSED)
    assert "after 1 round " in message
    assert "round 2 skipped," in message
    assert "no image of the label can be halved" in message
    assert _message(result, FindingCode.IMAGE_UPLOAD_RETRIED).startswith("round 2 skipped,")


@pytest.mark.os_agnostic
def test_such_a_module_refuses_the_halving_round_that_would_download_an_unread_qr_code_again() -> None:
    label = Label(elements=[Qr(x=5, y=150, data="CODE"), LOGO])
    qr, _logo = render_label(label, PLUG_IN).downloads
    modules = ImageModules(silent={0, 1})

    printer, refused = _refused(modules, label, PLUG_IN)

    assert modules.downloaded_held == []
    assert modules.downloads.count(qr) == 1
    assert FORMAT not in printer.data
    assert printer.queries == [WG, WG]
    message = _message(_single(refused.result), FindingCode.IMAGE_UPLOAD_REFUSED)
    assert "round 2 skipped," in message
    assert (
        f"halving is skipped, it would download again (a QR Code is never halved, so its name stays) {qr}:" in message
    )


@pytest.mark.os_agnostic
def test_such_a_module_halves_from_the_last_answered_read_when_the_round_2_read_got_no_answer() -> None:
    label = Label(elements=[Qr(x=5, y=150, data="CODE"), LOGO])
    qr, logo = render_label(label, PLUG_IN).downloads
    modules = ImageModules(lose={1}, silent={1})

    printer, sent = _print(modules, label, PLUG_IN)

    result = _single(sent)
    assert modules.downloaded_held == []
    assert modules.downloads.count(qr) == 1
    assert modules.downloads.count(logo) == 2
    assert result.halved_elements == (1,)
    assert printer.queries == [WG, WG, WG]
    assert printer.data.count(FORMAT) == 1


#: A plug-in module rendered for a two-way print: the check, not the model's budget, decides halving.
PLUG_IN_TWO_WAY = PLUG_IN.model_copy(update={"model_image_budget": False})
#: The module read before the job listed nothing there.
READ_EMPTY = HeldImages(listed=frozenset())


def _print_batch(modules: ImageModules, renders: Sequence[RenderResult], job: CheckedJob) -> BatchResult:
    """Print a batch of ``renders`` with ``job``'s upload check; return the batch as it went out."""
    printer = ModulePrinter(modules)
    batch = BatchResult(data=b"".join(r.data for r in renders), results=tuple(renders))
    report = print_checked(batch, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
    assert isinstance(report.result, BatchResult)
    assert printer.data.count(FORMAT) == len(renders)
    return report.result


def _assert_each_format_places_a_stored_image(sent: BatchResult, modules: ImageModules, module: str) -> None:
    """Every record's format names an image, and the module holds each one it names (nothing removes one here)."""
    candidates = set(modules.downloads) | set(modules.names(module))
    for record in sent.results:
        rest = record.data[record.upload_end :]
        placed = {name for name in candidates if name.encode("ascii") in rest}
        assert placed, "a format went out naming no image"
        assert placed <= set(modules.names(module))


@pytest.mark.os_agnostic
def test_a_batch_record_that_passed_after_a_read_with_no_answer_passes_on_what_its_last_read_listed() -> None:
    """Record 1's first download arrived though its read, and the names read again, got no answer.

    Record 2 must not download it again.
    """
    label = Label(elements=[LOGO])
    records = [{"n": "1"}, {"n": "2"}]
    modules = ImageModules(silent={0, 1})
    renders = render_batch(label, records, PLUG_IN_TWO_WAY, held=READ_EMPTY).results
    job = CheckedJob(renders=record_renders(label, records, PLUG_IN_TWO_WAY), check=_check(), held=READ_EMPTY)

    sent = _print_batch(modules, renders, job)

    assert modules.downloaded_held == []
    assert sent.results[0].halved_elements == (0,)
    _assert_each_format_places_a_stored_image(sent, modules, "B")


@pytest.mark.os_agnostic
def test_a_batch_record_whose_round_2_read_got_no_answer_passes_on_the_names_round_3_found_listed() -> None:
    """Round 2 sent ICON again and its read got no answer; record 2 must not download that ICON again."""
    label = Label(elements=[LOGO, ICON])
    records = [{"n": "1"}, {"n": "2"}]
    modules = ImageModules(lose={1}, silent={1})
    renders = render_batch(label, records, PLUG_IN_TWO_WAY, held=READ_EMPTY).results
    job = CheckedJob(renders=record_renders(label, records, PLUG_IN_TWO_WAY), check=_check(), held=READ_EMPTY)

    sent = _print_batch(modules, renders, job)

    assert modules.downloaded_held == []
    _assert_each_format_places_a_stored_image(sent, modules, "B")


@pytest.mark.os_agnostic
def test_a_name_a_record_sent_before_a_read_with_no_answer_is_not_downloaded_by_the_next_record() -> None:
    """Record 2 sends LOGO full size, its read gets no answer, and it passes placing the halved LOGO.

    Record 3 would download that full-size LOGO again, which the module may hold, and the names
    read once more before its round 1, and again in its round 2, get no answer either: its rounds 1
    and 2 are left out, and it places the halved LOGO too.
    """
    label = Label(elements=[LOGO])
    records = [{"n": "1"}, {"n": "2"}, {"n": "3"}]
    [logo] = render_label(label, PLUG_IN_TWO_WAY).downloads
    modules = ImageModules(lose={0}, silent={0, 1, 3, 4, 5, 6})
    renders = render_batch(label, records, PLUG_IN_TWO_WAY, held=READ_EMPTY).results
    job = CheckedJob(renders=record_renders(label, records, PLUG_IN_TWO_WAY), check=_check(), held=READ_EMPTY)

    sent = _print_batch(modules, renders, job)

    assert modules.downloaded_held == []
    assert modules.downloads.count(logo) == 2
    assert [record.downloads for record in sent.results[1:]] == [(), ()]
    first, *_ = (f.message for f in sent.results[2].findings if f.code is FindingCode.IMAGE_UPLOAD_RETRIED)
    assert first.startswith(f"round 1 skipped, it would download again {logo}:")
    assert f"; the names read before round 1 got no answer either (reading them failed: {URI}: no answer in " in first
    _assert_each_format_places_a_stored_image(sent, modules, "B")
    assert modules.placed_missing == []


@pytest.mark.os_agnostic
def test_a_record_meeting_names_that_may_be_held_reads_the_names_first_and_prints_at_full_size() -> None:
    """Record 2 sends LOGO full size, its reads get no answer, and it passes placing the halved LOGO.

    Record 3 would download that full-size LOGO again: it reads the names first, finds LOGO
    listed, and places it at full size with nothing downloaded.
    """
    label = Label(elements=[LOGO])
    records = [{"n": "1"}, {"n": "2"}, {"n": "3"}]
    [logo] = render_label(label, PLUG_IN_TWO_WAY).downloads
    modules = ImageModules(lose={0, 1}, silent={3, 4})
    renders = render_batch(label, records, PLUG_IN_TWO_WAY, held=READ_EMPTY).results
    job = CheckedJob(renders=record_renders(label, records, PLUG_IN_TWO_WAY), check=_check(), held=READ_EMPTY)

    sent = _print_batch(modules, renders, job)

    assert modules.downloaded_held == []
    assert modules.placed_missing == []
    third = sent.results[2]
    assert third.halved_elements == ()
    assert third.downloads == ()
    assert logo.encode("ascii") in third.data
    assert _upload_codes(third) == []
    # Three reads for record 1, two for record 2 (round 1's, the names read again in round 2; its
    # round 3 downloads nothing, so it reads nothing), and record 3's one read before its round 1.
    assert modules.requests == 6


@pytest.mark.os_agnostic
def test_a_name_that_may_be_held_is_carried_past_a_record_whose_reads_got_no_answer_to_the_one_after() -> None:
    """Record 2 sends LOGO full size and its reads get no answer; so do record 3's, which sends nothing of it.

    Record 3 never sent that LOGO itself, yet it may still be held: record 4 must read the names
    before downloading it, and finding it listed places it at full size with nothing downloaded.
    """
    label = Label(elements=[LOGO])
    records = [{"n": "1"}, {"n": "2"}, {"n": "3"}, {"n": "4"}]
    [logo] = render_label(label, PLUG_IN_TWO_WAY).downloads
    modules = ImageModules(lose={0}, silent={0, 1, 3, 4, 5, 6})
    renders = render_batch(label, records, PLUG_IN_TWO_WAY, held=READ_EMPTY).results
    job = CheckedJob(renders=record_renders(label, records, PLUG_IN_TWO_WAY), check=_check(), held=READ_EMPTY)

    sent = _print_batch(modules, renders, job)

    assert modules.downloaded_held == []
    assert modules.placed_missing == []
    assert modules.downloads.count(logo) == 2
    assert [record.halved_elements for record in sent.results] == [(0,), (0,), (0,), ()]
    assert [record.downloads for record in sent.results[1:]] == [(), (), ()]
    assert _upload_codes(sent.results[3]) == []
    # Record 1's three reads, record 2's two, record 3's two (before round 1, in round 2), and
    # record 4's one before its round 1.
    assert modules.requests == 8


@pytest.mark.os_agnostic
def test_a_round_that_downloads_nothing_sends_no_upload_reads_nothing_and_says_so() -> None:
    """Record 2's round 3 halves LOGO into the name record 1 left: nothing to upload, so nothing to read.

    Record 3's reads before its round 1 and in its round 2 get no answer, so it is halved the same way.
    """
    label = Label(elements=[LOGO])
    records = [{"n": "1"}, {"n": "2"}, {"n": "3"}]
    modules = ImageModules(lose={0, 1}, silent={3, 4, 5, 6})
    printer = ModulePrinter(modules)
    renders = render_batch(label, records, PLUG_IN_TWO_WAY, held=READ_EMPTY)
    job = CheckedJob(renders=record_renders(label, records, PLUG_IN_TWO_WAY), check=_check(), held=READ_EMPTY)
    first = renders.results[0]
    settings = first.data[: first.data.index(STX + b"I")]

    report = print_checked(renders, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert isinstance(report.result, BatchResult)
    second, third = report.result.results[1:]
    assert (second.downloads, third.downloads) == ((), ())
    *_, round_3 = (f.message for f in second.findings if f.code is FindingCode.IMAGE_UPLOAD_RETRIED)
    assert round_3 == (
        "round 3: sent no clear (module B may not be cleared), then sent nothing of the images halved 2x2: "
        "the module holds each one already, so no read was made"
    )
    # Record 1's three uploads; record 2's round-1 upload and its whole job, record 3's whole job.
    assert printer.data.count(settings) == 6
    # Record 1's three reads; record 2's round 1 and the names it read again in round 2; record 3's
    # read before its round 1 and the one in its round 2.
    assert modules.requests == 7
    assert modules.downloaded_held == []
    assert modules.placed_missing == []


def _refuse_third(third: RenderOptions, modules: ImageModules) -> tuple[ModulePrinter, BatchResult]:
    """Print LOGO for two records on the plug-in module, then once rendered with ``third``; expect a refusal."""
    label = Label(elements=[LOGO])
    printer = ModulePrinter(modules)
    renders = (*render_batch(label, [{"n": "1"}, {"n": "2"}], PLUG_IN_TWO_WAY, held=READ_EMPTY).results,)
    renders = (*renders, render_label(label, third, held=READ_EMPTY))
    batch = BatchResult(data=b"".join(r.data for r in renders), results=renders)
    job = CheckedJob(
        renders=(
            *record_renders(label, [{"n": "1"}, {"n": "2"}], PLUG_IN_TWO_WAY),
            *record_renders(label, None, third),
        ),
        check=_check(),
        held=READ_EMPTY,
    )

    with pytest.raises(UploadRefusedError) as caught:
        print_checked(batch, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    sent = caught.value.result
    assert isinstance(sent, BatchResult)
    return printer, sent


@pytest.mark.os_agnostic
def test_a_record_whose_every_round_is_left_out_is_refused_saying_no_round_ran() -> None:
    """As above (the names read before record 3's round 1, and in its round 2, get no answer), but record 3 is strict.

    Its images cannot be halved either: nothing of it is sent.
    """
    [logo] = render_label(Label(elements=[LOGO]), PLUG_IN_TWO_WAY).downloads
    strict = PLUG_IN_TWO_WAY.model_copy(update={"policy": FallbackPolicy.STRICT})
    modules = ImageModules(lose={0}, silent={0, 1, 3, 4, 5, 6})

    printer, sent = _refuse_third(strict, modules)

    assert modules.downloaded_held == []
    assert modules.downloads.count(logo) == 2
    assert printer.data.count(FORMAT) == 2
    message = _message(sent.results[2], FindingCode.IMAGE_UPLOAD_REFUSED)
    assert message.startswith(f"images {logo} not sent: no round ran: round 1 skipped, it would download again {logo}:")
    assert "round 2 skipped," in message
    assert "halving is refused" in message


@pytest.mark.os_agnostic
def test_a_record_in_error_once_rendered_from_the_names_read_first_is_refused_with_nothing_of_it_sent() -> None:
    """Record 1 sends LOGO full size, its reads get no answer, and it places the halved LOGO module B holds.

    Record 2 downloads LOGO and ICON, held names in error. The names it reads before its round 1
    list the LOGO record 1 sent, and ICON, which another program stored: it is refused there.
    """
    first, second = Label(elements=[LOGO]), Label(elements=[LOGO, ICON])
    [halved] = build_record(first, PLUG_IN_TWO_WAY, halve=True).result.downloads
    logo, icon = render_label(second, PLUG_IN_TWO_WAY).downloads
    held_in_error = PLUG_IN_TWO_WAY.model_copy(update={"held_image_severity": Severity.ERROR})
    held = HeldImages(listed=frozenset({halved}))
    modules = ImageModules(held=[("B", halved, 128), ("B", icon, 512)], silent={0, 1})
    printer = ModulePrinter(modules)
    one = build_record(first, PLUG_IN_TWO_WAY, held=held)
    two = build_record(second, held_in_error, held=one.held_after).result
    assert two.ok
    batch = BatchResult(data=one.result.data + two.data, results=(one.result, two))
    job = CheckedJob(
        renders=(*record_renders(first, None, PLUG_IN_TWO_WAY), *record_renders(second, None, held_in_error)),
        check=_check(),
        held=held,
    )

    with pytest.raises(UploadRefusedError) as caught:
        print_checked(batch, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert modules.downloads == [logo]
    assert modules.downloaded_held == []
    assert printer.data.count(FORMAT) == 1
    assert str(caught.value) == (
        "a label, rendered again during its upload check for what the printer then held, has errors; "
        "its format was not sent, nor anything after it"
    )
    sent = caught.value.result
    assert isinstance(sent, BatchResult)
    assert _message(sent.results[1], FindingCode.IMAGE_UPLOAD_RENDER_REFUSED) == (
        "the label, rendered again from the names read before round 1, has errors (image ICON (sent as "
        f"{icon}) is not downloaded: the printer already holds that name in module B, and a second download of "
        "it prints nothing; the label prints the stored image); nothing of it was sent"
    )
    assert [f.code for f in caught.value.findings] == [FindingCode.IMAGE_UPLOAD_RENDER_REFUSED]


@pytest.mark.os_agnostic
def test_a_batch_whose_names_read_got_no_answer_renders_the_next_record_from_the_first_records_read() -> None:
    """The names read before the job got no answer; record 1's read lists ICON, which record 2 then places."""
    options = TWO_WAY.model_copy(update={"clear_graphics_before_label": False, "clear_graphics_after_label": False})
    first, second = Label(elements=[LOGO]), Label(elements=[ICON])
    renders = build_jobs([first, second], options, held=HeldImages())
    [icon] = renders[1].downloads
    modules = ImageModules(held=[("A", icon, 512)])
    job = CheckedJob(
        renders=(*record_renders(first, None, options), *record_renders(second, None, options)),
        check=_check(),
        held=HeldImages(),
    )

    sent = _print_batch(modules, renders, job)

    assert modules.downloaded_held == []
    assert sent.results[1].downloads == ()
    _assert_each_format_places_a_stored_image(sent, modules, "A")


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "options",
    [
        RenderOptions(model="allegro-2", clear_graphics_before_label=False, model_image_budget=False),
        RenderOptions(
            model="prodigy", image_module=ImageModule.B, clear_graphics_before_label=False, model_image_budget=False
        ),
    ],
    ids=["clear-module", "prodigy-remove"],
)
def test_the_clear_after_a_record_empties_what_its_read_listed_so_the_next_record_downloads_again(
    options: RenderOptions,
) -> None:
    """Record 1's read lists LOGO, then its clear after takes LOGO out: record 2 must download it again."""
    label = Label(elements=[LOGO])
    records = [{"n": "1"}, {"n": "2"}]
    [logo] = render_label(label, options).downloads
    modules = ImageModules()
    renders = render_batch(label, records, options, held=READ_EMPTY).results
    job = CheckedJob(renders=record_renders(label, records, options), check=_check(), held=READ_EMPTY)

    sent = _print_batch(modules, renders, job)

    assert [record.downloads for record in sent.results] == [(logo,), (logo,)]
    assert modules.downloaded_held == []


#: Two bitmaps whose content names collide (from test_held_images: a brute-force search of 24x1 dots).
_COLLIDING = (["....###.####...#..###..."], ["..#.###.###.##....#....#"])


@pytest.mark.os_agnostic
def test_a_name_an_earlier_round_sent_keeps_its_picture_so_another_picture_under_it_is_salted() -> None:
    """Round 1 of record 1 sends the first bitmap's name before a read with no answer, and the names
    read again get none either; round 3 passes.

    Record 2's other bitmap has that same content name: the printer holds the first picture under
    it, so record 2 must download its own under the next salt, not place the listed name.
    """
    first = Label(elements=[Image(x=1, y=1, name="C0", bitmap=_COLLIDING[0]), LOGO])
    second = Label(elements=[Image(x=1, y=1, name="C1", bitmap=_COLLIDING[1])])
    collided = drawn_image_name(Bitmap.from_rows(_COLLIDING[0]))
    assert drawn_image_name(Bitmap.from_rows(_COLLIDING[1])) == collided
    salted = drawn_image_name(Bitmap.from_rows(_COLLIDING[1]), salt=1)
    modules = ImageModules(silent={0, 1})
    renders = build_jobs([first, second], PLUG_IN_TWO_WAY, held=READ_EMPTY)
    job = CheckedJob(
        renders=(*record_renders(first, None, PLUG_IN_TWO_WAY), *record_renders(second, None, PLUG_IN_TWO_WAY)),
        check=_check(),
        held=READ_EMPTY,
    )

    sent = _print_batch(modules, renders, job)

    assert sent.results[0].halved_elements == (0, 1)
    assert collided in modules.names("B")
    assert sent.results[1].downloads == (salted,)
    rest = sent.results[1].data[sent.results[1].upload_end :]
    assert salted.encode("ascii") in rest
    assert collided.encode("ascii") not in rest
    assert modules.downloaded_held == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize("silent", [{0, 1}, {0, 1, 2}], ids=["read-first", "read-again-in-round-2"])
def test_a_name_the_record_before_left_maybe_held_keeps_its_picture_when_a_later_read_lists_it(
    silent: set[int],
) -> None:
    """Module B holds record 1's halved names; its round-1 read and the names read again get no answer.

    So it places the halved names, and its full-size first bitmap may have arrived under the content
    name record 2's other bitmap has too. The names record 2 reads before its round 1 (or, when
    those get no answer, again in its round 2) list that name: it holds record 1's picture, so
    record 2 must download its own under the next salt, not place the listed name.
    """
    keys_off = PLUG_IN_TWO_WAY.model_copy(
        update={"clear_graphics_before_label": False, "clear_graphics_after_label": False}
    )
    first = Label(elements=[Image(x=1, y=1, name="C0", bitmap=_COLLIDING[0]), LOGO])
    second = Label(elements=[Image(x=1, y=1, name="C1", bitmap=_COLLIDING[1])])
    collided = drawn_image_name(Bitmap.from_rows(_COLLIDING[0]))
    salted = drawn_image_name(Bitmap.from_rows(_COLLIDING[1]), salt=1)
    halved = build_record(first, keys_off, halve=True).result.downloads
    held = HeldImages(listed=frozenset(halved))
    modules = ImageModules(held=[("B", name, 10) for name in halved], silent=silent)
    renders = build_jobs([first, second], keys_off, held=held)
    job = CheckedJob(
        renders=(*record_renders(first, None, keys_off), *record_renders(second, None, keys_off)),
        check=_check(),
        held=held,
    )

    sent = _print_batch(modules, renders, job)

    assert sent.results[0].halved_elements == (0, 1)
    assert collided in modules.names("B")
    assert sent.results[1].downloads == (salted,)
    rest = sent.results[1].data[sent.results[1].upload_end :]
    assert salted.encode("ascii") in rest
    assert collided.encode("ascii") not in rest
    assert modules.downloaded_held == []
    assert modules.placed_missing == []


#: A second 64 x 64 picture, as many dots as LOGO: two of them leave room for one more and a halved pair.
SECOND = Image(
    x=1,
    y=100,
    name="SECOND",
    bitmap=["".join("#" if (row * 7 + column * 11) % 5 == 0 else "." for column in range(64)) for row in range(64)],
)
#: The plain Prodigy into module B with the clear keys off: its retry clear removes the label's own names.
PRODIGY_NO_KEYS = RenderOptions(
    model="prodigy",
    image_module=ImageModule.B,
    clear_graphics_before_label=False,
    clear_graphics_after_label=False,
    model_image_budget=False,
)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("lose", "silent"), [((), (5,)), ((0,), (1, 5))], ids=["round-3-unread", "round-2-unread"])
def test_the_prodigys_removes_after_the_last_answered_read_are_applied_to_what_the_next_record_takes_as_held(
    lose: tuple[int, ...], silent: tuple[int, ...]
) -> None:
    """Record 2's round 3 removes its full-size names after the read that listed them, then passes unread.

    Its halved names are the ones record 1 left, so round 3 downloads nothing. Record 3 must not
    take the removed full-size name as held: a format placing it would print without the image.
    """
    label = Label(elements=[LOGO, SECOND])
    records = [{"n": "1"}, {"n": "2"}, {"n": "3"}]
    other = HeldImages(listed=frozenset({"OTHER"}))
    modules = ImageModules(capacity=1200, held=[("B", "OTHER", 300)], lose=lose, silent=silent)
    printer = ModulePrinter(modules)
    renders = render_batch(label, records, PRODIGY_NO_KEYS, held=other)
    job = CheckedJob(renders=record_renders(label, records, PRODIGY_NO_KEYS), check=_check(), held=other)

    report = print_checked(renders, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert modules.placed_missing == []
    assert modules.downloaded_held == []
    assert printer.data.count(FORMAT) == len(records)
    assert isinstance(report.result, BatchResult)
    assert all(record.ok for record in report.result.results)


class _CutBeforeModuleB(ImageModules):
    """Answers the first directory request with module A's block only, as if the answer ended there."""

    def _directory(self) -> bytes:
        whole = super()._directory()
        return whole.split(b"MODULE: B")[0] if self.requests == 1 else whole


@pytest.mark.os_agnostic
def test_a_directory_answer_that_leaves_out_the_job_s_module_is_no_answer_and_no_name_goes_twice() -> None:
    """Read as module B empty, the answer would make round 2 download LOGO, which B holds, again.

    Round 2 reads the names again instead, which list LOGO: the label places it.
    """
    label = Label(elements=[LOGO])
    [logo] = render_label(label, PLUG_IN_TWO_WAY).downloads
    modules = _CutBeforeModuleB(held=[("A", "OTHER", 10)])
    printer = ModulePrinter(modules)
    result = render_label(label, PLUG_IN_TWO_WAY, held=READ_EMPTY)
    job = CheckedJob(renders=record_renders(label, None, PLUG_IN_TWO_WAY), check=_check(), held=READ_EMPTY)

    report = print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert modules.downloaded_held == []
    assert modules.downloads.count(logo) == 1
    assert printer.data.count(FORMAT) == 1
    assert _message(_single(report.result), FindingCode.IMAGE_UPLOAD_RETRIED).startswith(
        "round 2: sent no clear (module B may not be cleared), read module B again first as the read before it gave "
        "no listing (module B then listed every image)"
    )


@pytest.mark.os_agnostic
def test_the_plain_prodigy_removes_each_of_the_labels_own_names_instead_of_a_clear() -> None:
    options = RenderOptions(model="prodigy", image_module=ImageModule.A)
    [full] = render_label(Label(elements=[LOGO]), options).downloads

    printer, refused = _refused(ImageModules(capacity=1), Label(elements=[LOGO]), options)

    assert STX + b"q" not in printer.data
    [halved] = _single(refused.result).downloads
    removes = [chunk for chunk in printer.sent if chunk.startswith(STX + b"R")]
    # Before round 2 and round 3 the full-size name goes; after the refusal the halved one.
    assert removes == [STX + b"R" + full.encode("ascii") + b"\r"] * 2 + [STX + b"R" + halved.encode("ascii") + b"\r"]


@pytest.mark.os_agnostic
def test_the_plain_prodigys_halving_round_places_a_halved_name_its_last_read_listed() -> None:
    """The names read before the job got no answer; module B holds LOGO's halved name from an earlier job.

    Rounds 1 and 2 lose the full-size download, and both reads list the halved name. The Prodigy's
    removes take out only the label's own full-size name, so round 3 must place the halved name the
    module holds, not download it again with no clear before it (which prints nothing).
    """
    label = Label(elements=[LOGO])
    [halved] = build_record(label, PRODIGY_NO_KEYS, halve=True).result.downloads
    modules = ImageModules(held=[("B", halved, 128)], lose={0, 1})
    printer = ModulePrinter(modules)
    result = render_label(label, PRODIGY_NO_KEYS, held=HeldImages())
    job = CheckedJob(renders=record_renders(label, None, PRODIGY_NO_KEYS), check=_check(), held=HeldImages())

    report = print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert modules.downloaded_held == []
    assert modules.placed_missing == []
    sent = _single(report.result)
    assert (sent.halved_elements, sent.downloads) == ((0,), ())
    # Round 3 downloads nothing, so the label's whole job follows its removes.
    *_, rest = printer.sent
    assert rest.count(FORMAT) == 1 and halved.encode("ascii") in rest
    assert printer.queries == [WG, WG]


@pytest.mark.os_agnostic
def test_a_prodigy_label_left_with_nothing_to_download_still_counts_as_one_a_clear_may_go_to() -> None:
    """Record 1's round 3 places the halved LOGO module B holds, after its round-2 read got no answer.

    Rendered so, it downloads nothing, so it has no name to remove; a clear still may go to that
    module, so it leaves the next record no name that may be held, and the next record makes no
    read before its round 1.
    """
    label = Label(elements=[LOGO])
    after_only = PRODIGY_NO_KEYS.model_copy(update={"clear_graphics_after_label": True})
    [halved] = build_record(label, after_only, halve=True).result.downloads
    held = HeldImages(listed=frozenset({halved}))
    records = [{"n": "1"}, {"n": "2"}]
    modules = ImageModules(held=[("B", halved, 128)], lose={0, 1}, silent={1})
    renders = render_batch(label, records, after_only, held=held).results
    job = CheckedJob(renders=record_renders(label, records, after_only), check=_check(), held=held)

    sent = _print_batch(modules, renders, job)

    assert modules.downloaded_held == []
    assert modules.placed_missing == []
    assert [record.halved_elements for record in sent.results] == [(0,), ()]
    # Record 1's two reads, record 2's one after its round 1.
    assert modules.requests == 3


@pytest.mark.os_agnostic
def test_the_plain_prodigys_halving_round_downloads_again_a_qr_code_its_removes_took_out() -> None:
    """Rounds 1 and 2 lose LOGO and their reads list the QR Code, which the removes before round 3 take out.

    A QR Code keeps its name when halved: round 3 must download it again, not place the name the
    last read listed, which the module no longer holds.
    """
    label = Label(elements=[Qr(x=5, y=150, data="CODE"), LOGO])
    qr, logo = render_label(label, PRODIGY_NO_KEYS).downloads
    modules = ImageModules(lose={1, 3})
    printer = ModulePrinter(modules)
    result = render_label(label, PRODIGY_NO_KEYS, held=READ_EMPTY)
    job = CheckedJob(renders=record_renders(label, None, PRODIGY_NO_KEYS), check=_check(), held=READ_EMPTY)

    report = print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert modules.placed_missing == []
    assert modules.downloaded_held == []
    assert modules.downloads.count(qr) == 3
    assert modules.downloads.count(logo) == 2
    sent = _single(report.result)
    assert sent.halved_elements == (1,)
    assert qr in sent.downloads


@pytest.mark.os_agnostic
def test_a_label_of_only_qr_codes_skips_the_halving_round_and_is_refused_after_two() -> None:
    _, refused = _refused(ImageModules(capacity=1), Label(elements=[Qr(x=5, y=5, data="CODE")]))

    message = _message(_single(refused.result), FindingCode.IMAGE_UPLOAD_REFUSED)
    assert "after 2 rounds" in message
    assert "no image of the label can be halved" in message


@pytest.mark.os_agnostic
def test_under_strict_the_halving_round_is_refused_like_every_fallback() -> None:
    options = RenderOptions(model="allegro-2", policy=FallbackPolicy.STRICT)

    printer, refused = _refused(ImageModules(capacity=1), Label(elements=[LOGO]), options)

    assert printer.queries == [WG, WG]
    message = _message(_single(refused.result), FindingCode.IMAGE_UPLOAD_REFUSED)
    assert "after 2 rounds" in message
    assert "halving is refused" in message


#: No clears, and room in a configured budget for one LOGO-sized download and not two, under
#: strict: rendered from an empty module a label of two images is in error.
_ONE_IMAGE_BUDGET = RenderOptions(
    model="allegro-2",
    clear_graphics_before_label=False,
    clear_graphics_after_label=False,
    image_memory_bytes=700,
    policy=FallbackPolicy.STRICT,
)


@pytest.mark.os_agnostic
def test_a_label_in_error_once_rendered_again_for_round_2_is_refused_and_its_format_never_sent() -> None:
    label = Label(elements=[LOGO, ICON])
    logo, icon = render_label(label, _ONE_IMAGE_BUDGET).downloads
    held = HeldImages(listed=frozenset({logo}))
    result = render_label(label, _ONE_IMAGE_BUDGET, held=held)
    assert result.ok and len(result.downloads) == 1
    printer = ModulePrinter(ImageModules(held=[("A", logo, 512)], lose={0}))
    job = CheckedJob(renders=record_renders(label, None, _ONE_IMAGE_BUDGET), check=_check(), held=held)

    with pytest.raises(UploadRefusedError) as caught:
        print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert FORMAT not in printer.data
    assert printer.queries == [WG]
    assert printer.sent.count(STX + b"qA") == 1
    assert printer.sent[-1] == STX + b"qA"
    assert str(caught.value) == (
        "a label, rendered again during its upload check for what the printer then held, has errors; "
        "its format was not sent, nor anything after it"
    )
    assert _message(_single(caught.value.result), FindingCode.IMAGE_UPLOAD_RENDER_REFUSED) == (
        "the label, rendered again for round 2, has errors (the job's image downloads hold 1024 bytes; the "
        "configured image memory budget is 700 bytes); its format was not sent (the read after its upload: "
        f"module A did not list {icon})"
    )
    assert [f.code for f in caught.value.findings] == [FindingCode.IMAGE_UPLOAD_RENDER_REFUSED]


@pytest.mark.os_agnostic
def test_a_batch_record_in_error_once_rendered_again_says_so_and_nothing_after_it_is_sent() -> None:
    first, second = Label(elements=[Qr(x=5, y=150, data="FIRST")]), Label(elements=[LOGO, ICON])
    logo, _icon = render_label(second, _ONE_IMAGE_BUDGET).downloads
    held = HeldImages(listed=frozenset({logo}))
    first_record = build_record(first, _ONE_IMAGE_BUDGET, held=held)
    one = first_record.result
    two = build_record(second, _ONE_IMAGE_BUDGET, held=first_record.held_after).result
    assert one.ok and two.ok
    batch = BatchResult(data=one.data + two.data, results=(one, two))

    def render_first(held: HeldImages, *, halve: bool) -> RenderedRecord:
        return build_record(first, _ONE_IMAGE_BUDGET, held=held, halve=halve)

    def render_second(held: HeldImages, *, halve: bool) -> RenderedRecord:
        return build_record(second, _ONE_IMAGE_BUDGET, held=held, halve=halve)

    printer = ModulePrinter(ImageModules(held=[("A", logo, 512)], lose={0}))
    job = CheckedJob(renders=(render_first, render_second), check=_check(), held=held)

    with pytest.raises(UploadRefusedError) as caught:
        print_checked(batch, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert printer.data.count(FORMAT) == 1
    assert "rendered again" in str(caught.value)
    assert "did not store" not in str(caught.value)
    assert FindingCode.IMAGE_MEMORY_EXCEEDED in [f.code for f in caught.value.findings]


@pytest.mark.os_agnostic
def test_a_job_with_an_error_finding_is_refused_before_anything_is_opened() -> None:
    printer = ModulePrinter(ImageModules())
    result, job = _job(Label(elements=[LOGO]), RenderOptions(model="prodigy"), None, 3)
    assert not result.ok

    with pytest.raises(LabelRejectedError):
        print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert printer.opened == []


class _HangingUpOnTheRead(ModulePrinter):
    """A print server that takes the downloads and closes the connection before the directory request."""

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = 120.0) -> bytes:
        self.closed = True
        raise TransportError("module printer: connection reset", unanswered=True)


class _HangingUpAfterTheAnswer(ModulePrinter):
    """A print server that answers the directory request and then closes the connection."""

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = 120.0) -> bytes:
        answer = super().read_answer(request, timeout=timeout, idle=idle, total=total)
        self.hung_up = True
        return answer


@pytest.mark.os_agnostic
def test_a_channel_the_print_server_closed_after_the_answer_gets_no_format_and_a_new_one_does() -> None:
    modules = ImageModules()
    first, second = _HangingUpAfterTheAnswer(modules), ModulePrinter(modules)
    channels = iter([first, second])

    def open_printer(uri: str, *, timeout: float) -> PrinterTransport:
        return next(channels)

    result, job = _job(Label(elements=[LOGO]), ALLEGRO, None, 3)
    report = print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_printer)

    assert FORMAT not in first.data
    assert second.sent[0].startswith(FORMAT)
    assert report.bytes_sent == len(result.data)


@pytest.mark.os_agnostic
def test_a_read_whose_connection_was_closed_goes_on_a_new_one_and_the_format_follows_there() -> None:
    modules = ImageModules()
    first, second = _HangingUpOnTheRead(modules), ModulePrinter(modules)
    channels = iter([first, second])

    def open_printer(uri: str, *, timeout: float) -> PrinterTransport:
        # A connect within the read's wait gets what is left of it, never more than the timeout.
        assert uri == URI and 0 < timeout <= 5.0
        return next(channels)

    result, job = _job(Label(elements=[LOGO]), ALLEGRO, None, 3)
    print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_printer)

    assert FORMAT not in first.data
    assert second.queries == [WG]
    assert second.data.count(FORMAT) == 1
    assert second.closed


class _RefusingFor:
    """Opens ``first``, then refuses every connect for ``seconds`` by ``clock``, then opens a new printer.

    A print server whose read got no answer refused the next connection for 7 s and more (one
    Allegro 2 behind its print server, 2026-11-01).
    """

    def __init__(self, first: ModulePrinter, *, seconds: float, clock: Callable[[], float]) -> None:
        self.first, self.seconds, self.clock = first, seconds, clock
        self.refused = 0
        self.channels: list[ModulePrinter] = []
        self._until: float | None = None

    def __call__(self, uri: str, *, timeout: float) -> PrinterTransport:
        if not self.channels:
            self.channels.append(self.first)
            return self.first
        if self._until is None:
            self._until = self.clock() + self.seconds
        if self.clock() < self._until:
            self.refused += 1
            raise TransportError(f"cannot connect to {uri}: Connection refused", unanswered=True)
        self.channels.append(ModulePrinter(self.first.modules))
        return self.channels[-1]


def _fake_time() -> tuple[Callable[[], float], Callable[[float], None]]:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    return (lambda: now[0]), sleep


@pytest.mark.os_agnostic
def test_a_connect_refused_after_a_read_with_no_answer_is_tried_again_within_the_wait() -> None:
    clock, sleep = _fake_time()
    opener = _RefusingFor(ModulePrinter(ImageModules(answers=False)), seconds=7.5, clock=clock)
    wait = AnswerWait(seconds=30.0, retry_interval=0.25, clock=clock, sleep=sleep)
    result, job = _job(Label(elements=[LOGO]), ALLEGRO, None, 3)
    job = job._replace(check=UploadCheck(wait=wait, rounds=3))

    with pytest.raises(UploadRefusedError):
        print_checked(result, job, uri=URI, timeout=5.0, open_printer=opener)

    assert opener.refused >= 7.5 / 0.25
    assert opener.first.modules.requests == 3
    assert opener.channels[-1].sent[-1] == STX + b"qA"


@pytest.mark.os_agnostic
def test_a_connect_refused_for_longer_than_the_wait_ends_the_job_with_the_wait_named() -> None:
    clock, sleep = _fake_time()
    opener = _RefusingFor(ModulePrinter(ImageModules(answers=False)), seconds=60.0, clock=clock)
    wait = AnswerWait(seconds=2.0, retry_interval=0.25, wait_longer="wait longer", clock=clock, sleep=sleep)
    result, job = _job(Label(elements=[LOGO]), ALLEGRO, None, 3)
    job = job._replace(check=UploadCheck(wait=wait, rounds=3))

    with pytest.raises(PrinterTimeoutError, match="wait longer"):
        print_checked(result, job, uri=URI, timeout=5.0, open_printer=opener)

    assert opener.refused > 1


@pytest.mark.os_agnostic
def test_verify_refused_by_the_upload_check_sends_no_format_and_takes_no_photo(tmp_path: Path) -> None:
    label = Label(elements=[LOGO])
    printer = ModulePrinter(ImageModules(capacity=1), answers={STATUS_REQUEST: b"NNNNNNNN\r"})
    photos: list[Path] = []

    def capture(settings: CameraSettings, *, output_dir: Path) -> CapturedImage:
        photos.append(output_dir)
        return CapturedImage(path=output_dir, media_type=MediaType.PNG, captured_at=datetime.now(tz=timezone.utc))

    result, job = _job(label, ALLEGRO, None, 3)
    with pytest.raises(UploadRefusedError):
        verify_print(
            _single(result),
            label,
            get_profile("allegro-2"),
            options=ALLEGRO,
            uri=URI,
            timeout=5.0,
            camera=CameraSettings(kind=CameraKind.FILE),
            output_dir=tmp_path,
            open_printer=open_recording_printer(printer),
            capture=capture,
            sleep=lambda _seconds: None,
            clock=lambda: 0.0,
            checked=job,
        )

    assert FORMAT not in printer.data
    assert STATUS_REQUEST not in printer.data
    assert photos == []


@pytest.mark.os_agnostic
def test_a_one_way_channel_reads_nothing_and_gets_the_whole_job_in_one_send() -> None:
    printer = RecordingPrinter(bidirectional=False)
    result, job = _job(Label(elements=[LOGO]), ALLEGRO, None, 3)

    report = print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert printer.sent == [result.data]
    assert printer.queries == []
    assert report.result is result


def _qr_batch(**options: Any) -> tuple[Label, list[dict[str, str]], RenderOptions]:
    label = Label(elements=[Qr(x=5, y=5, data="{code}")])
    return (
        label,
        [{"code": "FIRST"}, {"code": "SECOND"}, {"code": "FIRST"}],
        RenderOptions(model="allegro-2", **options),
    )


@pytest.mark.os_agnostic
def test_a_batch_checks_each_record_and_retries_only_the_record_whose_download_was_lost() -> None:
    label, records, options = _qr_batch()

    printer, sent = _print(ImageModules(lose={1}), label, options, records=records)

    assert isinstance(sent, BatchResult)
    assert [_upload_codes(result) for result in sent.results] == [
        [],
        [(FindingCode.IMAGE_UPLOAD_RETRIED, Severity.INFO)],
        [],
    ]
    assert printer.queries == [WG, WG, WG, WG]
    assert printer.data.count(FORMAT) == 3


@pytest.mark.os_agnostic
def test_with_no_clear_a_later_record_leaves_out_the_picture_an_earlier_record_stored() -> None:
    """Record 3 places FIRST, which record 1 stored: a second download of a held name prints nothing."""
    label, records, options = _qr_batch(clear_graphics_before_label=False, clear_graphics_after_label=False)
    modules = ImageModules()

    printer, sent = _print(modules, label, options, records=records)

    assert isinstance(sent, BatchResult)
    assert [len(result.downloads) for result in sent.results] == [1, 1, 0]
    assert modules.downloads == [*sent.results[0].downloads, *sent.results[1].downloads]
    # Record 3 downloads nothing, so it is sent whole with no read.
    assert printer.queries == [WG, WG]


@pytest.mark.os_agnostic
def test_a_record_cleared_by_its_retry_makes_the_next_record_download_what_the_clear_removed() -> None:
    """With no clear keys, record 3 would leave out FIRST as held from record 1; record 2's clear removed it."""
    label, records, options = _qr_batch(clear_graphics_before_label=False, clear_graphics_after_label=False)
    first = render_batch(label, records, options)
    assert len(first.results[2].downloads) == 0
    modules = ImageModules(lose={1})

    _, sent = _print(modules, label, options, records=records)

    assert isinstance(sent, BatchResult)
    assert sent.results[2].downloads == first.results[0].downloads
    assert modules.names("A") == [*sent.results[1].downloads, *sent.results[2].downloads]


@pytest.mark.os_agnostic
def test_a_refused_record_ends_the_batch_and_the_records_after_it_are_not_sent() -> None:
    label, records, options = _qr_batch()
    printer = ModulePrinter(ImageModules(lose={1, 2, 3}))
    result, job = _job(label, options, records, 2)

    with pytest.raises(UploadRefusedError) as caught:
        print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert printer.data.count(FORMAT) == 1
    sent = caught.value.result
    assert isinstance(sent, BatchResult)
    assert [result.ok for result in sent.results] == [True, False, True]


@pytest.mark.os_agnostic
def test_verify_checks_the_upload_and_draws_the_expected_picture_from_the_halved_label(tmp_path: Path) -> None:
    label = Label(elements=[LOGO])
    printer = ModulePrinter(ImageModules(capacity=HALF_ROOM), answers={STATUS_REQUEST: b"NNNNNNNN\r"})
    photo = tmp_path / "photo.png"

    def capture(settings: CameraSettings, *, output_dir: Path) -> CapturedImage:
        assert (settings.kind, output_dir) == (CameraKind.FILE, tmp_path)
        return CapturedImage(path=photo, media_type=MediaType.PNG, captured_at=datetime.now(tz=timezone.utc))

    result, job = _job(label, ALLEGRO, None, 3)
    report = verify_print(
        _single(result),
        label,
        get_profile("allegro-2"),
        options=ALLEGRO,
        uri=URI,
        timeout=5.0,
        camera=CameraSettings(kind=CameraKind.FILE),
        output_dir=tmp_path,
        open_printer=open_recording_printer(printer),
        capture=capture,
        sleep=lambda _seconds: None,
        clock=lambda: 0.0,
        checked=job,
    )

    assert report.result is not None
    assert report.result.halved_elements == (0,)
    assert printer.data.count(FORMAT) == 1
    assert printer.queries[-1] == STATUS_REQUEST


# ======================== the model's image budget on a two-way print ========================

#: 600 x 600 dots: 45000 bytes of dot data, past the Allegro 2's 44535-byte default budget and
#: within the fake module's 49088 bytes.
_BIG_PATTERN = ["".join("#" if (row * 3 + col * 11) % 7 == 0 else "." for col in range(600)) for row in range(600)]
BIG = Image(x=1, y=1, name="BIG", bitmap=_BIG_PATTERN)
TWO_WAY = RenderOptions(model="allegro-2", model_image_budget=False)


def _codes(result: RenderResult) -> list[FindingCode]:
    return [f.code for f in result.findings]


@pytest.mark.os_agnostic
def test_a_two_way_print_sends_images_past_the_model_budget_at_full_size_and_says_why() -> None:
    label = Label(elements=[BIG])
    offline = render_label(label, ALLEGRO)
    assert offline.halved_elements == (0,)

    printer, sent = _print(ImageModules(), label, TWO_WAY)

    result = _single(sent)
    assert result.halved_elements == ()
    assert result.image_bytes == 45000
    assert printer.data.count(FORMAT) == 1
    assert FindingCode.IMAGE_HALVED not in _codes(result)
    [skipped] = [f for f in result.findings if f.code is FindingCode.IMAGE_BUDGET_SKIPPED]
    assert skipped.severity is Severity.INFO
    assert "45000 bytes" in skipped.message
    assert "allegro-2 default image memory budget is 44535 bytes" in skipped.message
    assert "upload check" in skipped.message


@pytest.mark.os_agnostic
def test_a_two_way_print_within_the_model_budget_says_nothing_about_it() -> None:
    _, sent = _print(ImageModules(), Label(elements=[LOGO]), TWO_WAY)

    assert FindingCode.IMAGE_BUDGET_SKIPPED not in _codes(_single(sent))


@pytest.mark.os_agnostic
def test_a_configured_image_memory_bytes_halves_a_two_way_print_as_it_does_offline() -> None:
    options = RenderOptions(model="allegro-2", model_image_budget=False, image_memory_bytes=40000)

    _, sent = _print(ImageModules(), Label(elements=[BIG]), options)

    result = _single(sent)
    assert result.halved_elements == (0,)
    assert "configured image memory budget is 40000 bytes" in _message(result, FindingCode.IMAGE_HALVED)
    assert FindingCode.IMAGE_BUDGET_SKIPPED not in _codes(result)


@pytest.mark.os_agnostic
def test_a_job_the_model_budget_is_left_out_of_still_halves_in_round_3_when_the_module_is_full() -> None:
    _, sent = _print(ImageModules(capacity=20000), Label(elements=[BIG]), TWO_WAY)

    result = _single(sent)
    assert result.halved_elements == (0,)
    assert "did not store the job's images at full size" in _message(result, FindingCode.IMAGE_HALVED)


# ======================== a module no directory lists ========================


@pytest.mark.os_agnostic
def test_a_two_way_print_to_module_c_is_refused_at_once_with_nothing_sent_and_no_read() -> None:
    options = RenderOptions(model="allegro-2", image_module=ImageModule.C)
    assert render_label(Label(elements=[LOGO]), options).ok

    printer, refused = _refused(ImageModules(), Label(elements=[LOGO]), options)

    assert printer.sent == []
    assert printer.queries == []
    assert refused.bytes_sent == 0
    assert "no directory lists module C" in str(refused)
    result = _single(refused.result)
    [error] = result.errors
    assert error.code is FindingCode.IMAGE_MODULE_UNLISTED
    assert "image_module C" in error.message
    assert "module A" in error.message
    assert "one-way link" in error.message
    assert FindingCode.IMAGE_UPLOAD_RETRIED not in _codes(result)


@pytest.mark.os_agnostic
def test_a_two_way_batch_to_module_c_sends_not_even_the_records_before_the_first_download() -> None:
    label = Label(elements=[Qr(x=5, y=5, data="{code}")])
    options = RenderOptions(
        model="allegro-2",
        image_module=ImageModule.C,
        clear_graphics_before_label=False,
        clear_graphics_after_label=False,
    )
    records = [{"code": "ONE"}, {"code": "TWO"}]
    [first] = render_label(Label(elements=[Qr(x=5, y=5, data="ONE")]), options).downloads
    held = HeldImages(listed=frozenset({first}))
    result = render_batch(label, records, options, held=held)
    assert [len(r.downloads) for r in result.results] == [0, 1]
    job = CheckedJob(renders=record_renders(label, records, options), check=_check(), held=held)
    printer = ModulePrinter(ImageModules())

    with pytest.raises(UploadRefusedError) as caught:
        print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert printer.sent == []
    assert caught.value.bytes_sent == 0
    assert [f.code for f in caught.value.findings] == [FindingCode.IMAGE_MODULE_UNLISTED]


@pytest.mark.os_agnostic
def test_a_one_way_channel_sends_a_module_c_job_whole_as_before() -> None:
    options = RenderOptions(model="allegro-2", image_module=ImageModule.C)
    printer = RecordingPrinter(bidirectional=False)
    result, job = _job(Label(elements=[LOGO]), options, None, 3)

    print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert printer.sent == [result.data]


# ======================== the CLI over a real channel ========================


def _label_file(tmp_path: Path) -> str:
    path = tmp_path / "label.json"
    path.write_text(json.dumps({"elements": [LOGO.model_dump(mode="json")]}), encoding="utf-8")
    return str(path)


def _run(runner: CliRunner, args: list[str], **env: str) -> Any:
    return runner.invoke(cli_mod.cli, args, obj=build_production, env=env)


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_over_tcp_uploads_again_after_a_lost_download_and_reports_the_round(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[[Play], PrinterServer]
) -> None:
    modules = ImageModules(lose={0})
    server = serve(module_printer(modules))
    args = ["--json", "print", _label_file(tmp_path), "--model", "allegro-2", "--printer", server.address]

    result = _run(cli_runner, args)
    server.join()

    assert result.exit_code == 0, result.output
    received = bytes(server.received)
    assert received.count(WG) == 2
    assert received.index(WG) < received.index(FORMAT)
    assert len(modules.names("A")) == 0  # the clear after the label emptied it again
    codes = [finding["code"] for finding in json.loads(result.stdout)["data"]["findings"]]
    assert "image_upload_retried" in codes


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_over_tcp_refuses_a_label_the_printer_never_lists_and_sends_no_format(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[[Play], PrinterServer]
) -> None:
    server = serve(module_printer(ImageModules(capacity=1)))
    args = ["--json", "print", _label_file(tmp_path), "--model", "allegro-2", "--printer", server.address]

    result = _run(cli_runner, args, DATAMAX_DPL___PRINTER__UPLOAD_CHECK_ROUNDS="2")
    server.join()

    assert result.exit_code == 1, result.output
    assert FORMAT not in bytes(server.received)
    assert bytes(server.received).count(WG) == 2
    data = json.loads(result.stdout)["data"]
    errors = [finding for finding in data["findings"] if finding["severity"] == "error"]
    assert [finding["code"] for finding in errors] == ["image_upload_refused"]
    assert "after 2 rounds" in errors[0]["message"]


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_verify_over_tcp_refuses_a_label_the_printer_never_lists_and_sends_no_format(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[[Play], PrinterServer]
) -> None:
    server = serve(module_printer(ImageModules(capacity=1)))
    output_dir = tmp_path / "out"
    args = ["--json", "verify", _label_file(tmp_path), "--output-dir", str(output_dir)]

    result = _run(
        cli_runner,
        [*args, "--model", "allegro-2", "--printer", server.address],
        DATAMAX_DPL___PRINTER__UPLOAD_CHECK_ROUNDS="2",
        DATAMAX_DPL___CAMERA__KIND="file",
        DATAMAX_DPL___CAMERA__DIRECTORY=str(tmp_path),
    )
    server.join()

    assert result.exit_code == 1, result.output
    assert FORMAT not in bytes(server.received)
    assert bytes(server.received).count(WG) == 2
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    errors = [finding for finding in envelope["data"]["findings"] if finding["severity"] == "error"]
    assert [finding["code"] for finding in errors] == ["image_upload_refused"]
    assert "after 2 rounds" in errors[0]["message"]
    assert list(output_dir.glob("expected-*.png")) == []


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_over_tcp_sends_the_format_on_a_new_connection_when_the_server_closed_after_the_answer(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[..., PrinterServer]
) -> None:
    """The server closes 0.3 s after the directory answer, inside the 1 s of silence that ends it."""
    modules = ImageModules()
    server = serve(module_printer_closing(modules, after=0.3), module_printer(modules))
    args = ["--json", "print", _label_file(tmp_path), "--model", "allegro-2", "--printer", server.address]

    result = _run(cli_runner, args)
    server.join()

    assert result.exit_code == 0, result.output
    first, second = (bytes(connection) for connection in server.connections)
    assert FORMAT not in first
    assert second.startswith(FORMAT)


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_to_a_file_reads_nothing_and_writes_the_job_in_one_piece(cli_runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "job.dpl"
    args = ["print", _label_file(tmp_path), "--model", "allegro-2", "--printer", f"file:{target}"]

    result = _run(cli_runner, args)

    assert result.exit_code == 0, result.output
    assert target.read_bytes() == render_label(Label(elements=[LOGO]), ALLEGRO).data
    assert WG not in target.read_bytes()


def _big_label_file(tmp_path: Path) -> str:
    path = tmp_path / "big.json"
    path.write_text(json.dumps({"elements": [BIG.model_dump(mode="json")]}), encoding="utf-8")
    return str(path)


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_over_tcp_sends_images_past_the_model_budget_at_full_size_while_validate_halves_them(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[[Play], PrinterServer]
) -> None:
    modules = ImageModules()
    server = serve(module_printer(modules))
    source = _big_label_file(tmp_path)
    [full] = render_label(Label(elements=[BIG]), TWO_WAY).downloads

    printed = _run(cli_runner, ["--json", "print", source, "--model", "allegro-2", "--printer", server.address])
    server.join()
    validated = _run(cli_runner, ["--json", "validate", source, "--model", "allegro-2"])

    assert printed.exit_code == 0, printed.output
    assert modules.downloads == [full]
    print_codes = [finding["code"] for finding in json.loads(printed.stdout)["data"]["findings"]]
    assert "image_budget_skipped" in print_codes
    assert "image_halved" not in print_codes
    validate_codes = [finding["code"] for finding in json.loads(validated.stdout)["data"]["findings"]]
    assert "image_halved" in validate_codes
    assert "image_budget_skipped" not in validate_codes


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_to_a_file_keeps_the_model_budget_and_halves_images_past_it(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    target = tmp_path / "job.dpl"
    args = ["print", _big_label_file(tmp_path), "--model", "allegro-2", "--printer", f"file:{target}"]

    result = _run(cli_runner, args)

    assert result.exit_code == 0, result.output
    assert target.read_bytes() == render_label(Label(elements=[BIG]), ALLEGRO).data


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("no_ambient_configuration")
def test_print_over_tcp_to_module_c_is_refused_with_nothing_sent(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[[Play], PrinterServer]
) -> None:
    server = serve(module_printer(ImageModules()))
    args = ["--json", "print", _label_file(tmp_path), "--model", "allegro-2", "--printer", server.address]

    result = _run(cli_runner, args, DATAMAX_DPL___PRINTER__IMAGE_MODULE="C")
    server.join()

    assert result.exit_code == 1, result.output
    assert STX + b"I" not in bytes(server.received)
    assert FORMAT not in bytes(server.received)
    errors = [f for f in json.loads(result.stdout)["data"]["findings"] if f["severity"] == "error"]
    assert [finding["code"] for finding in errors] == ["image_module_unlisted"]


# ======================== what the domain hands the sender ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("options", "clear", "empties"),
    [
        (ALLEGRO, b"qA", True),
        (RenderOptions(model="dmx-600"), b"qE", True),
        (RenderOptions(model="allegro-2", image_module=ImageModule.B), b"", False),
        (RenderOptions(model="generic"), b"", False),
    ],
)
def test_the_retry_clear_is_q_on_built_in_ram_and_nothing_where_no_clear_may_go(
    options: RenderOptions, clear: bytes, empties: bool
) -> None:
    result = render_label(Label(elements=[LOGO]), options)

    assert result.retry_clear == (STX + clear if clear else b"")
    assert result.retry_clear_empties is empties


@pytest.mark.os_agnostic
def test_the_prodigys_retry_clear_removes_the_labels_own_names_whatever_the_clear_keys_say() -> None:
    options = RenderOptions(
        model="prodigy",
        image_module=ImageModule.A,
        clear_graphics_before_label=False,
        clear_graphics_after_label=False,
    )
    result = render_label(Label(elements=[LOGO]), options)

    [name] = result.downloads
    assert result.retry_clear == STX + b"R" + name.encode("ascii") + b"\r"
    assert not result.retry_clear_empties


@pytest.mark.os_agnostic
def test_the_upload_ends_after_the_last_download_and_the_format_follows_it() -> None:
    result = render_label(Label(elements=[LOGO, Qr(x=5, y=30, data="CODE")]), ALLEGRO)

    upload, rest = result.data[: result.upload_end], result.data[result.upload_end :]
    assert upload.count(STX + b"IA") == 2
    assert upload.endswith(b"FFFF\r")
    assert rest.startswith(FORMAT)


@pytest.mark.os_agnostic
def test_a_batch_cut_across_records_sits_in_each_records_job_and_the_jobs_join_to_the_data() -> None:
    label = Label.model_validate({"elements": [{"type": "qr", "x": 5, "y": 5, "data": "{code}"}], "cut": {"every": 2}})
    batch = render_batch(label, [{"code": "A"}, {"code": "B"}, {"code": "C"}], RenderOptions(model="dmx-600"))

    assert batch.cut_after == (False, True, True)
    assert b"".join(batch.record_jobs()) == batch.data
    assert [job.count(STX + b"o") for job in batch.record_jobs()] == [0, 1, 1]


def _two_label_mismatch() -> tuple[BatchResult, CheckedJob]:
    """A result for three records and renders for two: the two arguments describe different batches."""
    label = Label(elements=[Text(x=1, y=1, text="{n}")])
    options = RenderOptions(model="dmx-600")
    three = [{"n": "1"}, {"n": "2"}, {"n": "3"}]
    result = render_batch(label, three, options)
    return result, CheckedJob(renders=record_renders(label, three[:2], options), check=_check())


@pytest.mark.os_agnostic
def test_print_checked_refuses_a_result_and_renders_that_disagree_before_opening_the_printer() -> None:
    """Two labels printed and then a raw zip error is what a mismatch did; nothing may go out."""
    result, job = _two_label_mismatch()
    opened: list[str] = []

    def open_printer(uri: str, *, timeout: float) -> PrinterTransport:
        opened.append(uri)
        raise AssertionError("the printer must not be opened")

    with pytest.raises(ConfigurationError, match="3 labels.*2 renders"):
        print_checked(result, job, uri=URI, timeout=1.0, open_printer=open_printer)
    assert opened == []


@pytest.mark.os_agnostic
def test_send_checked_refuses_a_result_and_renders_that_disagree_before_sending() -> None:
    """The same check holds for a caller with its own link."""
    result, job = _two_label_mismatch()
    printer = RecordingPrinter()
    link = Link(
        printer=printer,
        uri=URI,
        timeout=1.0,
        open_printer=open_recording_printer(printer),
        reconnect=None,
    )
    with pytest.raises(ConfigurationError, match="3 labels.*2 renders"):
        send_checked(link, result, job)
    assert printer.data == b""
