"""Raw TCP socket transport, the channel of print servers and printer network cards (port 9100)."""

from __future__ import annotations

import socket
import time

from datamax_dpl.domain.errors import PrinterProtocolError, PrinterTimeoutError, TransportError

from .uri import ClosingTransport, TcpUri, check_timeout, transport_error

#: Longest answer read before the printer is judged to be sending something other than an answer.
MAX_ANSWER_BYTES = 64 * 1024
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

        Raises:
            PrinterTimeoutError: The connection was not made in time.
            TransportError: The host is unknown or refused the connection.
        """
        check_timeout(timeout)
        try:
            sock = socket.create_connection((uri.host, uri.port), timeout=timeout)
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
        try:
            self._sock.sendall(data)
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


def _nth(data: bytearray, needle: bytes, count: int) -> int:
    """Index of the ``count``-th occurrence of ``needle`` in ``data``, or -1."""
    index = -len(needle)
    for _ in range(count):
        index = data.find(needle, index + len(needle))
        if index < 0:
            return -1
    return index


__all__ = ["MAX_ANSWER_BYTES", "TcpTransport"]
