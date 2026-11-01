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
from printer_server import ImageModules, ModulePrinter, module_printer

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
from datamax_dpl.application.upload_check import CheckedJob, UploadCheck, UploadRefusedError, print_checked
from datamax_dpl.application.use_cases import AnswerWait, BatchResult, record_renders, render_batch, render_label
from datamax_dpl.application.verify import verify_print
from datamax_dpl.composition import build_production
from datamax_dpl.domain.camera import CameraKind, CameraSettings, CapturedImage
from datamax_dpl.domain.enums import FallbackPolicy, FindingCode, ImageModule, MediaType, Severity
from datamax_dpl.domain.errors import LabelRejectedError, TransportError
from datamax_dpl.domain.held_images import HeldImages
from datamax_dpl.domain.label import Image, Label, Qr
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.render import RenderResult
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.status import MEMORY_DIRECTORY_REQUESTS, STATUS_REQUEST

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from click.testing import CliRunner
    from printer_server import Play, PrinterServer

    from datamax_dpl.application.ports import PrinterTransport

STX = b"\x02"
WG = MEMORY_DIRECTORY_REQUESTS["graphic"]
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
        print_checked(result, job, uri=URI, timeout=5.0, open_printer=open_recording_printer(printer))

    assert printer.opened == []


class _HangingUpOnTheRead(ModulePrinter):
    """A print server that takes the downloads and closes the connection before the directory request."""

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = 120.0) -> bytes:
        self.closed = True
        raise TransportError("module printer: connection reset", unanswered=True)


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
