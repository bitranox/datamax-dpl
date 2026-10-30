"""Raw TCP transport against a real socket server on 127.0.0.1."""

from __future__ import annotations

import contextlib
import select
import socket
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from printer_server import JOIN_SECONDS, answering, silent

from datamax_dpl.adapters.transport import open_printer
from datamax_dpl.adapters.transport.tcp import (
    MAX_ANSWER_BYTES,
    MAX_IDLE_ANSWER_BYTES,
    REFUSED_RETRY_SECONDS,
    SEND_CHUNK_BYTES,
    TcpTransport,
)
from datamax_dpl.adapters.transport.uri import TcpUri
from datamax_dpl.domain.errors import PrinterProtocolError, PrinterTimeoutError, TransportError
from datamax_dpl.domain.status import is_status_line

if TYPE_CHECKING:
    from collections.abc import Callable

    from _typeshed import ReadableBuffer
    from printer_server import PrinterServer


@pytest.mark.os_agnostic
def test_sent_bytes_arrive_identical(serve: Callable[..., PrinterServer]) -> None:
    server = serve(silent)
    payload = bytes(range(256)) * 300 + b"\x02L\rD11\r\x0dE\r"
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        printer.send(payload[:1000])
        printer.send(payload[1000:])
    server.join()
    assert bytes(server.received) == payload


@pytest.mark.os_agnostic
def test_a_query_returns_the_answer_with_its_terminator(serve: Callable[..., PrinterServer]) -> None:
    server = serve(answering(b"\x01A", b"NNNNNNNN\rtrailing"))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        assert printer.bidirectional
        assert printer.query(b"\x01A", timeout=1.0) == b"NNNNNNNN\r"
    server.join()
    assert bytes(server.received) == b"\x01A"


@pytest.mark.os_agnostic
def test_an_answer_split_over_several_packets_is_joined(serve: Callable[..., PrinterServer]) -> None:
    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, 2)
        for part in (b"VER ", b"DA - 01", b".01\r\n"):
            connection.sendall(part)
            time.sleep(0.02)
        server.read_to_end(connection)

    server = serve(play)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        assert printer.query(b"\x02v", timeout=1.0, terminator=b"\r\n") == b"VER DA - 01.01\r\n"


@pytest.mark.os_agnostic
def test_a_printer_that_never_answers_times_out_within_the_timeout(serve: Callable[..., PrinterServer]) -> None:
    server = serve(silent)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        started = time.monotonic()
        with pytest.raises(PrinterTimeoutError, match=r"no complete answer within 0\.3 s \(0 bytes read\)") as caught:
            printer.query(b"\x01A", timeout=0.3)
        elapsed = time.monotonic() - started
    assert 0.25 <= elapsed < 1.5
    assert str(caught.value).endswith(
        "(0 bytes read); the printer may still be printing an earlier job; try again or raise the timeout"
    )


@pytest.mark.os_agnostic
def test_a_query_skips_whole_lines_the_caller_does_not_accept(serve: Callable[..., PrinterServer]) -> None:
    # Captured through a print server: a status request answered first by lines left over from an
    # earlier memory query, which arrive after the request and so are not dropped before it.
    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, 2)
        connection.sendall(b"MODULE: A\r")
        time.sleep(0.02)
        connection.sendall(b"AVAILABLE BYTES IN MODULE : 00048631\rNNNNNNNN\rtrailing")
        server.read_to_end(connection)

    server = serve(play)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        assert printer.query(b"\x01A", timeout=1.0, accept=is_status_line) == b"NNNNNNNN\r"


@pytest.mark.os_agnostic
def test_a_line_refused_between_kept_lines_is_cut_out_of_a_multi_line_answer(
    serve: Callable[..., PrinterServer],
) -> None:
    server = serve(answering(b"\x02KQ", b"VER: 2.03\rMODULE: A\rINSTALLED: 1\rLABEL MEM: 0\r"))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        answer = printer.query(b"\x02KQ", timeout=1.0, lines=2, accept=lambda line: not line.startswith(b"MODULE"))
    assert answer == b"VER: 2.03\rINSTALLED: 1\r"


@pytest.mark.os_agnostic
def test_an_answer_cut_short_after_a_skipped_line_times_out(serve: Callable[..., PrinterServer]) -> None:
    # The serial transport ends in the same words (test_transport_serial.py).
    server = serve(answering(b"\x02KQ", b"VER: 2.03\rMODULE: A\r"))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer, pytest.raises(PrinterTimeoutError) as caught:
        printer.query(b"\x02KQ", timeout=0.3, lines=2, accept=lambda line: not line.startswith(b"MODULE"))
    assert str(caught.value) == (
        f"tcp {server.uri.target}: no complete answer within 0.3 s"
        " (20 bytes read; 1 line skipped, last b'MODULE: A\\r')"
    )


@pytest.mark.os_agnostic
def test_without_accept_a_stale_line_is_the_answer(serve: Callable[..., PrinterServer]) -> None:
    server = serve(answering(b"\x02KQ", b"MODULE: A\rNNNNNNNN\r"))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        assert printer.query(b"\x02KQ", timeout=1.0) == b"MODULE: A\r"


@pytest.mark.os_agnostic
def test_skipped_lines_then_silence_time_out_counting_the_skipped_bytes(serve: Callable[..., PrinterServer]) -> None:
    server = serve(answering(b"\x01A", b"MODULE: A\r"))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer, pytest.raises(PrinterTimeoutError) as caught:
        printer.query(b"\x01A", timeout=0.3, accept=is_status_line)
    assert str(caught.value).endswith(
        "no complete answer within 0.3 s (10 bytes read; 1 line skipped, last b'MODULE: A\\r')"
    )


@pytest.mark.os_agnostic
def test_a_malformed_status_line_is_quoted_in_the_timeout(serve: Callable[..., PrinterServer]) -> None:
    # Seven flags are not a status answer, so the line is skipped; the timeout still shows it.
    server = serve(answering(b"\x01A", b"NNNNNNN\rnnnnnnnn\r"))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer, pytest.raises(PrinterTimeoutError) as caught:
        printer.query(b"\x01A", timeout=0.3, accept=is_status_line)
    assert str(caught.value).endswith("(17 bytes read; 2 lines skipped, last b'nnnnnnnn\\r')")


@pytest.mark.os_agnostic
def test_a_long_skipped_line_is_quoted_short(serve: Callable[..., PrinterServer]) -> None:
    server = serve(answering(b"\x01A", b"x" * 5000 + b"\r"))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer, pytest.raises(PrinterTimeoutError) as caught:
        printer.query(b"\x01A", timeout=0.3, accept=is_status_line)
    message = str(caught.value)
    assert "1 line skipped, last b'xxxx" in message
    assert "(5001 bytes))" in message
    assert len(message) < 300


@pytest.mark.os_agnostic
def test_flow_control_alone_still_earns_the_earlier_job_hint(serve: Callable[..., PrinterServer]) -> None:
    server = serve(answering(b"\x01A", b"\x11\x13"))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer, pytest.raises(PrinterTimeoutError) as caught:
        printer.query(b"\x01A", timeout=0.3, accept=is_status_line)
    assert str(caught.value).endswith(
        "(2 bytes read); the printer may still be printing an earlier job; try again or raise the timeout"
    )


@pytest.mark.os_agnostic
def test_skipped_lines_are_bounded_like_an_answer(serve: Callable[..., PrinterServer]) -> None:
    stale = b"MODULE: A\r" * (70 * 1024 // 10)
    server = serve(answering(b"\x01A", stale))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        started = time.monotonic()
        expected = rf"no complete answer within {MAX_ANSWER_BYTES} bytes \(\d+ lines skipped, last b'MODULE: A\\r'\)"
        with pytest.raises(PrinterProtocolError, match=expected):
            printer.query(b"\x01A", timeout=5.0, accept=is_status_line)
        assert time.monotonic() - started < 2.5


@pytest.mark.os_agnostic
def test_a_partial_answer_then_silence_times_out(serve: Callable[..., PrinterServer]) -> None:
    server = serve(answering(b"\x01A", b"NNNN"))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer, pytest.raises(PrinterTimeoutError, match="4 bytes"):
        printer.query(b"\x01A", timeout=0.3)


@pytest.mark.os_agnostic
def test_an_answer_longer_than_the_bound_is_a_protocol_error(serve: Callable[..., PrinterServer]) -> None:
    server = serve(answering(b"\x02KQ", b"x" * (MAX_ANSWER_BYTES + 10)))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer, pytest.raises(PrinterProtocolError):
        printer.query(b"\x02KQ", timeout=1.5)


@pytest.mark.os_agnostic
def test_a_terminator_past_the_bound_is_a_protocol_error(serve: Callable[..., PrinterServer]) -> None:
    server = serve(answering(b"\x02KQ", b"x" * MAX_ANSWER_BYTES + b"\r"))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer, pytest.raises(PrinterProtocolError):
        printer.query(b"\x02KQ", timeout=1.5)


@pytest.mark.os_agnostic
def test_an_answer_exactly_at_the_bound_is_accepted(serve: Callable[..., PrinterServer]) -> None:
    answer = b"x" * (MAX_ANSWER_BYTES - 1) + b"\r"
    server = serve(answering(b"\x02KQ", answer))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        assert printer.query(b"\x02KQ", timeout=1.5) == answer


@pytest.mark.os_agnostic
def test_a_printer_hanging_up_mid_answer_is_a_transport_error(serve: Callable[..., PrinterServer]) -> None:
    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, 2)
        connection.sendall(b"NN")

    server = serve(play)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        with pytest.raises(TransportError, match="closed the connection") as caught:
            printer.query(b"\x01A", timeout=1.0)
        assert not isinstance(caught.value, PrinterTimeoutError)


@pytest.mark.os_agnostic
def test_bytes_waiting_before_a_query_are_not_read_as_its_answer(serve: Callable[..., PrinterServer]) -> None:
    def play(server: PrinterServer, connection: socket.socket) -> None:
        connection.sendall(b"stale\r")
        server.read_exactly(connection, 2)
        connection.sendall(b"fresh\r")
        server.read_to_end(connection)

    server = serve(play)
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=2.0)
    readable, _, _ = select.select([sock], [], [], 2.0)
    assert readable, "the stale answer never arrived"
    with TcpTransport(sock, uri=server.uri, timeout=2.0) as printer:
        assert printer.query(b"\x01A", timeout=1.0) == b"fresh\r"


@pytest.mark.os_agnostic
def test_a_send_the_printer_does_not_take_times_out(serve: Callable[..., PrinterServer]) -> None:
    stop = threading.Event()

    def play(server: PrinterServer, connection: socket.socket) -> None:
        stop.wait(JOIN_SECONDS)

    server = serve(play)
    try:
        with TcpTransport.connect(server.uri, timeout=0.3) as printer:
            started = time.monotonic()
            with pytest.raises(PrinterTimeoutError, match="cannot write to tcp"):
                printer.send(b"x" * (64 * 1024 * 1024))
            assert time.monotonic() - started < 1.5
    finally:
        stop.set()


class _RecordingSocket(socket.socket):
    """A real socket that remembers the size of every ``sendall`` call."""

    def __init__(self, *, fileno: int) -> None:
        super().__init__(fileno=fileno)
        self.sizes: list[int] = []

    def sendall(self, data: ReadableBuffer, flags: int = 0, /) -> None:
        self.sizes.append(memoryview(data).nbytes)
        super().sendall(data, flags)


@pytest.mark.os_agnostic
def test_a_job_is_written_in_pieces_no_larger_than_the_send_chunk() -> None:
    # Windows accepts one send call of any size whole, so only pieces this small let a stalled
    # printer show up as a timeout there; the stalled-printer test above proves it on Windows.
    ours, theirs = socket.socketpair()
    received = bytearray()

    def drain() -> None:
        with theirs:
            while chunk := theirs.recv(65536):
                received.extend(chunk)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    job = b"x" * (3 * SEND_CHUNK_BYTES + 5)
    recording = _RecordingSocket(fileno=ours.detach())
    with TcpTransport(recording, uri=TcpUri(host="127.0.0.1"), timeout=2.0) as printer:
        printer.send(job)
    reader.join(JOIN_SECONDS)

    assert max(recording.sizes) <= SEND_CHUNK_BYTES
    assert bytes(received) == job


@pytest.mark.os_agnostic
def test_a_refused_connection_is_a_transport_error() -> None:
    with socket.create_server(("127.0.0.1", 0)) as probe:
        port = probe.getsockname()[1]
    with pytest.raises(TransportError, match=f"tcp 127.0.0.1:{port}"):
        open_printer(f"tcp://127.0.0.1:{port}", timeout=1.0)


def _free_port() -> int:
    with socket.create_server(("127.0.0.1", 0)) as probe:
        return int(probe.getsockname()[1])


#: Where Windows and macOS start the ports they hand out for a bind to port 0 (the IANA dynamic range).
_DYNAMIC_PORTS_START = 49152
#: The lowest port tried for one nothing may listen on: clear of the well-known and most registered ones.
_QUIET_PORTS_START = 20000


def _port_no_other_test_takes() -> int:
    """Return a free port below every range a bind to port 0 draws from.

    A port from ``_free_port`` goes back to the pool the next bind to port 0 draws from, and the
    xdist workers start fake printers that way all the time: in CI one landed on it inside the
    3 s retry window, and a connect meant to be refused for good succeeded. Below the dynamic
    range only an explicit bind can take the port, and no test binds one.
    """
    for port in range(_QUIET_PORTS_START, min(_linux_dynamic_ports_start(), _DYNAMIC_PORTS_START)):
        with contextlib.suppress(OSError), socket.create_server(("127.0.0.1", port)):
            return port
    # A host whose dynamic range starts below 20000 leaves no such port; the race is rare there.
    return _free_port()


def _linux_dynamic_ports_start() -> int:
    """The first port Linux hands out for a bind to port 0 (32768 by default), else the IANA start."""
    try:
        return int(Path("/proc/sys/net/ipv4/ip_local_port_range").read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError):
        return _DYNAMIC_PORTS_START


@pytest.mark.os_agnostic
def test_a_connection_refused_for_a_moment_is_retried() -> None:
    """A print server refuses a new connection for a moment after closing the last one.

    Measured on a SerComm PS301: about 1-2 s after a print job's connection closed, a status
    query's connect was refused. The listener here appears 0.6 s after the first attempt.
    """
    port = _free_port()
    received = bytearray()

    def late_printer() -> None:
        time.sleep(0.6)
        with socket.create_server(("127.0.0.1", port)) as listener:
            listener.settimeout(JOIN_SECONDS)
            connection, _ = listener.accept()
            with connection:
                while chunk := connection.recv(4096):
                    received.extend(chunk)

    thread = threading.Thread(target=late_printer, daemon=True)
    thread.start()
    printer = TcpTransport.connect(TcpUri(host="127.0.0.1", port=port), timeout=5.0)
    try:
        printer.send(b"\x01A")
    finally:
        printer.close()
    thread.join(JOIN_SECONDS)
    assert bytes(received) == b"\x01A"


@pytest.mark.os_agnostic
def test_a_connection_refused_for_good_fails_within_the_retry_window() -> None:
    """Retrying refused connections is bounded: a host that stays down is reported, not waited on.

    No attempt starts after the window closes, so the report comes within the window plus ONE
    attempt. An attempt is not instant everywhere: Windows retransmits the SYN and reports a refusal
    only after about 2 s, so the bound is measured here rather than assumed.
    """
    port = _port_no_other_test_takes()
    one_attempt = _seconds_for_one_refused_connect(port)
    started = time.monotonic()
    with pytest.raises(TransportError, match=rf"^cannot connect to tcp 127\.0\.0\.1:{port}: ") as caught:
        TcpTransport.connect(TcpUri(host="127.0.0.1", port=port), timeout=30.0)
    elapsed = time.monotonic() - started
    # the OS words the refusal in its own language, so the cause's TYPE says it was refused
    assert isinstance(caught.value.__cause__, ConnectionRefusedError)
    assert elapsed < REFUSED_RETRY_SECONDS + one_attempt + 1.0, f"{elapsed=:.2f} {one_attempt=:.2f}"


def _seconds_for_one_refused_connect(port: int) -> float:
    started = time.monotonic()
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.1", port), timeout=30.0)
    return time.monotonic() - started


@pytest.mark.os_agnostic
def test_open_printer_connects_a_tcp_uri(serve: Callable[..., PrinterServer]) -> None:
    server = serve(silent)
    printer = open_printer(f"tcp://127.0.0.1:{server.port}", timeout=2.0)
    try:
        printer.send(b"\x01A")
    finally:
        printer.close()
    server.join()
    assert bytes(server.received) == b"\x01A"


@pytest.mark.os_agnostic
def test_a_closed_transport_refuses_and_closes_twice(serve: Callable[..., PrinterServer]) -> None:
    server = serve(silent)
    printer = TcpTransport.connect(server.uri, timeout=2.0)
    printer.close()
    printer.close()
    with pytest.raises(TransportError, match="the transport is closed"):
        printer.send(b"x")
    with pytest.raises(TransportError, match="the transport is closed"):
        printer.query(b"x", timeout=1.0)


@pytest.mark.os_agnostic
def test_query_arguments_are_checked(serve: Callable[..., PrinterServer]) -> None:
    server = serve(silent)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        with pytest.raises(ValueError, match="terminator must not be empty"):
            printer.query(b"x", timeout=1.0, terminator=b"")
        with pytest.raises(ValueError, match="timeout must be above 0"):
            printer.query(b"x", timeout=0.0)
    with pytest.raises(ValueError, match="timeout must be above 0"):
        TcpTransport.connect(server.uri, timeout=-1.0)


# --- read_answer: answers ended by silence (<STX>W module directory) ----------------------------

#: The manual's sample answer to ``<STX>WG`` (see tests/test_memory.py).
DIRECTORY = b"MODULE: A\rAVAILABLE BYTES IN MODULE:00049083\rMODULE: B\rLOGO\rCAM\rAVAILABLE BYTES IN MODULE:00257919\r"


@pytest.mark.os_agnostic
def test_read_answer_returns_the_whole_directory_after_the_printer_falls_silent(
    serve: Callable[..., PrinterServer],
) -> None:
    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, 3)
        for line in DIRECTORY.split(b"\r")[:-1]:
            connection.sendall(line + b"\r")
            time.sleep(0.02)
        server.read_to_end(connection)

    server = serve(play)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        assert printer.read_answer(b"\x02WG", timeout=2.0, idle=0.5) == DIRECTORY
    server.join()
    assert bytes(server.received) == b"\x02WG"


@pytest.mark.os_agnostic
def test_read_answer_ends_at_the_first_silence_of_idle_seconds(serve: Callable[..., PrinterServer]) -> None:
    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, 3)
        connection.sendall(b"MODULE: A\r")
        time.sleep(1.2)
        # The client may already have closed: the late part has nowhere to go, which is the point.
        with contextlib.suppress(OSError):
            connection.sendall(b"LATE\r")
            server.read_to_end(connection)

    server = serve(play)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        started = time.monotonic()
        assert printer.read_answer(b"\x02WG", timeout=2.0, idle=0.3) == b"MODULE: A\r"
        assert time.monotonic() - started < 1.0


@pytest.mark.os_agnostic
def test_read_answer_with_no_byte_at_all_times_out_within_the_timeout(serve: Callable[..., PrinterServer]) -> None:
    server = serve(silent)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        started = time.monotonic()
        with pytest.raises(PrinterTimeoutError, match="no complete answer within 1 s"):
            printer.read_answer(b"\x02WG", timeout=1.0, idle=0.2)
        elapsed = time.monotonic() - started
    assert 0.7 <= elapsed < 2.5


@pytest.mark.os_agnostic
def test_read_answer_refuses_an_answer_longer_than_its_bound(serve: Callable[..., PrinterServer]) -> None:
    server = serve(answering(b"\x02WG", b"x" * (MAX_IDLE_ANSWER_BYTES + 10)))
    with (
        TcpTransport.connect(server.uri, timeout=2.0) as printer,
        pytest.raises(PrinterProtocolError, match=f"answer longer than {MAX_IDLE_ANSWER_BYTES} bytes"),
    ):
        printer.read_answer(b"\x02WG", timeout=2.0, idle=0.5)


@pytest.mark.os_agnostic
def test_read_answer_accepts_an_answer_exactly_at_its_bound(serve: Callable[..., PrinterServer]) -> None:
    answer = b"x" * MAX_IDLE_ANSWER_BYTES
    server = serve(answering(b"\x02WG", answer))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        assert printer.read_answer(b"\x02WG", timeout=2.0, idle=1.0) == answer


@pytest.mark.os_agnostic
def test_read_answer_returns_what_came_before_the_printer_hung_up(serve: Callable[..., PrinterServer]) -> None:
    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, 3)
        connection.sendall(DIRECTORY)

    server = serve(play)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        assert printer.read_answer(b"\x02WG", timeout=2.0, idle=0.5) == DIRECTORY


@pytest.mark.os_agnostic
def test_read_answer_from_a_printer_hanging_up_before_any_byte_is_a_transport_error(
    serve: Callable[..., PrinterServer],
) -> None:
    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, 3)

    server = serve(play)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        with pytest.raises(TransportError, match="closed the connection") as caught:
            printer.read_answer(b"\x02WG", timeout=2.0, idle=0.5)
        assert not isinstance(caught.value, PrinterTimeoutError)


@pytest.mark.os_agnostic
def test_bytes_waiting_before_read_answer_are_not_read_as_its_answer(serve: Callable[..., PrinterServer]) -> None:
    def play(server: PrinterServer, connection: socket.socket) -> None:
        connection.sendall(b"stale\r")
        server.read_exactly(connection, 3)
        connection.sendall(b"No Modules Available\r")
        server.read_to_end(connection)

    server = serve(play)
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=2.0)
    readable, _, _ = select.select([sock], [], [], 2.0)
    assert readable, "the stale answer never arrived"
    with TcpTransport(sock, uri=server.uri, timeout=2.0) as printer:
        assert printer.read_answer(b"\x02WF", timeout=2.0, idle=0.3) == b"No Modules Available\r"


@pytest.mark.os_agnostic
def test_read_answer_arguments_are_checked(serve: Callable[..., PrinterServer]) -> None:
    server = serve(silent)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        with pytest.raises(ValueError, match="idle must be above 0"):
            printer.read_answer(b"x", timeout=1.0, idle=0.0)
        with pytest.raises(ValueError, match="timeout must be above 0"):
            printer.read_answer(b"x", timeout=-1.0, idle=0.5)
        with pytest.raises(ValueError, match="total must be above 0"):
            printer.read_answer(b"x", timeout=1.0, idle=0.5, total=0.0)
    with pytest.raises(TransportError, match="the transport is closed"):
        printer.read_answer(b"x", timeout=1.0, idle=0.5)


@pytest.mark.os_agnostic
def test_read_answer_from_a_printer_that_never_stops_dripping_ends_at_the_total_deadline(
    serve: Callable[..., PrinterServer],
) -> None:
    stop = threading.Event()

    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, 3)
        # One byte every 0.1 s, well inside idle=0.5: only the total deadline can end the read.
        with contextlib.suppress(OSError):
            while not stop.wait(0.1):
                connection.sendall(b"x")

    server = serve(play)
    try:
        with TcpTransport.connect(server.uri, timeout=2.0) as printer:
            started = time.monotonic()
            with pytest.raises(PrinterTimeoutError, match="the answer did not end within 1 s"):
                printer.read_answer(b"\x02WG", timeout=1.0, idle=0.5, total=1.0)
            elapsed = time.monotonic() - started
    finally:
        stop.set()
    assert 0.7 <= elapsed < 2.5


@pytest.mark.os_agnostic
def test_read_answer_does_not_start_the_idle_clock_on_a_lone_xon(serve: Callable[..., PrinterServer]) -> None:
    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, 3)
        connection.sendall(b"\x11")
        time.sleep(0.9)
        connection.sendall(DIRECTORY)
        server.read_to_end(connection)

    server = serve(play)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        assert printer.read_answer(b"\x02WG", timeout=3.0, idle=0.3) == b"\x11" + DIRECTORY


@pytest.mark.os_agnostic
def test_read_answer_with_only_flow_control_bytes_is_no_answer(serve: Callable[..., PrinterServer]) -> None:
    server = serve(answering(b"\x02WG", b"\x11\x13"))
    with (
        TcpTransport.connect(server.uri, timeout=2.0) as printer,
        pytest.raises(PrinterTimeoutError, match=r"no complete answer within 1 s \(2 bytes read\)"),
    ):
        printer.read_answer(b"\x02WG", timeout=1.0, idle=0.3)
