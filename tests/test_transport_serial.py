"""Serial transport: pyserial's ``loop://`` port everywhere, and a real pty pair on POSIX."""

from __future__ import annotations

import os
import select
import sys
import threading
import time
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.transport import open_printer
from datamax_dpl.adapters.transport.serial import SerialTransport
from datamax_dpl.adapters.transport.tcp import MAX_ANSWER_BYTES
from datamax_dpl.adapters.transport.uri import SerialUri
from datamax_dpl.domain.errors import PrinterProtocolError, PrinterTimeoutError, TransportError

if TYPE_CHECKING:
    from collections.abc import Iterator

LOOP = SerialUri(path="loop://")
JOIN_SECONDS = 5.0


# --- loop:// (every platform) -----------------------------------------------------------------


@pytest.mark.os_agnostic
def test_a_loop_port_echoes_a_query_back_as_its_answer() -> None:
    with SerialTransport.open(LOOP, timeout=1.0) as printer:
        assert printer.bidirectional
        assert printer.query(b"PING\r", timeout=1.0) == b"PING\r"


@pytest.mark.os_agnostic
def test_bytes_received_before_a_query_are_dropped() -> None:
    with SerialTransport.open(LOOP, timeout=1.0) as printer:
        printer.send(b"stale answer\r")
        assert printer.query(b"fresh\r", timeout=1.0) == b"fresh\r"


@pytest.mark.os_agnostic
def test_an_answer_without_terminator_times_out_within_the_timeout() -> None:
    with SerialTransport.open(LOOP, timeout=1.0) as printer:
        started = time.monotonic()
        with pytest.raises(PrinterTimeoutError, match="2 bytes read"):
            printer.query(b"\x01A", timeout=0.3)
        assert 0.25 <= time.monotonic() - started < 1.5


@pytest.mark.os_agnostic
def test_a_closed_port_refuses_and_closes_twice() -> None:
    printer = SerialTransport.open(LOOP, timeout=1.0)
    printer.close()
    printer.close()
    with pytest.raises(TransportError, match="serial loop://"):
        printer.send(b"x")
    with pytest.raises(TransportError, match="serial loop://"):
        printer.query(b"x\r", timeout=1.0)


@pytest.mark.os_agnostic
def test_query_arguments_are_checked() -> None:
    with SerialTransport.open(LOOP, timeout=1.0) as printer:
        with pytest.raises(ValueError, match="terminator must not be empty"):
            printer.query(b"x", timeout=1.0, terminator=b"")
        with pytest.raises(ValueError, match="timeout must be above 0"):
            printer.query(b"x", timeout=-1.0)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("path", ["bogus://nowhere", "/nonexistent-dir/ttyS99"])
def test_a_port_that_cannot_be_opened_is_a_transport_error(path: str) -> None:
    with pytest.raises(TransportError, match="cannot open serial"):
        SerialTransport.open(SerialUri(path=path), timeout=1.0)


@pytest.mark.os_agnostic
def test_open_printer_reports_a_missing_serial_port_as_a_transport_error() -> None:
    with pytest.raises(TransportError, match="cannot open serial /nonexistent-dir/ttyS99"):
        open_printer("serial:///nonexistent-dir/ttyS99?baud=19200", timeout=1.0)


# --- draining a real port's output queue ---------------------------------------------------------


class DrainingPort:
    """A port whose output queue empties at ``bytes_per_tick`` every ``tick`` seconds (0: never).

    ``flush`` fails at once: pyserial's own flush waits without any bound (``tcdrain`` on POSIX),
    which is the hang the transport must not reach.
    """

    def __init__(self, *, queued: int, bytes_per_tick: int, tick: float = 0.1) -> None:
        self.timeout: float | None = 1.0
        self._queued = queued
        self._bytes_per_tick = bytes_per_tick
        self._tick = tick
        self._started = time.monotonic()
        self.output_discarded = False

    @property
    def out_waiting(self) -> int:
        if self.output_discarded:
            return 0
        drained = int((time.monotonic() - self._started) / self._tick) * self._bytes_per_tick
        return max(0, self._queued - drained)

    def write(self, b: bytes, /) -> int | None:
        return len(b)

    def flush(self) -> None:
        raise AssertionError("pyserial's unbounded flush was called")

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        return b""

    def reset_input_buffer(self) -> None:
        return None

    def reset_output_buffer(self) -> None:
        self.output_discarded = True

    def close(self) -> None:
        return None


@pytest.mark.os_agnostic
def test_a_port_whose_output_never_drains_times_out_and_drops_the_queued_bytes() -> None:
    port = DrainingPort(queued=5, bytes_per_tick=0)
    printer = SerialTransport(port, uri=LOOP, drain_timeout=0.2)
    started = time.monotonic()

    with pytest.raises(PrinterTimeoutError, match=r"took no data for 0\.2 s"):
        printer.send(b"\x01A")

    assert time.monotonic() - started < 1.0
    assert port.output_discarded


@pytest.mark.os_agnostic
def test_a_port_that_drains_slower_than_the_timeout_but_steadily_does_not_time_out() -> None:
    # Five ticks of 0.1 s to empty, against a 0.25 s timeout: the clock restarts on every byte taken.
    port = DrainingPort(queued=5, bytes_per_tick=1)
    printer = SerialTransport(port, uri=LOOP, drain_timeout=0.25)

    printer.send(b"\x01A")

    assert port.out_waiting == 0
    assert not port.output_discarded


# --- a real pty pair (POSIX) -------------------------------------------------------------------


class PtyPrinter:
    """The master side of a pty pair, playing the printer; the slave side is the serial port."""

    def __init__(self, *, master: int, slave: int, path: str) -> None:
        self.master = master
        self.path = path
        # pyserial opens the slave by name; our own descriptor stays open until the fixture ends so
        # the master never reads EIO from a slave nobody holds between test steps.
        self._slave = slave
        self.received = bytearray()

    def read(self, count: int, *, deadline: float) -> bytes:
        while len(self.received) < count and time.monotonic() < deadline:
            readable, _, _ = select.select([self.master], [], [], 0.05)
            if readable:
                self.received += os.read(self.master, 65536)
        return bytes(self.received)

    def close(self) -> None:
        os.close(self._slave)
        os.close(self.master)


@pytest.fixture
def pty_printer() -> Iterator[PtyPrinter]:
    # The guard also tells the type checker, which checks this file for Windows too, that the
    # POSIX-only calls below never run there.
    if sys.platform == "win32":
        pytest.skip("os.openpty is POSIX only")
    master, slave = os.openpty()
    printer = PtyPrinter(master=master, slave=slave, path=os.ttyname(slave))
    yield printer
    printer.close()


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="os.openpty is POSIX only")
def test_bytes_sent_to_a_pty_arrive_identical(pty_printer: PtyPrinter) -> None:
    payload = bytes(range(256)) * 64
    with SerialTransport.open(SerialUri(path=pty_printer.path), timeout=2.0) as printer:
        reader = threading.Thread(
            target=pty_printer.read, args=(len(payload),), kwargs={"deadline": time.monotonic() + JOIN_SECONDS}
        )
        reader.start()
        printer.send(payload)
        reader.join(JOIN_SECONDS)
    assert bytes(pty_printer.received) == payload


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="os.openpty is POSIX only")
def test_a_pty_printer_answers_a_status_query(pty_printer: PtyPrinter) -> None:
    def answer() -> None:
        pty_printer.read(2, deadline=time.monotonic() + JOIN_SECONDS)
        os.write(pty_printer.master, b"NYNN")
        time.sleep(0.05)
        os.write(pty_printer.master, b"NNNN\r")

    uri = SerialUri(path=pty_printer.path, baud=19200)
    with SerialTransport.open(uri, timeout=2.0) as printer:
        thread = threading.Thread(target=answer)
        thread.start()
        assert printer.query(b"\x01A", timeout=1.5) == b"NYNNNNNN\r"
        thread.join(JOIN_SECONDS)
    assert bytes(pty_printer.received) == b"\x01A"


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="os.openpty is POSIX only")
def test_a_silent_pty_printer_times_out(pty_printer: PtyPrinter) -> None:
    with SerialTransport.open(SerialUri(path=pty_printer.path), timeout=1.0) as printer:
        started = time.monotonic()
        with pytest.raises(PrinterTimeoutError):
            printer.query(b"\x01A", timeout=0.3)
        assert time.monotonic() - started < 1.5


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="os.openpty is POSIX only")
def test_a_pty_nobody_reads_makes_a_send_time_out(pty_printer: PtyPrinter) -> None:
    with SerialTransport.open(SerialUri(path=pty_printer.path), timeout=0.3) as printer:
        started = time.monotonic()
        with pytest.raises(PrinterTimeoutError, match="write timed out"):
            printer.send(b"x" * (4 * 1024 * 1024))
        assert time.monotonic() - started < 1.5


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="os.openpty is POSIX only")
def test_a_pty_answer_longer_than_the_bound_is_a_protocol_error(pty_printer: PtyPrinter) -> None:
    def flood() -> None:
        pty_printer.read(2, deadline=time.monotonic() + JOIN_SECONDS)
        os.write(pty_printer.master, b"x" * (MAX_ANSWER_BYTES + 10))

    with SerialTransport.open(SerialUri(path=pty_printer.path, baud=115200), timeout=1.5) as printer:
        thread = threading.Thread(target=flood, daemon=True)
        thread.start()
        with pytest.raises(PrinterProtocolError, match="no answer terminator within"):
            printer.query(b"\x02KQ", timeout=1.5)
