"""Raw TCP socket transport, the channel of print servers and printer network cards (port 9100)."""

from __future__ import annotations

import errno
import socket
import time
from typing import TYPE_CHECKING

from datamax_dpl.domain.errors import AnswerNotEndedError, PrinterProtocolError, TransportError
from datamax_dpl.domain.timing import DEFAULT_TRANSPORT_TUNING, MAX_ANSWER_SECONDS, TimeoutName, TransportTuning

from .uri import (
    NOTHING_SKIPPED,
    ChannelAction,
    ClosingTransport,
    Skipped,
    TcpUri,
    Wait,
    answer_overflow,
    answer_started,
    check_timeout,
    no_answer,
    shown_seconds,
    transport_error,
    truncated_job_advice,
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
_RECV_CHUNK = 4096
#: Most bytes dropped before a request. An earlier answer is no longer than this, and a peer sending
#: faster than this reads could otherwise keep the request from ever going out.
_MOST_STALE_BYTES = MAX_IDLE_ANSWER_BYTES


class TcpTransport(ClosingTransport):
    """A connected raw socket to one printer.

    Use :meth:`connect` to open one; the constructor takes an already connected socket so a caller
    holding one (a test, a socket pair) can wrap it.
    """

    def __init__(
        self, sock: socket.socket, *, uri: TcpUri, timeout: float, tuning: TransportTuning = DEFAULT_TRANSPORT_TUNING
    ) -> None:
        """Wrap ``sock``; ``timeout`` bounds the write of every piece of ``tuning.send_chunk_bytes``."""
        self._sock = sock
        self._uri = uri
        self._timeout = check_timeout(timeout)
        self._tuning = tuning
        sock.settimeout(self._timeout)

    @classmethod
    def connect(
        cls, uri: TcpUri, *, timeout: float, tuning: TransportTuning = DEFAULT_TRANSPORT_TUNING
    ) -> TcpTransport:
        """Connect to ``uri`` within ``timeout`` seconds.

        Args:
            uri: Host and port.
            timeout: Seconds for the connection and for every later write of one piece.
            tuning: The piece size a job is written in, and how a refused connect is retried.

        Returns:
            The connected transport.

        A refused connection is retried every ``tuning.refused_connect_retry_interval`` seconds for
        up to ``tuning.refused_connect_wait`` (never longer than ``timeout``) before it is reported.
        A print server that just closed a job's connection refuses the next one for a moment (about
        1-2 s measured on a SerComm PS301); other connect errors are not retried. A refusal, a
        connect timeout and a reset while connecting are ``unanswered``: no request went out.

        Raises:
            PrinterTimeoutError: The connection was not made in time.
            TransportError: The host is unknown or kept refusing the connection.
        """
        check_timeout(timeout)
        connects = _Connects()
        try:
            sock = _connect_retrying_refusals(uri, timeout=timeout, tuning=tuning, connects=connects)
        except OSError as exc:
            # Windows reports a refusal only after about 2 s of SYN retries, so under a short
            # timeout a refusing print server times out instead; neither took a request. A server
            # that resets on accept can do so before the connect has finished: with a timeout,
            # the connect waits for the handshake and then reads the socket's error, which by then
            # is the reset. An unknown or unreachable host is not worth asking again.
            no_request = isinstance(exc, TimeoutError) or _is_hang_up(exc)
            error = transport_error(uri, action=ChannelAction.CONNECT, error=exc, unanswered=no_request)
            # Every connect of the retry window was an attempt; transport_error knows of one only.
            error.attempts = connects.tried
            raise error from exc
        except UnicodeError as exc:
            # A host name that passed the URI check still goes through the socket's idna codec, and an
            # IPv6 zone id is free text it cannot always encode. No connection was attempted.
            reason = OSError(errno.EINVAL, "the host name cannot be encoded")
            raise transport_error(uri, action=ChannelAction.CONNECT, error=reason) from exc
        return cls(sock, uri=uri, timeout=timeout, tuning=tuning)

    @property
    def bidirectional(self) -> bool:
        """A socket reads the printer's answers."""
        return True

    def peer_closed(self) -> bool:
        """True when the printer or print server has closed or reset the connection.

        Peeks one byte without waiting, so a byte of an answer that is waiting stays for the next
        read. Measured over Linux loopback: a close (FIN) or a reset (RST) with nothing ahead of it
        is seen; a byte still waiting ahead of a close hides it, so the rest is then written to a
        closed connection; a server that only stops writing (a half close, ``SHUT_WR``) reads as
        closed, which costs a needless new connection and nothing else. A close still on its way,
        or one the server makes after the next write, is not seen.
        """
        if self._sock.fileno() == -1:
            return True
        self._sock.settimeout(0.0)
        try:
            return not self._sock.recv(1, socket.MSG_PEEK)
        except BlockingIOError:
            return False
        except OSError:
            # A reset, or any other error the socket holds: nothing more goes out on it.
            return True
        finally:
            self._sock.settimeout(self._timeout)

    def send(self, data: bytes) -> None:
        """Write ``data`` completely, in pieces of ``send_chunk_bytes``.

        Raises:
            PrinterTimeoutError: One piece was not taken within the timeout: the printer stopped
                taking data.
            TransportError: The connection failed or is closed. A failure while writing (this
                error or a timeout) says how much of the job was written and advises a power
                cycle, since the printer may wait for the rest.
        """
        self._ensure_open()
        self._write(data, request=False)

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
        self._send_request(request)
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
            PrinterTimeoutError: No byte within ``timeout`` seconds, or (``AnswerNotEndedError``)
                the answer did not end within ``total`` seconds.
            PrinterProtocolError: More than :data:`MAX_IDLE_ANSWER_BYTES` arrived.
            TransportError: The connection failed, or the printer closed it before answering.
            ValueError: ``timeout``, ``idle`` or ``total`` is not a usable number of seconds.
        """
        started = time.monotonic()
        check_timeout(timeout)
        check_timeout(idle, name=TimeoutName.IDLE)
        check_timeout(total, name=TimeoutName.TOTAL)
        self._ensure_open()
        self._send_request(request)
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

    def _send_request(self, request: bytes) -> None:
        """Drop what is already waiting, then write ``request``.

        A hang-up (reset, broken pipe) before the request is fully written is ``unanswered``: a
        print server that resets a connection on accept took no request. A request is a few bytes
        that go out in one piece, so a write that failed sent none of it.
        """
        self._discard_waiting()
        self._write(request, request=True)

    def _write(self, data: bytes, *, request: bool) -> None:
        """Write ``data`` completely; a hang-up while writing a ``request`` is ``unanswered``.

        A job whose write fails says how much of it went and that the printer may wait for the
        rest (:func:`~.uri.truncated_job_advice`); a request never does.
        """
        view = memoryview(data)
        piece = self._tuning.send_chunk_bytes
        written = 0
        try:
            # A socket timeout bounds one whole sendall call, not the gap between two bytes, so
            # the piece size decides what the timeout measures. With small pieces (1 KiB by
            # default) it bounds how long the printer took no data: a 64 KiB piece at the 1390
            # bytes/s of a print server feeding a 9600 baud printer needs 47 s and failed a 10 s
            # timeout while the printer was still taking data. Pieces also keep a stalled printer
            # visible on Windows, which takes a single send of any size whole while its buffer is
            # not full (a 64 MB job "sent" to a printer reading nothing, in 0.02 s), so a job sent
            # in one call never times out there.
            for written in range(0, len(view), piece):
                self._sock.sendall(view[written : written + piece])
        except OSError as exc:
            hung_up = request and _is_hang_up(exc)
            # A timeout is the measured case of a printer left waiting for the rest of a job, and a
            # reset or a broken pipe can cut a job off just as well. ``written`` is where the
            # failing piece starts, so part of that piece may have gone too. A request is a few
            # bytes, never a job the printer could wait on.
            advice = "" if request else truncated_job_advice(written=written, size=len(data))
            raise transport_error(
                self._uri, action=ChannelAction.WRITE, error=exc, unanswered=hung_up, advice=advice
            ) from exc

    def _discard_waiting(self) -> None:
        """Read and drop whatever already sits in the receive buffer, without waiting.

        It runs before a request is written, so a hang-up here is ``unanswered``.
        """
        dropped = 0
        try:
            # A zero timeout makes recv raise at once when nothing is waiting.
            self._sock.settimeout(0.0)
            while dropped < _MOST_STALE_BYTES and (stale := self._sock.recv(_RECV_CHUNK)):
                dropped += len(stale)
        except BlockingIOError:
            pass
        except OSError as exc:
            hung_up = _is_hang_up(exc)
            raise transport_error(self._uri, action=ChannelAction.READ, error=exc, unanswered=hung_up) from exc
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
            raise transport_error(self._uri, action=ChannelAction.READ, error=exc, unanswered=hung_up) from exc
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
                raise AnswerNotEndedError(
                    f"tcp {self._uri.target}: the answer did not end within {shown_seconds(total)} s "
                    f"({len(answer)} bytes read)"
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
            raise transport_error(self._uri, action=ChannelAction.READ, error=exc) from exc


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


def _is_hang_up(error: OSError) -> bool:
    """Whether ``error`` means the peer closed the connection under us.

    macOS can report a write to a socket the peer is tearing down as EPROTOTYPE ("Protocol wrong
    type for socket"), a plain ``OSError`` rather than a ``ConnectionError``.

    Example:
        >>> _is_hang_up(BrokenPipeError(errno.EPIPE, "Broken pipe")), _is_hang_up(OSError(errno.EPROTOTYPE, "x"))
        (True, True)
        >>> _is_hang_up(OSError(errno.EBADF, "Bad file descriptor"))
        False
    """
    return isinstance(error, ConnectionError) or error.errno == errno.EPROTOTYPE


class _Connects:
    """How many connects one opening tried, so its failure can count each of them as an attempt."""

    def __init__(self) -> None:
        self.tried = 0


def _connect_retrying_refusals(
    uri: TcpUri, *, timeout: float, tuning: TransportTuning, connects: _Connects
) -> socket.socket:
    """Connect, retrying a refused connection while the retry window is open; count each connect in ``connects``.

    The window is ``tuning.refused_connect_wait`` (at most ``timeout``) and the pause between two
    connects ``tuning.refused_connect_retry_interval``.

    No attempt starts once the window has closed: the refusal of the last attempt inside it is
    raised as is. A host that keeps refusing is therefore reported after the window plus one
    attempt, and an attempt is not instant everywhere - Windows retransmits the SYN and reports a
    refusal only after about 2 s.
    """
    interval = tuning.refused_connect_retry_interval
    deadline = time.monotonic() + min(timeout, tuning.refused_connect_wait)
    while True:
        connects.tried += 1
        try:
            return socket.create_connection((uri.host, uri.port), timeout=timeout)
        except ConnectionRefusedError:
            if time.monotonic() + interval >= deadline:
                raise
        time.sleep(interval)


__all__ = ["MAX_ANSWER_BYTES", "MAX_ANSWER_SECONDS", "MAX_IDLE_ANSWER_BYTES", "TcpTransport"]
