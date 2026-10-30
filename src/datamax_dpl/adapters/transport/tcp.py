"""Raw TCP socket transport, the channel of print servers and printer network cards (port 9100)."""

from __future__ import annotations

import socket
import time

from datamax_dpl.domain.errors import PrinterProtocolError, PrinterTimeoutError, TransportError

from .uri import ClosingTransport, TcpUri, check_timeout, transport_error

#: Longest answer read before the printer is judged to be sending something other than an answer.
MAX_ANSWER_BYTES = 64 * 1024
#: Longest answer :meth:`TcpTransport.read_answer` collects. That answer (the ``<STX>W`` module
#: directory) has no terminator: per module about 46 bytes of header lines plus one line of at most
#: 16 characters and ``<CR>`` per stored name, for up to five modules (A-E). The manual does not
#: bound the number of names, so 1 MiB (about 60,000 full-length names) is the margin over any
#: listing a 256K or 512K module can hold, while a printer streaming something endless is still
#: cut off.
MAX_IDLE_ANSWER_BYTES = 1024 * 1024
#: Largest piece handed to one send call. Windows takes a single send of any size whole while its
#: buffer is not full (a 64 MB job "sent" to a printer reading nothing, in 0.02 s), so a job sent
#: in one call never times out there; pieces no larger than its default 64 KiB send buffer block
#: once the printer stops reading, on every OS.
SEND_CHUNK_BYTES = 64 * 1024
#: Longest time a REFUSED connection is retried. A print server that just closed a job's
#: connection refuses the next one for a moment (about 1-2 s measured on a SerComm PS301), so a
#: status query sent right after a print would otherwise fail although nothing is wrong. Other
#: connect errors (unknown host, unreachable, timeout) are not retried.
REFUSED_RETRY_SECONDS = 3.0
_REFUSED_RETRY_INTERVAL = 0.25
_RECV_CHUNK = 4096


class TcpTransport(ClosingTransport):
    """A connected raw socket to one printer.

    Use :meth:`connect` to open one; the constructor takes an already connected socket so a caller
    holding one (a test, a socket pair) can wrap it.
    """

    def __init__(self, sock: socket.socket, *, uri: TcpUri, timeout: float) -> None:
        """Wrap ``sock``; ``timeout`` bounds every write."""
        self._sock = sock
        self._uri = uri
        self._timeout = check_timeout(timeout)
        sock.settimeout(self._timeout)

    @classmethod
    def connect(cls, uri: TcpUri, *, timeout: float) -> TcpTransport:
        """Connect to ``uri`` within ``timeout`` seconds.

        Args:
            uri: Host and port.
            timeout: Seconds for the connection and for every later write.

        Returns:
            The connected transport.

        A refused connection is retried for up to :data:`REFUSED_RETRY_SECONDS` (never longer than
        ``timeout``) before it is reported.

        Raises:
            PrinterTimeoutError: The connection was not made in time.
            TransportError: The host is unknown or kept refusing the connection.
        """
        check_timeout(timeout)
        try:
            sock = _connect_retrying_refusals(uri, timeout=timeout)
        except OSError as exc:
            raise transport_error(uri, action="connect to", error=exc) from exc
        return cls(sock, uri=uri, timeout=timeout)

    @property
    def bidirectional(self) -> bool:
        """A socket reads the printer's answers."""
        return True

    def send(self, data: bytes) -> None:
        """Write ``data`` completely.

        Raises:
            PrinterTimeoutError: The printer stopped accepting data for longer than the timeout.
            TransportError: The connection failed or is closed.
        """
        self._ensure_open()
        view = memoryview(data)
        try:
            # The socket timeout bounds each piece, so it measures how long the printer stopped
            # taking data, not how long the whole job takes.
            for offset in range(0, len(view), SEND_CHUNK_BYTES):
                self._sock.sendall(view[offset : offset + SEND_CHUNK_BYTES])
        except OSError as exc:
            raise transport_error(self._uri, action="write to", error=exc) from exc

    def query(self, request: bytes, *, timeout: float, terminator: bytes = b"\r", lines: int = 1) -> bytes:
        """Send ``request`` and return the answer up to and including ``terminator``.

        Bytes already waiting before the request belong to an earlier exchange (an answer that
        arrived after its query timed out) and are discarded, so they are never read as this
        answer. Bytes after the terminator are dropped for the same reason.

        Raises:
            PrinterTimeoutError: No complete answer within ``timeout`` seconds.
            PrinterProtocolError: More than :data:`MAX_ANSWER_BYTES` arrived without a terminator.
            TransportError: The connection failed or the printer closed it.
        """
        deadline = time.monotonic() + check_timeout(timeout)
        if not terminator or lines < 1:
            raise ValueError("terminator must not be empty and lines at least 1")
        self._ensure_open()
        self._discard_waiting()
        self.send(request)
        try:
            return self._read_until(terminator, deadline=deadline, lines=lines)
        finally:
            self._sock.settimeout(self._timeout)

    def read_answer(self, request: bytes, *, timeout: float, idle: float) -> bytes:
        """Send ``request`` and return every byte until ``idle`` seconds pass without a new one.

        Bytes already waiting before the request are discarded, as in :meth:`query`. A printer
        that closes the connection after its first byte has ended its answer, so what came is
        returned.

        Args:
            request: The bytes to send.
            timeout: Seconds to wait for the first byte.
            idle: Seconds of silence that end the answer.

        Returns:
            The answer, at most :data:`MAX_IDLE_ANSWER_BYTES` long.

        Raises:
            PrinterTimeoutError: No byte within ``timeout`` seconds.
            PrinterProtocolError: More than :data:`MAX_IDLE_ANSWER_BYTES` arrived.
            TransportError: The connection failed, or the printer closed it before answering.
            ValueError: ``timeout`` or ``idle`` is not a usable number of seconds.
        """
        deadline = time.monotonic() + check_timeout(timeout)
        check_timeout(idle, name="idle")
        self._ensure_open()
        self._discard_waiting()
        self.send(request)
        try:
            answer = bytearray(self._receive(deadline - time.monotonic(), received=0))
            while chunk := self._receive_until_idle(idle, room=MAX_IDLE_ANSWER_BYTES + 1 - len(answer)):
                answer += chunk
                if len(answer) > MAX_IDLE_ANSWER_BYTES:
                    raise PrinterProtocolError(
                        f"tcp {self._uri.target}: answer longer than {MAX_IDLE_ANSWER_BYTES} bytes"
                    )
            return bytes(answer)
        finally:
            self._sock.settimeout(self._timeout)

    def close(self) -> None:
        """Close the socket; safe to call twice."""
        self._sock.close()

    def _ensure_open(self) -> None:
        """Refuse to use a closed socket, whose calls would fail with a bare ``OSError``."""
        if self._sock.fileno() == -1:
            raise TransportError(f"tcp {self._uri.target}: the transport is closed")

    def _discard_waiting(self) -> None:
        """Read and drop whatever already sits in the receive buffer, without waiting."""
        try:
            # A zero timeout makes recv raise at once when nothing is waiting.
            self._sock.settimeout(0.0)
            while self._sock.recv(_RECV_CHUNK):
                pass
        except BlockingIOError:
            pass
        except OSError as exc:
            raise transport_error(self._uri, action="read from", error=exc) from exc
        finally:
            self._sock.settimeout(self._timeout)

    def _read_until(self, terminator: bytes, *, deadline: float, lines: int = 1) -> bytes:
        """Collect bytes until the ``lines``-th ``terminator``, the deadline or the size bound."""
        answer = bytearray()
        while True:
            end = _nth(answer, terminator, lines)
            if 0 <= end <= MAX_ANSWER_BYTES - len(terminator):
                return bytes(answer[: end + len(terminator)])
            if end >= 0 or len(answer) >= MAX_ANSWER_BYTES:
                raise PrinterProtocolError(
                    f"tcp {self._uri.target}: no answer terminator within {MAX_ANSWER_BYTES} bytes"
                )
            answer += self._receive(deadline - time.monotonic(), received=len(answer))

    def _receive(self, remaining: float, *, received: int) -> bytes:
        """Receive one chunk within ``remaining`` seconds; an empty read means the printer hung up."""
        if remaining <= 0:
            raise PrinterTimeoutError(f"tcp {self._uri.target}: no complete answer in time ({received} bytes read)")
        self._sock.settimeout(remaining)
        try:
            chunk = self._sock.recv(_RECV_CHUNK)
        except TimeoutError as exc:
            raise PrinterTimeoutError(
                f"tcp {self._uri.target}: no complete answer in time ({received} bytes read)"
            ) from exc
        except OSError as exc:
            raise transport_error(self._uri, action="read from", error=exc) from exc
        if not chunk:
            raise TransportError(f"tcp {self._uri.target}: the printer closed the connection ({received} bytes read)")
        return chunk

    def _receive_until_idle(self, idle: float, *, room: int) -> bytes:
        """Receive up to ``room`` bytes; empty once ``idle`` seconds pass silently or the printer hangs up."""
        self._sock.settimeout(idle)
        try:
            return self._sock.recv(min(_RECV_CHUNK, room))
        except TimeoutError:
            return b""
        except OSError as exc:
            raise transport_error(self._uri, action="read from", error=exc) from exc


def _connect_retrying_refusals(uri: TcpUri, *, timeout: float) -> socket.socket:
    """Connect, retrying a refused connection while the retry window is open.

    No attempt starts once the window has closed: the refusal of the last attempt inside it is
    raised as is. A host that keeps refusing is therefore reported after the window plus one
    attempt, and an attempt is not instant everywhere - Windows retransmits the SYN and reports a
    refusal only after about 2 s.
    """
    deadline = time.monotonic() + min(timeout, REFUSED_RETRY_SECONDS)
    while True:
        try:
            return socket.create_connection((uri.host, uri.port), timeout=timeout)
        except ConnectionRefusedError:
            if time.monotonic() + _REFUSED_RETRY_INTERVAL >= deadline:
                raise
        time.sleep(_REFUSED_RETRY_INTERVAL)


def _nth(data: bytearray, needle: bytes, count: int) -> int:
    """Index of the ``count``-th occurrence of ``needle`` in ``data``, or -1."""
    index = -len(needle)
    for _ in range(count):
        index = data.find(needle, index + len(needle))
        if index < 0:
            return -1
    return index


__all__ = ["MAX_ANSWER_BYTES", "MAX_IDLE_ANSWER_BYTES", "REFUSED_RETRY_SECONDS", "TcpTransport"]
