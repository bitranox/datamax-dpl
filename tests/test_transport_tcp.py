"""Raw TCP transport against a real socket server on 127.0.0.1."""

from __future__ import annotations

import contextlib
import select
import socket
import threading
import time
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.transport import open_printer
from datamax_dpl.adapters.transport.tcp import MAX_ANSWER_BYTES, REFUSED_RETRY_SECONDS, SEND_CHUNK_BYTES, TcpTransport
from datamax_dpl.adapters.transport.uri import TcpUri
from datamax_dpl.domain.errors import PrinterProtocolError, PrinterTimeoutError, TransportError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from _typeshed import ReadableBuffer

JOIN_SECONDS = 5.0


class PrinterServer:
    """A one-connection server; ``handler`` plays the printer on the accepted socket."""

    def __init__(self, handler: Callable[[socket.socket], None]) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port: int = self.listener.getsockname()[1]
        self.received = bytearray()
        self._handler = handler
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def uri(self) -> TcpUri:
        return TcpUri(host="127.0.0.1", port=self.port)

    def _serve(self) -> None:
        connection, _ = self.listener.accept()
        with connection:
            self._handler(connection)

    def read_exactly(self, connection: socket.socket, count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            chunk = connection.recv(count - len(data))
            if not chunk:
                break
            data += chunk
        self.received += data
        return bytes(data)

    def read_to_end(self, connection: socket.socket) -> None:
        # A client that closes with our answer still unread resets the connection: that is its end.
        with contextlib.suppress(ConnectionResetError):
            while chunk := connection.recv(65536):
                self.received += chunk

    def join(self) -> None:
        self._thread.join(JOIN_SECONDS)
        self.listener.close()


@pytest.fixture
def serve() -> Iterator[Callable[[Callable[[PrinterServer, socket.socket], None]], PrinterServer]]:
    servers: list[PrinterServer] = []

    def start(play: Callable[[PrinterServer, socket.socket], None]) -> PrinterServer:
        holder: list[PrinterServer] = []
        server = PrinterServer(lambda connection: play(holder[0], connection))
        holder.append(server)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.join()


def _answer(request: bytes, answer: bytes) -> Callable[[PrinterServer, socket.socket], None]:
    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, len(request))
        connection.sendall(answer)
        server.read_to_end(connection)

    return play


def _silent(server: PrinterServer, connection: socket.socket) -> None:
    server.read_to_end(connection)


@pytest.mark.os_agnostic
def test_sent_bytes_arrive_identical(serve: Callable[..., PrinterServer]) -> None:
    server = serve(_silent)
    payload = bytes(range(256)) * 300 + b"\x02L\rD11\r\x0dE\r"
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        printer.send(payload[:1000])
        printer.send(payload[1000:])
    server.join()
    assert bytes(server.received) == payload


@pytest.mark.os_agnostic
def test_a_query_returns_the_answer_with_its_terminator(serve: Callable[..., PrinterServer]) -> None:
    server = serve(_answer(b"\x01A", b"NNNNNNNN\rtrailing"))
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
    server = serve(_silent)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        started = time.monotonic()
        with pytest.raises(PrinterTimeoutError, match="no complete answer"):
            printer.query(b"\x01A", timeout=0.3)
        elapsed = time.monotonic() - started
    assert 0.25 <= elapsed < 1.5


@pytest.mark.os_agnostic
def test_a_partial_answer_then_silence_times_out(serve: Callable[..., PrinterServer]) -> None:
    server = serve(_answer(b"\x01A", b"NNNN"))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer, pytest.raises(PrinterTimeoutError, match="4 bytes"):
        printer.query(b"\x01A", timeout=0.3)


@pytest.mark.os_agnostic
def test_an_answer_longer_than_the_bound_is_a_protocol_error(serve: Callable[..., PrinterServer]) -> None:
    server = serve(_answer(b"\x02KQ", b"x" * (MAX_ANSWER_BYTES + 10)))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer, pytest.raises(PrinterProtocolError):
        printer.query(b"\x02KQ", timeout=1.5)


@pytest.mark.os_agnostic
def test_a_terminator_past_the_bound_is_a_protocol_error(serve: Callable[..., PrinterServer]) -> None:
    server = serve(_answer(b"\x02KQ", b"x" * MAX_ANSWER_BYTES + b"\r"))
    with TcpTransport.connect(server.uri, timeout=2.0) as printer, pytest.raises(PrinterProtocolError):
        printer.query(b"\x02KQ", timeout=1.5)


@pytest.mark.os_agnostic
def test_an_answer_exactly_at_the_bound_is_accepted(serve: Callable[..., PrinterServer]) -> None:
    answer = b"x" * (MAX_ANSWER_BYTES - 1) + b"\r"
    server = serve(_answer(b"\x02KQ", answer))
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
    port = _free_port()
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
    server = serve(_silent)
    printer = open_printer(f"tcp://127.0.0.1:{server.port}", timeout=2.0)
    try:
        printer.send(b"\x01A")
    finally:
        printer.close()
    server.join()
    assert bytes(server.received) == b"\x01A"


@pytest.mark.os_agnostic
def test_a_closed_transport_refuses_and_closes_twice(serve: Callable[..., PrinterServer]) -> None:
    server = serve(_silent)
    printer = TcpTransport.connect(server.uri, timeout=2.0)
    printer.close()
    printer.close()
    with pytest.raises(TransportError, match="the transport is closed"):
        printer.send(b"x")
    with pytest.raises(TransportError, match="the transport is closed"):
        printer.query(b"x", timeout=1.0)


@pytest.mark.os_agnostic
def test_query_arguments_are_checked(serve: Callable[..., PrinterServer]) -> None:
    server = serve(_silent)
    with TcpTransport.connect(server.uri, timeout=2.0) as printer:
        with pytest.raises(ValueError, match="terminator must not be empty"):
            printer.query(b"x", timeout=1.0, terminator=b"")
        with pytest.raises(ValueError, match="timeout must be above 0"):
            printer.query(b"x", timeout=0.0)
    with pytest.raises(ValueError, match="timeout must be above 0"):
        TcpTransport.connect(server.uri, timeout=-1.0)
