"""What a checked job's link holds of the channels it opened: the current one, however many it took."""

from __future__ import annotations

import gc
import weakref
from typing import TYPE_CHECKING, cast

import pytest

from datamax_dpl.application.ports import PrinterEndpoint
from datamax_dpl.application.upload_check import Link
from datamax_dpl.application.use_cases import AnswerWait

if TYPE_CHECKING:
    from datamax_dpl.application.ports import PrinterTransport


class _ClosingPrintServer:
    """A channel a print server has closed by the next write: each send needs a new one."""

    def __init__(self) -> None:
        self.closed = False

    def peer_closed(self) -> bool:
        return True

    def send(self, data: bytes) -> None:
        assert not self.closed
        assert data

    def close(self) -> None:
        self.closed = True


def _send_through_dropped_channels(sends: int) -> tuple[int, list[_ClosingPrintServer]]:
    """Send ``sends`` times over channels dropped each time; return how many stay alive, and the last."""
    alive: weakref.WeakSet[_ClosingPrintServer] = weakref.WeakSet()
    last: list[_ClosingPrintServer] = []

    def open_new(uri: str, *, timeout: float) -> PrinterTransport:
        assert (uri, timeout) == ("tcp://printer", 1.0)
        opened = _ClosingPrintServer()
        alive.add(opened)
        last[:] = [opened]
        return cast("PrinterTransport", opened)

    link = Link(
        printer=None,
        endpoint=PrinterEndpoint(uri="tcp://printer", timeout=1.0, open_printer=open_new),
        reconnect=AnswerWait(seconds=1.0),
    )
    for _ in range(sends):
        link.send(b"x")
    gc.collect()
    count = len(alive)
    link.close_opened()
    return count, last


@pytest.mark.os_agnostic
@pytest.mark.parametrize("sends", [50, 500])
def test_a_link_holds_one_opened_channel_however_many_it_took(sends: int) -> None:
    alive, last = _send_through_dropped_channels(sends)
    assert alive <= 2, f"{alive} channels held after {sends} sends"
    assert [channel.closed for channel in last] == [True], "the last one is closed with the link"
