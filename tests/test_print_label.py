"""``print_label`` makes the decisions ``print`` makes, so a library caller gets the same job."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from printer_server import ImageModules, ModulePrinter

import datamax_dpl
from datamax_dpl import (
    AnswerWait,
    ConfigurationError,
    Image,
    Label,
    OpenPrinter,
    PrinterEndpoint,
    PrinterTransport,
    PrintRefusedError,
    PrintRequest,
    RenderOptions,
    Text,
    UploadCheck,
    print_label,
    printer_uri_is_one_way,
)
from datamax_dpl.adapters.cli.job_spool import FileJobSpool
from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
from datamax_dpl.application.use_cases import ONE_WAY_LINK, BatchReport
from datamax_dpl.domain.enums import FindingCode
from datamax_dpl.domain.render import RenderResult

if TYPE_CHECKING:
    from collections.abc import Iterator

_TCP = "tcp://192.0.2.60:9100"
_FILE = "file:job.dpl"
#: 640 x 640 dots: 51200 bytes of dot data, past the Allegro 2's 49088-byte model budget.
_BIG_ROWS = ["".join("#" if (row * 3 + col * 11) % 7 == 0 else "." for col in range(640)) for row in range(640)]
_BIG = Label(elements=[Image(x=1, y=1, name="BIG", bitmap=_BIG_ROWS)])
#: Room for the image at full size, as a bigger cartridge than the model's budget would have.
_ROOMY = 65536
_SMALL_ROWS = ["#.#.#.#." for _ in range(8)]
_NO_CLEAR = RenderOptions(model="allegro-2", clear_graphics_before_label=False, clear_graphics_after_label=False)
_LOGO_BATCH = Label(elements=[Image(x=1, y=1, name="LOGO", bitmap=_SMALL_ROWS), Text(x=1, y=20, text="{a}")])
_ALLEGRO = RenderOptions(model="allegro-2")
#: Past the Allegro 2's 104 mm print width, which refuses an image rather than clipping it.
_PAST_WIDTH = Label(elements=[Image(x=110, y=1, name="WIDE", bitmap=_SMALL_ROWS)])


def _never_opened(uri: str, *, timeout: float) -> PrinterTransport:
    msg = f"{uri} was opened within {timeout} s for a job that was refused"
    raise AssertionError(msg)


def _request(
    opener: OpenPrinter,
    *,
    one_way: bool,
    options: RenderOptions = _ALLEGRO,
    rounds: int = 3,
) -> PrintRequest:
    return PrintRequest(
        options=options,
        endpoint=PrinterEndpoint(uri=_FILE if one_way else _TCP, timeout=1.0, open_printer=opener),
        check=UploadCheck(wait=AnswerWait(seconds=1), rounds=rounds),
        one_way=one_way,
    )


def _single(result: RenderResult | BatchReport) -> RenderResult:
    assert isinstance(result, RenderResult)
    return result


def _opener(*, one_way: bool) -> OpenPrinter:
    if one_way:
        return open_recording_printer(RecordingPrinter(bidirectional=False))
    return open_recording_printer(ModulePrinter(ImageModules(capacity=_ROOMY)))


@pytest.mark.os_agnostic
@pytest.mark.parametrize("given", [True, False], ids=["caller_budget_on", "caller_budget_off"])
@pytest.mark.parametrize(
    ("one_way", "rounds", "halved"),
    [(False, 3, ()), (False, 0, (0,)), (True, 3, (0,)), (True, 0, (0,))],
    ids=["two_way_checked", "two_way_unchecked", "one_way_checked", "one_way_unchecked"],
)
def test_the_link_and_the_rounds_decide_whether_the_model_budget_applies(
    one_way: bool,
    rounds: int,
    halved: tuple[int, ...],
    given: bool,
) -> None:
    options = RenderOptions(model="allegro-2", model_image_budget=given)

    printed = print_label(_BIG, _request(_opener(one_way=one_way), one_way=one_way, options=options, rounds=rounds))

    assert _single(printed.result).halved_elements == halved


@pytest.mark.os_agnostic
def test_a_one_way_print_never_reads_even_a_channel_that_could_and_says_why_the_names_were_not_read() -> None:
    printer = RecordingPrinter()
    label = Label(elements=[Image(x=1, y=1, name="LOGO", bitmap=_SMALL_ROWS)])

    printed = print_label(label, _request(open_recording_printer(printer), one_way=True, options=_NO_CLEAR))

    result = _single(printed.result)
    assert b"".join(printer.sent) == result.data
    assert printed.bytes_sent == len(result.data)
    [unread] = [f for f in result.findings if f.code is FindingCode.IMAGE_NAMES_UNREAD]
    assert ONE_WAY_LINK in unread.message


@pytest.mark.os_agnostic
@pytest.mark.parametrize("one_way", [False, True], ids=["two_way", "one_way"])
def test_a_refused_label_opens_no_printer_and_the_error_carries_the_job(one_way: bool) -> None:
    with pytest.raises(PrintRefusedError) as refused:
        print_label(_PAST_WIDTH, _request(_never_opened, one_way=one_way))

    assert not refused.value.result.ok
    assert refused.value.findings


@pytest.mark.os_agnostic
def test_a_two_way_batch_reads_the_names_at_send_so_a_stored_image_is_not_refused() -> None:
    request = _request(_opener(one_way=False), one_way=False, options=_NO_CLEAR)._replace(
        records=[{"a": "1"}, {"a": "2"}]
    )

    printed = print_label(_LOGO_BATCH, request)

    assert isinstance(printed.result, BatchReport)
    assert (printed.result.ok, printed.result.records) == (True, 2)


@pytest.mark.os_agnostic
def test_a_one_way_batch_refuses_a_record_whose_names_nobody_can_read() -> None:
    request = _request(_never_opened, one_way=True, options=_NO_CLEAR)._replace(records=[{"a": "1"}, {"a": "2"}])

    with pytest.raises(PrintRefusedError) as refused:
        print_label(_LOGO_BATCH, request)

    assert FindingCode.BATCH_IMAGE_NAMES_UNREAD in {f.code for f in refused.value.findings}


@pytest.mark.os_agnostic
def test_a_batch_given_a_spool_reads_a_generator_of_records_once() -> None:
    def records() -> Iterator[dict[str, str]]:
        yield {"a": "1"}
        yield {"a": "2"}

    with FileJobSpool() as spool:
        request = _request(_opener(one_way=False), one_way=False, options=_NO_CLEAR)._replace(
            records=records(), spool=spool
        )
        printed = print_label(_LOGO_BATCH, request)

    assert isinstance(printed.result, BatchReport)
    assert printed.result.records == 2


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("uri", "one_way"),
    [("tcp://192.0.2.60:9100", False), ("serial:///dev/ttyS0", False), ("file:job.dpl", True), ("-", True)],
)
def test_a_uri_says_whether_its_channel_is_one_way(uri: str, one_way: bool) -> None:
    assert printer_uri_is_one_way(uri) is one_way


@pytest.mark.os_agnostic
def test_a_uri_that_is_no_printer_uri_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="nonsense"):
        printer_uri_is_one_way("nonsense://x")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["print_label", "PrintRequest", "PrintRefusedError", "printer_uri_is_one_way"])
def test_the_one_call_route_is_importable_from_the_root(name: str) -> None:
    assert name in datamax_dpl.__all__
