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
from datamax_dpl.application.ports import PrinterEndpoint
from datamax_dpl.application.upload_check import (
    CheckedJob,
    Link,
    UploadCheck,
    UploadRefusedError,
    print_checked,
    send_checked,
)
from datamax_dpl.application.use_cases import (
    AnswerWait,
    BatchReport,
    BatchResult,
    record_renders,
    render_batch,
    render_label,
)
from datamax_dpl.application.verify import verify_print
from datamax_dpl.composition import build_production, build_testing
from datamax_dpl.domain.camera import CameraKind, CameraSettings, CapturedImage
from datamax_dpl.domain.enums import FallbackPolicy, FindingCode, ImageModule, MediaType, MemoryKind, Severity
from datamax_dpl.domain.errors import ConfigurationError, LabelRejectedError, PrinterTimeoutError, TransportError
from datamax_dpl.domain.held_images import READ_AT_SEND
from datamax_dpl.domain.label import Image, Label, Qr, Text
from datamax_dpl.domain.png import encode_png
from datamax_dpl.domain.preview import preview_bitmap
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.render import RenderResult, build_record
from datamax_dpl.domain.render_context import RenderOptions
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
    """Render as ``print`` does for a two-way link: each label's names are read right before its downloads."""
    if records is None:
        result: RenderResult | BatchResult = render_label(label, options, held=READ_AT_SEND)
    else:
        result = render_batch(label, records, options, read_names_at_send=True)
    return result, CheckedJob(renders=record_renders(label, records, options), reset=None, check=_check(rounds))


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
    report = print_checked(
        result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
    )
    assert report.bytes_sent == sum(len(chunk) for chunk in printer.sent if chunk != WG)
    return printer, report.result


def _single(result: RenderResult | BatchResult | BatchReport) -> RenderResult:
    assert isinstance(result, RenderResult)
    return result


def _refused(
    modules: ImageModules, label: Label, options: RenderOptions = ALLEGRO, *, rounds: int = 3
) -> tuple[ModulePrinter, UploadRefusedError]:
    printer = ModulePrinter(modules)
    result, job = _job(label, options, None, rounds)
    with pytest.raises(UploadRefusedError) as caught:
        print_checked(
            result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
        )
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
    # The names read before the downloads, then each round's read.
    assert printer.queries == [WG] * 4
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
def test_a_name_the_label_s_own_round_1_stored_is_left_out_of_round_2_with_no_held_finding() -> None:
    """ICON arrived in round 1 and LOGO was lost: round 2 places ICON, the label's own picture, and says nothing of it.

    A held name in error refuses a label; one the label itself sent is not a name the printer held without it.
    """
    label = Label(elements=[LOGO, ICON])
    logo, icon = render_label(label, PLUG_IN).downloads
    held_in_error = PLUG_IN.model_copy(update={"held_image_severity": Severity.ERROR})
    # Module B holds another job's image, so the read before the downloads lists module B.
    modules = ImageModules(held=[("B", "OTHER", 10)], lose={0})

    _, sent = _print(modules, label, held_in_error)

    assert modules.downloads == [logo, icon, logo]
    assert FindingCode.IMAGE_NAME_HELD not in [f.code for f in _single(sent).findings]


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
    # Request 0 is the names read before the downloads, which lists an empty module B.
    modules = ImageModules(held=[("B", "OTHER", 10)], silent={1})
    printer = ModulePrinter(modules)
    result = render_label(label, PLUG_IN_TWO_WAY, held=READ_AT_SEND)
    job = CheckedJob(renders=record_renders(label, None, PLUG_IN_TWO_WAY), reset=None, check=_check(rounds))

    report = print_checked(
        result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
    )

    sent = _single(report.result)
    assert (sent.halved_elements, sent.downloads) == ((), ())
    assert modules.downloads == [logo]
    assert modules.downloaded_held == []
    assert modules.placed_missing == []
    assert printer.queries == [WG, WG, WG]
    names_read, upload, first_read, second_read, rest = printer.sent
    assert (names_read, first_read, second_read) == (WG, WG, WG)
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
    modules = ImageModules(held=[("B", "OTHER", 10)], silent={1, 2})

    printer, sent = _print(modules, label, PLUG_IN)

    result = _single(sent)
    assert modules.downloaded_held == []
    assert modules.downloads.count(logo) == 1
    assert result.halved_elements == (0,)
    # The names read before the downloads, round 1's read, the names read again in round 2, round 3's read.
    assert printer.queries == [WG] * 4
    assert printer.data.count(FORMAT) == 1
    first, second = (f.message for f in result.findings if f.code is FindingCode.IMAGE_UPLOAD_RETRIED)
    assert first.startswith(f"round 2 skipped, it would download again {logo}:")
    assert f"; the names read again got no answer either (reading them failed: {URI}: no answer in " in first
    assert second.startswith("round 3:")


@pytest.mark.os_agnostic
def test_such_a_module_refuses_after_a_read_with_no_answer_when_nothing_can_be_halved() -> None:
    label = Label(elements=[Qr(x=5, y=150, data="CODE")])

    printer, refused = _refused(ImageModules(held=[("B", "OTHER", 10)], silent={1, 2}), label, PLUG_IN)

    result = _single(refused.result)
    assert FORMAT not in printer.data
    assert printer.queries == [WG, WG, WG]
    # The names read before the downloads, the one upload, its read, the names read again; no clear may go.
    assert len(printer.sent) == 4
    message = _message(result, FindingCode.IMAGE_UPLOAD_REFUSED)
    assert "after 1 round " in message
    assert "round 2 skipped," in message
    assert "no image of the label can be halved" in message
    assert _message(result, FindingCode.IMAGE_UPLOAD_RETRIED).startswith("round 2 skipped,")


@pytest.mark.os_agnostic
def test_such_a_module_refuses_the_halving_round_that_would_download_an_unread_qr_code_again() -> None:
    label = Label(elements=[Qr(x=5, y=150, data="CODE"), LOGO])
    qr, _logo = render_label(label, PLUG_IN).downloads
    modules = ImageModules(held=[("B", "OTHER", 10)], silent={1, 2})

    printer, refused = _refused(modules, label, PLUG_IN)

    assert modules.downloaded_held == []
    assert modules.downloads.count(qr) == 1
    assert FORMAT not in printer.data
    assert printer.queries == [WG, WG, WG]
    message = _message(_single(refused.result), FindingCode.IMAGE_UPLOAD_REFUSED)
    assert "round 2 skipped," in message
    assert (
        f"halving is skipped, it would download again (a QR Code is never halved, so its name stays) {qr}:" in message
    )


@pytest.mark.os_agnostic
def test_such_a_module_halves_from_the_last_answered_read_when_the_round_2_read_got_no_answer() -> None:
    label = Label(elements=[Qr(x=5, y=150, data="CODE"), LOGO])
    qr, logo = render_label(label, PLUG_IN).downloads
    modules = ImageModules(held=[("B", "OTHER", 10)], lose={1}, silent={2})

    printer, sent = _print(modules, label, PLUG_IN)

    result = _single(sent)
    assert modules.downloaded_held == []
    assert modules.downloads.count(qr) == 1
    assert modules.downloads.count(logo) == 2
    assert result.halved_elements == (1,)
    assert printer.queries == [WG] * 4
    assert printer.data.count(FORMAT) == 1


#: A plug-in module rendered for a two-way print: the check, not the model's budget, decides halving.
PLUG_IN_TWO_WAY = PLUG_IN.model_copy(update={"model_image_budget": False})


@pytest.mark.os_agnostic
def test_a_label_in_error_once_rendered_from_the_names_read_before_its_downloads_is_refused_with_nothing_sent() -> None:
    """The names read list ICON, which another program stored, and a held name is in error here: nothing goes out."""
    label = Label(elements=[LOGO, ICON])
    _logo, icon = render_label(label, PLUG_IN_TWO_WAY).downloads
    held_in_error = PLUG_IN_TWO_WAY.model_copy(update={"held_image_severity": Severity.ERROR})
    modules = ImageModules(held=[("B", icon, 512)])

    printer, refused = _refused(modules, label, held_in_error)

    assert printer.sent == [WG]
    assert modules.downloads == []
    assert str(refused) == (
        "a label, rendered again during its upload check for what the printer then held, has errors; "
        "its format was not sent, nor anything after it"
    )
    message = _message(_single(refused.result), FindingCode.IMAGE_UPLOAD_RENDER_REFUSED)
    assert message.startswith(
        "the label, rendered again from the names read before its downloads, has errors (image ICON (sent as "
    )
    assert icon in message


@pytest.mark.os_agnostic
def test_a_later_label_in_error_once_rendered_from_round_2_s_read_again_is_refused_with_nothing_of_it_sent() -> None:
    """Label 2's read before its downloads gets no answer; round 2 reads again, lists ICON, and refuses it there."""
    first, second = Label(elements=[LOGO]), Label(elements=[ICON])
    [logo] = render_label(first, PLUG_IN_TWO_WAY).downloads
    [icon] = render_label(second, PLUG_IN_TWO_WAY).downloads
    held_in_error = PLUG_IN_TWO_WAY.model_copy(update={"held_image_severity": Severity.ERROR})
    # Requests 0 and 1 are label 1's read before its downloads and its upload check's read.
    modules = ImageModules(held=[("B", icon, 512)], silent={2})
    printer = ModulePrinter(modules)
    renders = (
        render_label(first, PLUG_IN_TWO_WAY, held=READ_AT_SEND),
        render_label(second, held_in_error, held=READ_AT_SEND),
    )
    job = CheckedJob(
        renders=(*record_renders(first, None, PLUG_IN_TWO_WAY), *record_renders(second, None, held_in_error)),
        reset=None,
        check=_check(),
    )

    with pytest.raises(UploadRefusedError) as caught:
        print_checked(
            BatchResult(results=renders),
            job,
            endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer)),
        )

    assert modules.downloads == [logo]
    assert modules.downloaded_held == []
    assert printer.data.count(FORMAT) == 1
    sent = caught.value.result
    assert isinstance(sent, BatchResult)
    message = _message(sent.results[1], FindingCode.IMAGE_UPLOAD_RENDER_REFUSED)
    assert message.startswith("the label, rendered again for round 2, has errors (image ICON (sent as ")
    assert [f.code for f in caught.value.findings] == [FindingCode.IMAGE_UPLOAD_RENDER_REFUSED]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("lose", "silent", "reads"),
    # Request 0 is the names read before the downloads, which lists module B.
    [({0}, {2}, 4), ({0}, {1, 3}, 5)],
    ids=["round-2-upload-unread", "round-1-unread-and-round-2-upload-unread"],
)
def test_such_a_module_halves_with_no_further_read_when_round_2_s_read_after_its_upload_got_no_answer(
    lose: set[int], silent: set[int], reads: int
) -> None:
    """An accepted limit (see _Record.halve_and_upload): the full-size name arrived, yet the label prints halved.

    A read before round 3 might print it at full size, and would add one wait to the worst case;
    this pins the choice, so a change to it is a deliberate one.
    """
    label = Label(elements=[LOGO])
    [logo] = render_label(label, PLUG_IN_TWO_WAY).downloads
    modules = ImageModules(held=[("B", "OTHER", 10)], lose=lose, silent=silent)
    printer = ModulePrinter(modules)
    job = CheckedJob(renders=record_renders(label, None, PLUG_IN_TWO_WAY), reset=None, check=_check())
    result = render_label(label, PLUG_IN_TWO_WAY, held=READ_AT_SEND)

    report = print_checked(
        result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
    )

    assert _single(report.result).halved_elements == (0,)
    assert logo in modules.names("B")
    assert modules.requests == reads
    assert printer.data.count(FORMAT) == 1
    assert modules.downloaded_held == []
    assert modules.placed_missing == []


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
    """Record 1's clear after takes LOGO out: the names record 2 reads before its downloads miss it."""
    label = Label(elements=[LOGO])
    records = [{"n": "1"}, {"n": "2"}]
    [logo] = render_label(label, options).downloads
    modules = ImageModules()

    _, sent = _print(modules, label, options, records=records)

    assert isinstance(sent, BatchResult)
    assert [record.downloads for record in sent.results] == [(logo,), (logo,)]
    assert modules.downloaded_held == []


#: The plain Prodigy into module B with the clear keys off: its retry clear removes the label's own names.
PRODIGY_NO_KEYS = RenderOptions(
    model="prodigy",
    image_module=ImageModule.B,
    clear_graphics_before_label=False,
    clear_graphics_after_label=False,
    model_image_budget=False,
)


class _CutBeforeModuleB(ImageModules):
    """Answers the read after round 1 with module A's block only, as if the answer ended there."""

    def _directory(self) -> bytes:
        whole = super()._directory()
        # Request 1 is the names read before the downloads; request 2 the read after round 1.
        return whole.split(b"MODULE: B")[0] if self.requests == 2 else whole


@pytest.mark.os_agnostic
def test_a_directory_answer_that_leaves_out_the_job_s_module_is_no_answer_and_no_name_goes_twice() -> None:
    """Read as module B empty, the answer would make round 2 download LOGO, which B holds, again.

    Round 2 reads the names again instead, which list LOGO: the label places it.
    """
    label = Label(elements=[LOGO])
    [logo] = render_label(label, PLUG_IN_TWO_WAY).downloads
    modules = _CutBeforeModuleB(held=[("A", "OTHER", 10), ("B", "OTHER", 10)])
    printer = ModulePrinter(modules)
    result = render_label(label, PLUG_IN_TWO_WAY, held=READ_AT_SEND)
    job = CheckedJob(renders=record_renders(label, None, PLUG_IN_TWO_WAY), reset=None, check=_check())

    report = print_checked(
        result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
    )

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
    """The names read before the downloads got no answer; module B holds LOGO's halved name from an earlier job.

    Rounds 1 and 2 lose the full-size download, and both reads list the halved name. The Prodigy's
    removes take out only the label's own full-size name, so round 3 must place the halved name the
    module holds, not download it again with no clear before it (which prints nothing).
    """
    label = Label(elements=[LOGO])
    [halved] = build_record(label, PRODIGY_NO_KEYS, halve=True).downloads
    modules = ImageModules(held=[("B", halved, 128)], lose={0, 1}, silent={0})
    printer = ModulePrinter(modules)
    result = render_label(label, PRODIGY_NO_KEYS, held=READ_AT_SEND)
    job = CheckedJob(renders=record_renders(label, None, PRODIGY_NO_KEYS), reset=None, check=_check())

    report = print_checked(
        result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
    )

    assert modules.downloaded_held == []
    assert modules.placed_missing == []
    sent = _single(report.result)
    assert (sent.halved_elements, sent.downloads) == ((0,), ())
    # Round 3 downloads nothing, so the label's whole job follows its removes.
    *_, rest = printer.sent
    assert rest.count(FORMAT) == 1 and halved.encode("ascii") in rest
    # The names read before the downloads, then rounds 1 and 2; round 3 downloads nothing and reads nothing.
    assert printer.queries == [WG, WG, WG]


@pytest.mark.os_agnostic
def test_the_plain_prodigys_halving_round_downloads_again_a_qr_code_its_removes_took_out() -> None:
    """Rounds 1 and 2 lose LOGO and their reads list the QR Code, which the removes before round 3 take out.

    A QR Code keeps its name when halved: round 3 must download it again, not place the name the
    last read listed, which the module no longer holds.
    """
    label = Label(elements=[Qr(x=5, y=150, data="CODE"), LOGO])
    qr, logo = render_label(label, PRODIGY_NO_KEYS).downloads
    modules = ImageModules(held=[("B", "OTHER", 10)], lose={1, 3})
    printer = ModulePrinter(modules)
    result = render_label(label, PRODIGY_NO_KEYS, held=READ_AT_SEND)
    job = CheckedJob(renders=record_renders(label, None, PRODIGY_NO_KEYS), reset=None, check=_check())

    report = print_checked(
        result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
    )

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


@pytest.mark.os_agnostic
def test_a_job_with_an_error_finding_is_refused_before_anything_is_opened() -> None:
    printer = ModulePrinter(ImageModules())
    result, job = _job(Label(elements=[LOGO]), RenderOptions(model="prodigy"), None, 3)
    assert not result.ok

    with pytest.raises(LabelRejectedError):
        print_checked(
            result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
        )

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
    report = print_checked(result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_printer))

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
    print_checked(result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_printer))

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
        print_checked(result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=opener))

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
        print_checked(result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=opener))

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
            options=ALLEGRO,
            endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer)),
            camera=CameraSettings(kind=CameraKind.FILE),
            output_dir=tmp_path,
            capture=capture,
            sleep=lambda _seconds: None,
            checked=job,
        )

    assert FORMAT not in printer.data
    assert STATUS_REQUEST not in printer.data
    assert photos == []


@pytest.mark.os_agnostic
def test_a_one_way_channel_reads_nothing_and_gets_the_whole_job_in_one_send() -> None:
    printer = RecordingPrinter(bidirectional=False)
    result, job = _job(Label(elements=[LOGO]), ALLEGRO, None, 3)

    report = print_checked(
        result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
    )

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
    # Each record's names read before its downloads; record 3's lists FIRST, so it downloads nothing
    # and is sent whole with no read after.
    assert printer.queries == [WG] * 5


@pytest.mark.os_agnostic
def test_a_record_cleared_by_its_retry_makes_the_next_record_download_what_the_clear_removed() -> None:
    """With no clear keys, record 3 places FIRST where record 1 stored it; here record 2's clear removed it.

    The names record 3 reads before its downloads no longer list FIRST, so it downloads it again.
    """
    label, records, options = _qr_batch(clear_graphics_before_label=False, clear_graphics_after_label=False)
    first = render_label(label.model_copy(update={"elements": [Qr(x=5, y=5, data=records[0]["code"])]}), options)
    modules = ImageModules(lose={1})

    _, sent = _print(modules, label, options, records=records)

    assert isinstance(sent, BatchResult)
    assert sent.results[2].downloads == first.downloads
    assert modules.names("A") == [*sent.results[1].downloads, *sent.results[2].downloads]


@pytest.mark.os_agnostic
def test_a_refused_record_ends_the_batch_and_the_records_after_it_are_not_sent() -> None:
    label, records, options = _qr_batch()
    printer = ModulePrinter(ImageModules(lose={1, 2, 3}))
    result, job = _job(label, options, records, 2)

    with pytest.raises(UploadRefusedError) as caught:
        print_checked(
            result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
        )

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
        options=ALLEGRO,
        endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer)),
        camera=CameraSettings(kind=CameraKind.FILE),
        output_dir=tmp_path,
        capture=capture,
        sleep=lambda _seconds: None,
        checked=job,
    )

    assert report.result is not None
    assert report.result.halved_elements == (0,)
    assert printer.data.count(FORMAT) == 1
    assert printer.queries[-1] == STATUS_REQUEST
    profile = get_profile("allegro-2")
    as_first_rendered = encode_png(preview_bitmap(label, profile, sent_as=_single(result).sent_as, options=ALLEGRO))
    assert report.expected_png != as_first_rendered
    assert report.expected_png == encode_png(
        preview_bitmap(label, profile, sent_as=report.result.sent_as, options=ALLEGRO)
    )


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
    label = Label(elements=[Qr(x=5, y=5, data="TWO")])
    options = RenderOptions(
        model="allegro-2",
        image_module=ImageModule.C,
        clear_graphics_before_label=False,
        clear_graphics_after_label=False,
    )
    words = Label(elements=[Text(x=5, y=5, text="ONE")])
    result = BatchResult(
        results=(render_label(words, options, held=READ_AT_SEND), render_label(label, options, held=READ_AT_SEND))
    )
    assert [len(r.downloads) for r in result.results] == [0, 1]
    job = CheckedJob(
        renders=(*record_renders(words, None, options), *record_renders(label, None, options)),
        reset=None,
        check=_check(),
    )
    printer = ModulePrinter(ImageModules())

    with pytest.raises(UploadRefusedError) as caught:
        print_checked(
            result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
        )

    assert printer.sent == []
    assert caught.value.bytes_sent == 0
    assert [f.code for f in caught.value.findings] == [FindingCode.IMAGE_MODULE_UNLISTED]


@pytest.mark.os_agnostic
def test_a_one_way_channel_sends_a_module_c_job_whole_as_before() -> None:
    options = RenderOptions(model="allegro-2", image_module=ImageModule.C)
    printer = RecordingPrinter(bidirectional=False)
    result, job = _job(Label(elements=[LOGO]), options, None, 3)

    print_checked(
        result, job, endpoint=PrinterEndpoint(uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))
    )

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
    return result, CheckedJob(renders=record_renders(label, three[:2], options), reset=None, check=_check())


@pytest.mark.os_agnostic
def test_print_checked_refuses_a_result_and_renders_that_disagree_before_opening_the_printer() -> None:
    """Two labels printed and then a raw zip error is what a mismatch did; nothing may go out."""
    result, job = _two_label_mismatch()
    opened: list[str] = []

    def open_printer(uri: str, *, timeout: float) -> PrinterTransport:
        opened.append(uri)
        raise AssertionError("the printer must not be opened")

    with pytest.raises(ConfigurationError, match=r"3 labels.*2 renders"):
        print_checked(result, job, endpoint=PrinterEndpoint(uri=URI, timeout=1.0, open_printer=open_printer))
    assert opened == []


@pytest.mark.os_agnostic
def test_send_checked_refuses_a_result_and_renders_that_disagree_before_sending() -> None:
    """The same check holds for a caller with its own link."""
    result, job = _two_label_mismatch()
    printer = RecordingPrinter()
    link = Link(
        printer=printer,
        endpoint=PrinterEndpoint(uri=URI, timeout=1.0, open_printer=open_recording_printer(printer)),
        reconnect=AnswerWait(seconds=1.0),
    )
    with pytest.raises(ConfigurationError, match=r"3 labels.*2 renders"):
        send_checked(link, result, job)
    assert printer.data == b""


class _RecordingReadWaits(ModulePrinter):
    """Keeps how long each directory read was given: the one seam that shows which wait governs it."""

    def __init__(self, modules: ImageModules) -> None:
        super().__init__(modules)
        self.read_waits: list[float] = []

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = 120.0) -> bytes:
        self.read_waits.append(timeout)
        return super().read_answer(request, timeout=timeout, idle=idle, total=total)


@pytest.mark.os_agnostic
def test_the_cli_upload_check_waits_stored_names_wait_not_status_wait(cli_runner: CliRunner, tmp_path: Path) -> None:
    label = tmp_path / "label.json"
    label.write_text(json.dumps({"elements": [LOGO.model_dump(mode="json", exclude_none=True)]}), encoding="utf-8")
    printer = _RecordingReadWaits(ImageModules())

    result = cli_runner.invoke(
        cli_mod.cli,
        [
            "--set",
            "printer.stored_names_wait=7",
            "--set",
            "printer.status_wait=9",
            "--json",
            "print",
            str(label),
            "--model",
            "allegro-2",
            "--printer",
            URI,
        ],
        obj=lambda: build_testing(printer=printer),
    )

    assert result.exit_code == 0, result.output
    assert printer.read_waits
    assert all(5.0 < wait <= 7.0 for wait in printer.read_waits), printer.read_waits
