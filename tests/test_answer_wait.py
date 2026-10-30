"""How the wait for a first answer ends: the seconds it reports and the last failure it quotes.

These run the use cases against a fake channel and a clock that moves only when the fake says so,
so a pause that overruns the wait, a connect that uses up the rest of it, or an answer wait capped
below it can be staged exactly; tests/test_status_wait.py drives the same loop over real sockets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from datamax_dpl.application.use_cases import AnswerWait, query_status, read_memory_directory
from datamax_dpl.domain.errors import PrinterTimeoutError, TransportError
from datamax_dpl.domain.timing import MAX_ANSWER_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamax_dpl.application.ports import PrinterTransport

PRINTER = "tcp://192.0.2.60:9100"
REFUSED = "cannot connect to tcp 192.0.2.60:9100: Connection refused"


class Clock:
    """Monotonic seconds that move only when a test or a fake moves them."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class SilentChannel:
    """A channel that takes a request and answers nothing: each wait for an answer runs out in full."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.closed = False

    @property
    def bidirectional(self) -> bool:
        return True

    def send(self, data: bytes) -> None:
        del data

    def query(
        self,
        request: bytes,
        *,
        timeout: float,
        terminator: bytes = b"\r",
        lines: int = 1,
        accept: Callable[[bytes], bool] | None = None,
    ) -> bytes:
        del request, terminator, lines, accept
        self._clock.now += timeout
        raise PrinterTimeoutError(f"silent: no complete answer within {timeout:g} s (0 bytes read)")

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = MAX_ANSWER_SECONDS) -> bytes:
        del request, idle
        # As the TCP transport does: the first byte is waited for at most ``total`` seconds.
        waited = min(timeout, total)
        self._clock.now += waited
        raise PrinterTimeoutError(f"silent: no complete answer within {waited:g} s (0 bytes read)")

    def close(self) -> None:
        self.closed = True


def refusing_then(*then: Callable[[], PrinterTransport]) -> Callable[..., PrinterTransport]:
    """An opener refusing the first connect, then running ``then`` in turn for each later one."""
    later = iter(then)
    calls = 0

    def open_printer(uri: str, *, timeout: float) -> PrinterTransport:
        del uri, timeout
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransportError(REFUSED, unanswered=True)
        return next(later)()

    return open_printer


@pytest.mark.os_agnostic
def test_a_pause_that_overruns_the_wait_starts_no_attempt_and_keeps_the_last_failure() -> None:
    clock = Clock()

    def overrunning_sleep(seconds: float, /) -> None:
        clock.now += seconds + 0.6

    wait = AnswerWait(seconds=1.0, retry_interval=0.5, clock=clock, sleep=overrunning_sleep)

    with pytest.raises(PrinterTimeoutError) as caught:
        query_status(uri=PRINTER, timeout=1.0, open_printer=refusing_then(), wait=wait)

    message = str(caught.value)
    assert message.startswith(f"{PRINTER}: no answer in 1.1 s (1 attempt);")
    assert message.endswith(f"Last failure: {REFUSED}")


@pytest.mark.os_agnostic
def test_a_connect_that_uses_up_the_wait_asks_nothing_closes_and_keeps_the_last_failure() -> None:
    clock = Clock()
    channel = SilentChannel(clock)

    def slow_connect() -> PrinterTransport:
        clock.now += 2.0
        return channel

    def sleep(seconds: float, /) -> None:
        clock.now += seconds

    wait = AnswerWait(seconds=1.0, retry_interval=0.25, clock=clock, sleep=sleep)

    with pytest.raises(PrinterTimeoutError) as caught:
        query_status(uri=PRINTER, timeout=1.0, open_printer=refusing_then(slow_connect), wait=wait)

    message = str(caught.value)
    assert message.startswith(f"{PRINTER}: no answer in 2.2 s (2 attempts);")
    assert message.endswith(f"Last failure: {REFUSED}")
    assert channel.closed


@pytest.mark.os_agnostic
def test_memory_reports_the_answer_wait_the_transport_capped_not_the_wait_asked_for() -> None:
    clock = Clock()
    channel = SilentChannel(clock)

    def open_channel(uri: str, *, timeout: float) -> PrinterTransport:
        del uri, timeout
        return channel

    def no_pause(seconds: float, /) -> None:
        del seconds

    wait = AnswerWait(seconds=300.0, clock=clock, sleep=no_pause)

    with pytest.raises(PrinterTimeoutError) as caught:
        read_memory_directory(kinds=("graphic",), uri=PRINTER, timeout=10.0, open_printer=open_channel, wait=wait)

    assert str(caught.value).startswith(f"{PRINTER}: no answer in {MAX_ANSWER_SECONDS:.1f} s (1 attempt);")
    assert channel.closed
