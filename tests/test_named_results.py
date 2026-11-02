"""The application and adapter helpers return named records, never bare tuples.

A frozen dataclass does not compare equal to the tuple it replaced, so a caller still unpacking
one fails at once; these tests also check each record's type and read its fields by name, so a
return that quietly went back to a tuple shows here first.
"""

from __future__ import annotations

import pytest

from datamax_dpl.adapters.cli.config_load import ConfigLoad, load_config, start_logging
from datamax_dpl.adapters.cli.context import TracebackState, snapshot_traceback_state
from datamax_dpl.adapters.config.overrides import NestedOverrides, nest_overrides
from datamax_dpl.adapters.memory import PrinterOpen, RecordingPrinter, open_recording_printer
from datamax_dpl.application.upload_check import CheckedJob, CheckedSend, Link, UploadCheck, send_checked
from datamax_dpl.application.use_cases import (
    ONE_WAY_LINK,
    AnswerWait,
    RenderedJob,
    StatusPoll,
    StoredNamesRead,
    poll_status,
    read_stored_names,
    record_renders,
    render_label,
    render_with_stored_names,
)
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.enums import ImageModule, MemoryKind
from datamax_dpl.domain.held_images import HeldImages
from datamax_dpl.domain.label import Label, Text
from datamax_dpl.domain.render import RenderResult
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.status import MEMORY_DIRECTORY_REQUESTS, STATUS_REQUEST

URI = "tcp://192.0.2.60:9100"
WG = MEMORY_DIRECTORY_REQUESTS[MemoryKind.GRAPHIC]
LISTING = b"MODULE: A\rLOGO\rAVAILABLE BYTES IN MODULE: 00049083\r"
HELLO = Label(elements=[Text(x=5, y=5, text="Hello")])
OPTIONS = RenderOptions(model="allegro-2")


@pytest.mark.os_agnostic
def test_read_stored_names_returns_the_channel_that_answered_and_what_it_listed() -> None:
    printer = RecordingPrinter(answers={WG: LISTING})

    read = read_stored_names(
        printer, module=ImageModule.A, uri=URI, timeout=1.0, open_printer=open_recording_printer(printer)
    )

    assert type(read) is StoredNamesRead
    assert read.channel is printer
    assert read.held.listed == frozenset({"LOGO"})


@pytest.mark.os_agnostic
def test_read_stored_names_on_a_one_way_link_returns_no_channel_and_the_reason() -> None:
    printer = RecordingPrinter(bidirectional=False)

    read = read_stored_names(
        None, module=ImageModule.A, uri=URI, timeout=1.0, open_printer=open_recording_printer(printer)
    )

    assert type(read) is StoredNamesRead
    assert read.channel is None
    assert read.held.unread_because == ONE_WAY_LINK


@pytest.mark.os_agnostic
def test_poll_status_returns_the_channel_and_the_status_by_name() -> None:
    printer = RecordingPrinter(answers={STATUS_REQUEST: b"NNNNNNNN\r"})
    wait = AnswerWait(seconds=1.0)

    poll = poll_status(
        printer, uri=URI, timeout=1.0, open_printer=open_recording_printer(printer), wait=wait, started=wait.clock()
    )

    assert type(poll) is StatusPoll
    assert poll.channel is printer
    assert poll.status.idle


@pytest.mark.os_agnostic
def test_render_with_stored_names_reads_nothing_for_a_job_that_downloads_nothing() -> None:
    reads: list[ImageModule] = []

    def read(module: ImageModule) -> HeldImages:
        reads.append(module)
        return HeldImages()

    rendered = render_with_stored_names(lambda held: render_label(HELLO, OPTIONS, held=held), read=read)

    assert type(rendered) is RenderedJob
    assert isinstance(rendered.result, RenderResult)
    assert rendered.result.ok
    assert (rendered.held, reads) == (None, [])


@pytest.mark.os_agnostic
def test_send_checked_returns_the_job_as_sent_and_whether_every_label_went() -> None:
    printer = RecordingPrinter()
    opener = open_recording_printer(printer)
    result = render_label(HELLO, OPTIONS)
    job = CheckedJob(renders=record_renders(HELLO, None, OPTIONS), check=UploadCheck(wait=AnswerWait(seconds=1.0)))
    link = Link(printer=opener(URI, timeout=1.0), uri=URI, timeout=1.0, open_printer=opener, reconnect=None)

    send = send_checked(link, result, job)

    assert type(send) is CheckedSend
    assert send.all_sent is True
    assert isinstance(send.result, RenderResult)
    assert printer.data == result.data


@pytest.mark.os_agnostic
def test_load_config_and_start_logging_return_one_named_result() -> None:
    services = build_testing()

    loaded = load_config(services, profile=None, env_file=None, set_overrides=("a.b=1",))
    started = start_logging(services, loaded, env_file=None)

    assert type(loaded) is ConfigLoad
    assert type(started) is ConfigLoad
    assert (started.config.get("a"), started.error) == ({"b": 1}, None)


@pytest.mark.os_agnostic
def test_nest_overrides_names_the_tree_and_the_keys() -> None:
    nested = nest_overrides(("a.b=1", "a.c.d=x"))

    assert type(nested) is NestedOverrides
    assert nested.tree == {"a": {"b": 1, "c": {"d": "x"}}}
    assert nested.keys == frozenset({"a.b", "a.c.d"})


@pytest.mark.os_agnostic
def test_the_traceback_snapshot_names_both_flags(managed_traceback_state: None) -> None:
    state = snapshot_traceback_state()

    assert type(state) is TracebackState
    assert (state.enabled, state.force_color) == (False, False)


@pytest.mark.os_agnostic
def test_every_open_of_the_recording_printer_is_recorded_by_name() -> None:
    printer = RecordingPrinter()

    open_recording_printer(printer)(URI, timeout=2.5)

    [opened] = printer.opened
    assert type(opened) is PrinterOpen
    assert (opened.uri, opened.timeout) == (URI, 2.5)
