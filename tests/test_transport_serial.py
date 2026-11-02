"""Serial transport: pyserial's ``loop://`` port everywhere, and a real pty pair on POSIX."""

from __future__ import annotations

import contextlib
import errno
import os
import re
import select
import sys
import threading
import time
from typing import TYPE_CHECKING

import pytest
import serial

from datamax_dpl.adapters.transport import open_printer
from datamax_dpl.adapters.transport.serial import SerialTransport
from datamax_dpl.adapters.transport.tcp import MAX_ANSWER_BYTES, MAX_IDLE_ANSWER_BYTES
from datamax_dpl.adapters.transport.uri import SerialUri
from datamax_dpl.application.use_cases import MEMORY_REPORT_LINES, detect_printer
from datamax_dpl.domain.errors import AnswerNotEndedError, PrinterProtocolError, PrinterTimeoutError, TransportError
from datamax_dpl.domain.status import is_status_line
from datamax_dpl.domain.timing import SEND_CHUNK_BYTES, TransportTuning

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

LOOP = SerialUri(path="loop://")
JOIN_SECONDS = 5.0
#: The manual's sample answer to ``<STX>WG`` (see tests/test_memory.py).
DIRECTORY = b"MODULE: A\rAVAILABLE BYTES IN MODULE:00049083\rMODULE: B\rLOGO\rCAM\rAVAILABLE BYTES IN MODULE:00257919\r"


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
        with pytest.raises(PrinterTimeoutError, match=r"no complete answer within 0\.3 s \(2 bytes read\)"):
            printer.query(b"\x01A", timeout=0.3)
        assert 0.25 <= time.monotonic() - started < 1.5


@pytest.mark.os_agnostic
def test_a_loop_port_skips_echoed_lines_the_caller_does_not_accept() -> None:
    with SerialTransport.open(LOOP, timeout=1.0) as printer:
        echoed = printer.query(b"MODULE: A\rNNNNNNNN\r", timeout=1.0, accept=is_status_line)
    assert echoed == b"NNNNNNNN\r"


@pytest.mark.os_agnostic
def test_a_loop_port_that_answers_fewer_lines_than_asked_times_out() -> None:
    with SerialTransport.open(LOOP, timeout=1.0) as printer:
        started = time.monotonic()
        with pytest.raises(PrinterTimeoutError) as caught:
            printer.query(b"A\r", timeout=0.3, lines=2)
        assert 0.25 <= time.monotonic() - started < 1.5
    assert str(caught.value) == "serial loop://: no complete answer within 0.3 s (2 bytes read)"


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
def test_read_answer_on_a_loop_port_returns_the_echo_after_the_silence() -> None:
    with SerialTransport.open(LOOP, timeout=1.0) as printer:
        printer.send(b"stale answer\r")
        assert printer.read_answer(DIRECTORY, timeout=1.0, idle=0.3) == DIRECTORY


@pytest.mark.os_agnostic
def test_read_answer_with_no_byte_at_all_times_out_within_the_timeout() -> None:
    with SerialTransport.open(LOOP, timeout=1.0) as printer:
        started = time.monotonic()
        with pytest.raises(PrinterTimeoutError, match="no complete answer within 1 s"):
            printer.read_answer(b"", timeout=1.0, idle=0.2)
        assert 0.7 <= time.monotonic() - started < 2.5


@pytest.mark.os_agnostic
def test_read_answer_arguments_are_checked_and_a_closed_port_refuses() -> None:
    printer = SerialTransport.open(LOOP, timeout=1.0)
    with pytest.raises(ValueError, match="idle must be above 0"):
        printer.read_answer(b"x", timeout=1.0, idle=-1.0)
    with pytest.raises(ValueError, match="timeout must be above 0"):
        printer.read_answer(b"x", timeout=0.0, idle=0.5)
    with pytest.raises(ValueError, match="total must be above 0"):
        printer.read_answer(b"x", timeout=1.0, idle=0.5, total=-1.0)
    printer.close()
    with pytest.raises(TransportError, match="cannot read from serial"):
        printer.read_answer(b"x", timeout=1.0, idle=0.5)


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

    def read(self, size: int = 1) -> bytes:
        return b""

    @property
    def in_waiting(self) -> int:
        return 0

    def reset_input_buffer(self) -> None:
        return None

    def reset_output_buffer(self) -> None:
        self.output_discarded = True

    def close(self) -> None:
        return None


class ScriptedPort(DrainingPort):
    """A port with nothing queued whose input holds ``pending``; reads end at once when it is used up."""

    def __init__(self, pending: bytes) -> None:
        super().__init__(queued=0, bytes_per_tick=0)
        self.pending = pending

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        end = self.pending.find(expected)
        take = len(self.pending) if end < 0 else end + len(expected)
        if size is not None:
            take = min(take, size)
        chunk, self.pending = self.pending[:take], self.pending[take:]
        return chunk


class AnsweringPort(ScriptedPort):
    """A port that answers each written request from a table, as a printer on the line would."""

    def __init__(self, answers: Mapping[bytes, bytes]) -> None:
        super().__init__(b"")
        self._answers = dict(answers)

    def write(self, b: bytes, /) -> int | None:
        self.pending += self._answers.get(bytes(b), b"")
        return len(b)

    def reset_input_buffer(self) -> None:
        self.pending = b""


class PieceRecordingPort(DrainingPort):
    """A port with nothing queued that records the size of every write; write number ``stall_at`` (from 1) fails.

    It fails with ``error``, by default pyserial's own error for a line held past write_timeout.
    """

    def __init__(self, *, stall_at: int = 0, error: Exception | None = None) -> None:
        super().__init__(queued=0, bytes_per_tick=0)
        self.sizes: list[int] = []
        self._stall_at = stall_at
        self._error = serial.SerialTimeoutException("Write timeout") if error is None else error

    def write(self, b: bytes, /) -> int | None:
        if len(self.sizes) + 1 == self._stall_at:
            raise self._error
        self.sizes.append(len(b))
        return len(b)


class VanishingPort(DrainingPort):
    """A port that takes every write and then fails to say what it still holds, as a pulled USB adapter does."""

    @property
    def out_waiting(self) -> int:
        raise OSError(errno.EIO, "Input/output error")


#: The advice of a job whose write failed before a whole piece was known to have gone.
PARTLY_WRITTEN = (
    "the job may have been partly written, so the printer may be waiting for the rest of it: power-cycle it"
)
#: The advice of a 1000-byte job whose third 100-byte piece failed.
TWO_PIECES_WRITTEN = (
    "at least 200 of 1000 bytes were written, so the printer may be waiting for the rest of the job: power-cycle it"
)
#: A pulled USB serial adapter: pyserial passes the OS error on.
PULLED = OSError(errno.EIO, "Input/output error")


@pytest.mark.os_agnostic
def test_a_serial_job_is_written_in_pieces_no_larger_than_the_send_chunk() -> None:
    """pyserial's write timeout bounds a whole write call, so pieces make it a no-data bound, as on tcp."""
    port = PieceRecordingPort()
    printer = SerialTransport(port, uri=LOOP)

    printer.send(b"x" * (3 * SEND_CHUNK_BYTES + 5))

    assert port.sizes == [SEND_CHUNK_BYTES] * 3 + [5]


@pytest.mark.os_agnostic
def test_a_serial_job_takes_the_configured_piece_size() -> None:
    port = PieceRecordingPort()
    printer = SerialTransport(port, uri=LOOP, tuning=TransportTuning(send_chunk_bytes=100))

    printer.send(b"x" * 250)

    assert port.sizes == [100, 100, 50]


@pytest.mark.os_agnostic
def test_a_serial_job_cut_off_part_way_advises_a_power_cycle_and_drops_the_rest() -> None:
    port = PieceRecordingPort(stall_at=3)
    printer = SerialTransport(port, uri=LOOP, tuning=TransportTuning(send_chunk_bytes=100))

    with pytest.raises(PrinterTimeoutError) as caught:
        printer.send(b"x" * 1000)

    assert str(caught.value) == (
        "serial loop://: write timed out; at least 200 of 1000 bytes were written, "
        "so the printer may be waiting for the rest of the job: power-cycle it"
    )
    assert port.output_discarded


@pytest.mark.os_agnostic
def test_a_serial_write_that_times_out_in_the_first_piece_advises_a_power_cycle() -> None:
    """pyserial does not say how much of the timed-out write left the port, so part of it may have."""
    port = PieceRecordingPort(stall_at=1)
    printer = SerialTransport(port, uri=LOOP)

    with pytest.raises(PrinterTimeoutError) as caught:
        printer.send(b"x" * 1000)

    assert str(caught.value) == f"serial loop://: write timed out; {PARTLY_WRITTEN}"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("stall_at", "advice"),
    [
        (3, TWO_PIECES_WRITTEN),
        (1, PARTLY_WRITTEN),
    ],
    ids=["after-two-pieces", "in-the-first-piece"],
)
def test_a_serial_port_failing_while_writing_a_job_advises_a_power_cycle(stall_at: int, advice: str) -> None:
    """A pulled adapter cuts a job off as a held line does."""
    port = PieceRecordingPort(stall_at=stall_at, error=PULLED)
    printer = SerialTransport(port, uri=LOOP, tuning=TransportTuning(send_chunk_bytes=100))

    with pytest.raises(TransportError) as caught:
        printer.send(b"x" * 1000)

    assert type(caught.value) is TransportError
    assert caught.value.advice == advice


@pytest.mark.os_agnostic
def test_a_serial_port_failing_while_its_job_drains_advises_a_power_cycle() -> None:
    printer = SerialTransport(VanishingPort(queued=0, bytes_per_tick=0), uri=LOOP, drain_timeout=0.2)

    with pytest.raises(TransportError) as caught:
        printer.send(b"x" * 8)

    assert caught.value.advice == PARTLY_WRITTEN


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "port",
    [
        PieceRecordingPort(stall_at=1),
        PieceRecordingPort(stall_at=1, error=PULLED),
        DrainingPort(queued=5, bytes_per_tick=0),
        VanishingPort(queued=0, bytes_per_tick=0),
    ],
    ids=["write-timeout", "write-fails", "drain-timeout", "drain-fails"],
)
def test_a_serial_request_that_fails_to_go_out_advises_no_power_cycle(port: DrainingPort) -> None:
    """A request is a few bytes, never a job a printer could be left waiting on."""
    printer = SerialTransport(port, uri=LOOP, drain_timeout=0.2)

    with pytest.raises(TransportError) as caught:
        printer.query(b"\x01A", timeout=0.3)

    assert caught.value.advice == ""


@pytest.mark.os_agnostic
def test_a_serial_job_whose_queue_stops_draining_part_way_advises_a_power_cycle() -> None:
    # Five bytes stay queued: three of the eight left the port before it stopped.
    port = DrainingPort(queued=5, bytes_per_tick=0)
    printer = SerialTransport(port, uri=LOOP, drain_timeout=0.2)

    with pytest.raises(PrinterTimeoutError) as caught:
        printer.send(b"x" * 8)

    assert caught.value.advice == (
        "at least 3 of 8 bytes were written, so the printer may be waiting for the rest of the job: power-cycle it"
    )


@pytest.mark.os_agnostic
def test_a_serial_answer_cut_short_after_a_skipped_line_times_out_as_tcp_does() -> None:
    # The same answer over tcp ends in the same words (test_transport_tcp.py).
    printer = SerialTransport(ScriptedPort(b"VER: 2.03\rMODULE: A\r"), uri=LOOP)
    with pytest.raises(PrinterTimeoutError) as caught:
        printer.query(b"\x02KQ", timeout=0.3, lines=2, accept=lambda line: not line.startswith(b"MODULE"))
    assert str(caught.value) == (
        "serial loop://: no complete answer within 0.3 s (20 bytes read; 1 line skipped, last b'MODULE: A\\r')"
    )


@pytest.mark.os_agnostic
def test_detect_over_serial_ignores_a_memory_report_cut_short() -> None:
    # Three of the eight <STX>KQ lines, then silence: half a report is no evidence.
    port = AnsweringPort(
        {
            b"\x02k": b"Y\r",
            b"\x02v": b"VER DA - 01.01 12/21/93\r",
            b"\x02KQ": b"INTERNAL MEMORY\rVER: 2.03\rLABEL SIZE: 0400:0600:IN\r",
        }
    )

    def open_port(uri: str, *, timeout: float) -> SerialTransport:
        return SerialTransport(port, uri=LOOP)

    report = detect_printer(uri="serial://loop", timeout=0.3, open_printer=open_port)

    assert MEMORY_REPORT_LINES == 8
    assert (report.evidence.serial_test, report.evidence.version) == (True, "VER DA - 01.01 12/21/93")
    assert report.evidence.memory is None


@pytest.mark.os_agnostic
def test_a_query_skips_a_stale_memory_line_before_the_status() -> None:
    port = ScriptedPort(b"AVAILABLE BYTES IN MODULE : 00048631\rNNNNNNNN\r")
    printer = SerialTransport(port, uri=LOOP)
    assert printer.query(b"\x01A", timeout=1.0, accept=is_status_line) == b"NNNNNNNN\r"


@pytest.mark.os_agnostic
def test_skipped_serial_lines_are_bounded_like_an_answer() -> None:
    port = ScriptedPort(b"MODULE: A\r" * (70 * 1024 // 10))
    printer = SerialTransport(port, uri=LOOP)
    expected = rf"no complete answer within {MAX_ANSWER_BYTES} bytes \(6553 lines skipped, last b'MODULE: A\\r'\)"
    with pytest.raises(PrinterProtocolError, match=expected):
        printer.query(b"\x01A", timeout=1.0, accept=is_status_line)
    assert port.pending, "the transport read past its bound"


@pytest.mark.os_agnostic
def test_a_malformed_serial_status_line_is_quoted_in_the_timeout() -> None:
    printer = SerialTransport(ScriptedPort(b"MODULE: A\rNNNNNNN\r"), uri=LOOP)
    with pytest.raises(PrinterTimeoutError) as caught:
        printer.query(b"\x01A", timeout=0.3, accept=is_status_line)
    assert str(caught.value) == (
        "serial loop://: no complete answer within 0.3 s (18 bytes read; 2 lines skipped, last b'NNNNNNN\\r')"
    )


@pytest.mark.os_agnostic
def test_flow_control_alone_on_serial_still_earns_the_earlier_job_hint() -> None:
    printer = SerialTransport(ScriptedPort(b"\x11"), uri=LOOP)
    with pytest.raises(PrinterTimeoutError) as caught:
        printer.query(b"\x01A", timeout=0.3, accept=is_status_line)
    assert str(caught.value).endswith(
        "(1 bytes read); the printer may still be printing an earlier job; try again or raise the timeout"
    )


@pytest.mark.os_agnostic
def test_a_serial_printer_sending_nothing_is_told_it_may_still_be_printing() -> None:
    printer = SerialTransport(ScriptedPort(b""), uri=LOOP)
    with pytest.raises(PrinterTimeoutError, match=r"\(0 bytes read\)") as caught:
        printer.query(b"\x01A", timeout=0.3, accept=is_status_line)
    assert str(caught.value).endswith(
        "(0 bytes read); the printer may still be printing an earlier job; try again or raise the timeout"
    )


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
    # A silent printer still takes the request off the line and only never answers. A pty master
    # nobody reads models a printer that takes no data at all: macOS then keeps the request in the
    # slave's output queue, and the send times out instead of the answer (seen on a CI cell).
    with SerialTransport.open(SerialUri(path=pty_printer.path), timeout=1.0) as printer:
        reader = threading.Thread(
            target=pty_printer.read, args=(2,), kwargs={"deadline": time.monotonic() + JOIN_SECONDS}
        )
        reader.start()
        started = time.monotonic()
        with pytest.raises(PrinterTimeoutError, match=r"no complete answer within 0\.3 s"):
            printer.query(b"\x01A", timeout=0.3)
        assert time.monotonic() - started < 1.5
        reader.join(JOIN_SECONDS)
    assert bytes(pty_printer.received) == b"\x01A"


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
def test_open_printer_paces_a_serial_job_by_the_tuning_it_is_given(pty_printer: PtyPrinter) -> None:
    """Only whole pieces count as written, so the count shows the piece size the transport used."""
    # 97 shares no factor with the default 1024 (see the tcp test of the same name), and is far
    # below the about 1 KB a macOS pty holds, so whole pieces go before the write times out.
    tuning = TransportTuning(send_chunk_bytes=97)
    with (
        contextlib.closing(open_printer(f"serial://{pty_printer.path}", timeout=0.3, tuning=tuning)) as printer,
        pytest.raises(PrinterTimeoutError) as caught,
    ):
        printer.send(b"x" * (4 * 1024 * 1024))

    written = re.search(r"; at least ([0-9]+) of [0-9]+ bytes were written", str(caught.value))
    assert written is not None, str(caught.value)
    assert int(written.group(1)) > 0
    assert int(written.group(1)) % 97 == 0


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="os.openpty is POSIX only")
def test_a_pty_answer_longer_than_the_bound_is_a_protocol_error(pty_printer: PtyPrinter) -> None:
    def flood() -> None:
        pty_printer.read(2, deadline=time.monotonic() + JOIN_SECONDS)
        os.write(pty_printer.master, b"x" * (MAX_ANSWER_BYTES + 10))

    # The size bound is what ends this read, so the timeout only guards against a wedge. At 1.5 s a
    # starved runner lost the race: PrinterTimeoutError with 37776 of the bytes read.
    with SerialTransport.open(SerialUri(path=pty_printer.path, baud=115200), timeout=30.0) as printer:
        thread = threading.Thread(target=flood, daemon=True)
        thread.start()
        with pytest.raises(PrinterProtocolError, match="no answer terminator within"):
            printer.query(b"\x02KQ", timeout=30.0)


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="os.openpty is POSIX only")
def test_a_pty_directory_in_pieces_is_joined_and_ends_at_the_silence(pty_printer: PtyPrinter) -> None:
    def answer() -> None:
        pty_printer.read(3, deadline=time.monotonic() + JOIN_SECONDS)
        for line in DIRECTORY.split(b"\r")[:-1]:
            os.write(pty_printer.master, line + b"\r")
            time.sleep(0.02)
        time.sleep(1.2)
        os.write(pty_printer.master, b"LATE\r")

    with SerialTransport.open(SerialUri(path=pty_printer.path, baud=115200), timeout=2.0) as printer:
        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        assert printer.read_answer(b"\x02WG", timeout=2.0, idle=0.4) == DIRECTORY
        thread.join(JOIN_SECONDS)
    assert bytes(pty_printer.received) == b"\x02WG"


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="os.openpty is POSIX only")
def test_a_pty_answer_longer_than_the_read_answer_bound_is_refused(pty_printer: PtyPrinter) -> None:
    def flood() -> None:
        pty_printer.read(3, deadline=time.monotonic() + JOIN_SECONDS)
        rest = memoryview(b"x" * (MAX_IDLE_ANSWER_BYTES + 10))
        while rest:
            rest = rest[os.write(pty_printer.master, rest) :]

    with SerialTransport.open(SerialUri(path=pty_printer.path, baud=115200), timeout=1.5) as printer:
        thread = threading.Thread(target=flood, daemon=True)
        thread.start()
        with pytest.raises(PrinterProtocolError, match=f"answer longer than {MAX_IDLE_ANSWER_BYTES} bytes"):
            printer.read_answer(b"\x02WG", timeout=1.5, idle=0.5)


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="os.openpty is POSIX only")
def test_a_pty_printer_that_never_stops_dripping_ends_at_the_total_deadline(pty_printer: PtyPrinter) -> None:
    stop = threading.Event()

    def drip() -> None:
        pty_printer.read(3, deadline=time.monotonic() + JOIN_SECONDS)
        # One byte every 0.1 s, well inside idle=0.5: only the total deadline can end the read.
        while not stop.wait(0.1):
            os.write(pty_printer.master, b"x")

    with SerialTransport.open(SerialUri(path=pty_printer.path, baud=115200), timeout=2.0) as printer:
        thread = threading.Thread(target=drip, daemon=True)
        thread.start()
        started = time.monotonic()
        try:
            with pytest.raises(AnswerNotEndedError, match="the answer did not end within 1 s"):
                printer.read_answer(b"\x02WG", timeout=1.0, idle=0.5, total=1.0)
        finally:
            stop.set()
            thread.join(JOIN_SECONDS)
        assert 0.7 <= time.monotonic() - started < 2.5


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="os.openpty is POSIX only")
def test_a_pty_lone_xon_does_not_start_the_idle_clock(pty_printer: PtyPrinter) -> None:
    def answer() -> None:
        pty_printer.read(3, deadline=time.monotonic() + JOIN_SECONDS)
        os.write(pty_printer.master, b"\x11")
        time.sleep(0.9)
        os.write(pty_printer.master, DIRECTORY)

    with SerialTransport.open(SerialUri(path=pty_printer.path, baud=115200), timeout=2.0) as printer:
        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        assert printer.read_answer(b"\x02WG", timeout=3.0, idle=0.3) == b"\x11" + DIRECTORY
        thread.join(JOIN_SECONDS)
