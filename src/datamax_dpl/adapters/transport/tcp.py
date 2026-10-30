"""Raw TCP socket transport, the channel of print servers and printer network cards (port 9100)."""

from __future__ import annotations

import socket
import time
from typing import TYPE_CHECKING

from datamax_dpl.domain.errors import PrinterProtocolError, PrinterTimeoutError, TransportError
from datamax_dpl.domain.timing import MAX_ANSWER_SECONDS

from .uri import (
    NOTHING_SKIPPED,
    ClosingTransport,
    Skipped,
    TcpUri,
    Wait,
    answer_overflow,
    answer_started,
    check_timeout,
    no_answer,
    transport_error,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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

    def query(
        self,
        request: bytes,
        *,
        timeout: float,
        terminator: bytes = b"\r",
        lines: int = 1,
        accept: Callable[[bytes], bool] | None = None,
    ) -> bytes:
        """Send ``request`` and return the answer up to and including ``terminator``.

        Bytes already waiting before the request belong to an earlier exchange (an answer that
        arrived after its query timed out) and are discarded, so they are never read as this
        answer. Bytes after the terminator are dropped for the same reason. A line ``accept``
        refuses is skipped: a print server can pass on what the printer still owed an earlier
        query only after this request went out.

        Raises:
            PrinterTimeoutError: No complete answer within ``timeout`` seconds.
            PrinterProtocolError: :data:`MAX_ANSWER_BYTES` arrived, skipped lines included,
                without the answer.
            TransportError: The connection failed or the printer closed it.
        """
        wait = Wait.since(time.monotonic(), check_timeout(timeout))
        if not terminator or lines < 1:
            raise ValueError("terminator must not be empty and lines at least 1")
        self._ensure_open()
        self._discard_waiting()
        self.send(request)
        try:
            answer = _Lines(f"tcp {self._uri.target}", terminator, lines=lines, accept=accept)
            return self._read_until(answer, wait=wait)
        finally:
            self._sock.settimeout(self._timeout)

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = MAX_ANSWER_SECONDS) -> bytes:
        """Send ``request`` and return every byte until ``idle`` seconds pass without a new one.

        Bytes already waiting before the request are discarded, as in :meth:`query`. XON and XOFF
        alone do not start the answer: until another byte comes, the wait is the first-byte wait.
        A printer that closes the connection after it began answering has ended its answer, so
        what came is returned.

        Args:
            request: The bytes to send.
            timeout: Seconds to wait for the first byte.
            idle: Seconds of silence that end the answer.
            total: Seconds the whole answer may take, from the request to its ending silence.

        Returns:
            The answer, at most :data:`MAX_IDLE_ANSWER_BYTES` long.

        Raises:
            PrinterTimeoutError: No byte within ``timeout`` seconds, or the answer did not end
                within ``total`` seconds.
            PrinterProtocolError: More than :data:`MAX_IDLE_ANSWER_BYTES` arrived.
            TransportError: The connection failed, or the printer closed it before answering.
            ValueError: ``timeout``, ``idle`` or ``total`` is not a usable number of seconds.
        """
        started = time.monotonic()
        check_timeout(timeout)
        check_timeout(idle, name="idle")
        check_timeout(total, name="total")
        self._ensure_open()
        self._discard_waiting()
        self.send(request)
        try:
            answer = self._receive_start(Wait.since(started, min(timeout, total)))
            return self._receive_rest(answer, idle=idle, end=started + total, total=total)
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

    def _read_until(self, lines: _Lines, *, wait: Wait) -> bytes:
        """Receive into ``lines`` until it holds the answer, ``wait`` ends or the size bound is reached."""
        while True:
            answer = lines.add(self._receive(wait, received=lines.pending, skipped=lines.skipped))
            if answer is not None:
                return answer

    def _receive(self, wait: Wait, *, received: bytes | bytearray, skipped: Skipped = NOTHING_SKIPPED) -> bytes:
        """Receive one chunk before ``wait`` ends; an empty read means the printer hung up.

        ``received`` and ``skipped`` are what came of the answer so far, for the error messages. A
        hang-up (closed or reset) before any of it is ``unanswered``; once a line was skipped it is
        not, since a print server passing on an earlier answer may yet pass on this one.
        """
        remaining = wait.remaining()
        if remaining <= 0:
            raise no_answer(f"tcp {self._uri.target}", wait=wait, received=received, skipped=skipped)
        self._sock.settimeout(remaining)
        nothing_came = not answer_started(received) and not skipped.lines
        try:
            chunk = self._sock.recv(_RECV_CHUNK)
        except TimeoutError as exc:
            raise no_answer(f"tcp {self._uri.target}", wait=wait, received=received, skipped=skipped) from exc
        except OSError as exc:
            hung_up = nothing_came and isinstance(exc, ConnectionError)
            raise transport_error(self._uri, action="read from", error=exc, unanswered=hung_up) from exc
        if not chunk:
            read = len(received) + skipped.size
            raise TransportError(
                f"tcp {self._uri.target}: the printer closed the connection ({read} bytes read)",
                unanswered=nothing_came,
            )
        return chunk

    def _receive_start(self, wait: Wait) -> bytearray:
        """Receive until a byte other than flow control has come, before ``wait`` ends."""
        answer = bytearray()
        while not answer_started(answer):
            answer += self._receive(wait, received=answer)
            self._check_answer_size(answer)
        return answer

    def _receive_rest(self, answer: bytearray, *, idle: float, end: float, total: float) -> bytes:
        """Receive until ``idle`` seconds of silence or a hang-up; past ``end`` the answer did not end."""
        while True:
            wait = min(idle, end - time.monotonic())
            chunk = self._receive_until_idle(wait, room=MAX_IDLE_ANSWER_BYTES + 1 - len(answer)) if wait > 0 else None
            if chunk is None and wait >= idle:
                return bytes(answer)
            if chunk is None:
                raise PrinterTimeoutError(
                    f"tcp {self._uri.target}: the answer did not end within {total:g} s ({len(answer)} bytes read)"
                )
            if not chunk:
                return bytes(answer)
            answer += chunk
            self._check_answer_size(answer)

    def _check_answer_size(self, answer: bytearray) -> None:
        """Refuse an answer past :data:`MAX_IDLE_ANSWER_BYTES`."""
        if len(answer) > MAX_IDLE_ANSWER_BYTES:
            raise PrinterProtocolError(f"tcp {self._uri.target}: answer longer than {MAX_IDLE_ANSWER_BYTES} bytes")

    def _receive_until_idle(self, wait: float, *, room: int) -> bytes | None:
        """Receive up to ``room`` bytes within ``wait`` seconds: None on silence, empty on a hang-up."""
        self._sock.settimeout(wait)
        try:
            return self._sock.recv(min(_RECV_CHUNK, room))
        except TimeoutError:
            return None
        except OSError as exc:
            raise transport_error(self._uri, action="read from", error=exc) from exc


class _Lines:
    """An answer received in chunks, judged line by line; a line ``accept`` refuses is cut out of it.

    Every byte received counts toward :data:`MAX_ANSWER_BYTES`, the skipped ones too, so a printer
    streaming refused lines is cut off as one streaming an endless answer is.
    """

    def __init__(self, channel: str, terminator: bytes, *, lines: int, accept: Callable[[bytes], bool] | None) -> None:
        self._channel = channel
        self._terminator = terminator
        self._lines = lines
        self._accept = accept
        self._data = bytearray()
        self._start = 0
        self._kept = 0
        self.skipped = NOTHING_SKIPPED

    @property
    def received(self) -> int:
        """Bytes received so far, skipped lines included."""
        return len(self._data) + self.skipped.size

    @property
    def pending(self) -> bytearray:
        """The bytes of the answer received so far: its kept lines and the line being read."""
        return self._data

    def add(self, chunk: bytes) -> bytes | None:
        """Take ``chunk``; return the answer once its ``lines``-th kept line is in, else None.

        Raises:
            PrinterProtocolError: The bytes received reached :data:`MAX_ANSWER_BYTES` without the answer.
        """
        self._data += chunk
        while (line_end := self._next_line_end()) >= 0:
            if line_end + self.skipped.size > MAX_ANSWER_BYTES:
                raise self._overflow()
            if self._take(line_end):
                return bytes(self._data[:line_end])
        if self.received >= MAX_ANSWER_BYTES:
            raise self._overflow()
        return None

    def _next_line_end(self) -> int:
        """Index just past the terminator of the line being read, or -1 while it is incomplete."""
        end = self._data.find(self._terminator, self._start)
        return end if end < 0 else end + len(self._terminator)

    def _take(self, line_end: int) -> bool:
        """Keep the line ending at ``line_end`` or cut it out; True when it was the answer's last line."""
        line = bytes(self._data[self._start : line_end])
        if self._accept is not None and not self._accept(line):
            del self._data[self._start : line_end]
            self.skipped = self.skipped.plus(line)
            return False
        self._kept += 1
        self._start = line_end
        return self._kept == self._lines

    def _overflow(self) -> PrinterProtocolError:
        return answer_overflow(self._channel, limit=MAX_ANSWER_BYTES, skipped=self.skipped)


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


__all__ = ["MAX_ANSWER_BYTES", "MAX_ANSWER_SECONDS", "MAX_IDLE_ANSWER_BYTES", "REFUSED_RETRY_SECONDS", "TcpTransport"]
