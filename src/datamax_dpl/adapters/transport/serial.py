"""Serial port transport over pyserial.

The transport talks to the port only through :class:`SerialPort`, the handful of pyserial calls it
needs, so the rest of the code never depends on pyserial's wider surface. Ports are opened with
``serial.serial_for_url``, which also takes pyserial's URL handlers (``loop://``, ``rfc2217://``,
``socket://``) as the port path.
"""

from __future__ import annotations

import contextlib
import time
from typing import Protocol

import serial

from datamax_dpl.domain.errors import PrinterProtocolError, PrinterTimeoutError, TransportError

from .tcp import MAX_ANSWER_BYTES, MAX_ANSWER_SECONDS, MAX_IDLE_ANSWER_BYTES
from .uri import ClosingTransport, SerialUri, Wait, answer_started, check_timeout, no_answer

_DRAIN_POLL_SECONDS = 0.01


def _has_output_queue(port: object) -> bool:
    """Whether ``port`` is a real serial port, with an output queue of its own.

    A URL handler's ``out_waiting`` means something else (for ``loop://`` the bytes nobody has read
    back yet). The pyserial stubs declare every ``serial_for_url`` result a ``Serial``, but a URL
    handler returns its own ``SerialBase`` subclass, hence the ``object`` parameter.

    Example:
        >>> _has_output_queue(serial.serial_for_url("loop://"))
        False
    """
    return isinstance(port, serial.Serial)


class SerialPort(Protocol):
    """The pyserial calls this transport uses."""

    @property
    def timeout(self) -> float | None:
        """Read timeout in seconds."""
        ...

    @timeout.setter
    def timeout(self, timeout: float | None) -> None: ...

    def write(self, b: bytes, /) -> int | None:
        """Queue ``b`` for sending."""
        ...

    def flush(self) -> None:
        """Wait until every queued byte is sent."""
        ...

    def read_until(self, expected: bytes = ..., size: int | None = ...) -> bytes:
        """Read until ``expected``, ``size`` bytes or the read timeout."""
        ...

    def read(self, size: int = ...) -> bytes:
        """Read ``size`` bytes or what arrived until the read timeout."""
        ...

    @property
    def in_waiting(self) -> int:
        """Bytes received and not read yet."""
        ...

    @property
    def out_waiting(self) -> int:
        """Bytes written and not yet sent."""
        ...

    def reset_input_buffer(self) -> None:
        """Drop every byte received and not read yet."""
        ...

    def reset_output_buffer(self) -> None:
        """Drop every byte written and not yet sent."""
        ...

    def close(self) -> None:
        """Close the port."""
        ...


class SerialTransport(ClosingTransport):
    """An open serial port to one printer.

    Use :meth:`open`; the constructor takes an already open :class:`SerialPort` so a caller
    holding one can wrap it.
    """

    def __init__(self, port: SerialPort, *, uri: SerialUri, drain_timeout: float | None = None) -> None:
        """Wrap the open ``port``.

        Args:
            port: The open port.
            uri: What the port was opened from, for messages.
            drain_timeout: Seconds the port may go without sending a queued byte before
                :meth:`send` gives up, or ``None`` for a port with no output queue of its own (the
                pyserial URL handlers, where written bytes are already gone).
        """
        self._port = port
        self._uri = uri
        self._drain_timeout = drain_timeout

    @classmethod
    def open(cls, uri: SerialUri, *, timeout: float) -> SerialTransport:
        """Open the port ``uri`` names with its line settings.

        Args:
            uri: Port path (or pyserial URL) and line settings.
            timeout: Seconds every write may take.

        Returns:
            The open transport.

        Raises:
            TransportError: The port does not exist, is busy, refuses the settings or cannot be
                opened for lack of permission.
        """
        check_timeout(timeout)
        try:
            port: SerialPort = serial.serial_for_url(
                uri.path,
                baudrate=uri.baud,
                parity=uri.parity.value,
                bytesize=uri.bytesize,
                stopbits=uri.stopbits,
                rtscts=uri.rtscts,
                xonxoff=uri.xonxoff,
                timeout=timeout,
                write_timeout=timeout,
            )
        except (OSError, ValueError) as exc:
            raise TransportError(f"cannot open serial {uri.path}: {exc}") from exc
        drain_timeout = timeout if _has_output_queue(port) else None
        return cls(port, uri=uri, drain_timeout=drain_timeout)

    @property
    def bidirectional(self) -> bool:
        """A serial port reads the printer's answers."""
        return True

    def send(self, data: bytes) -> None:
        """Write ``data`` and wait until it has left the port.

        Raises:
            PrinterTimeoutError: The printer held the line (flow control) past the write timeout,
                or the port sent none of its queued bytes for that long. The unsent bytes are
                dropped, so they never reach the printer later as the tail of a broken job.
            TransportError: The port failed or is closed.
        """
        try:
            self._port.write(data)
        except serial.SerialTimeoutException as exc:
            self._drop_queued_output()
            raise PrinterTimeoutError(f"serial {self._uri.path}: write timed out") from exc
        except OSError as exc:
            raise TransportError(f"cannot write to serial {self._uri.path}: {exc}") from exc
        self._drain()

    def query(self, request: bytes, *, timeout: float, terminator: bytes = b"\r", lines: int = 1) -> bytes:
        """Send ``request`` and return the answer up to and including ``terminator``.

        Bytes received before the request belong to an earlier exchange and are dropped first.

        Raises:
            PrinterTimeoutError: No complete answer within ``timeout`` seconds.
            PrinterProtocolError: More than ``MAX_ANSWER_BYTES`` arrived without a terminator.
            TransportError: The port failed or is closed.
        """
        check_timeout(timeout)
        if not terminator or lines < 1:
            raise ValueError("terminator must not be empty and lines at least 1")
        self._drop_waiting_input()
        self.send(request)
        wait = Wait.since(time.monotonic(), timeout)
        answer = self._read_lines(terminator, wait=wait, lines=lines)
        if answer.endswith(terminator):
            return answer
        if len(answer) >= MAX_ANSWER_BYTES:
            raise PrinterProtocolError(f"serial {self._uri.path}: no answer terminator within {MAX_ANSWER_BYTES} bytes")
        raise no_answer(f"serial {self._uri.path}", wait=wait, received=len(answer))

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = MAX_ANSWER_SECONDS) -> bytes:
        """Send ``request`` and return every byte until ``idle`` seconds pass without a new one.

        Bytes received before the request belong to an earlier exchange and are dropped first.
        XON and XOFF alone do not start the answer: until another byte comes, the wait is the
        first-byte wait.

        Args:
            request: The bytes to send.
            timeout: Seconds to wait for the first byte.
            idle: Seconds of silence that end the answer.
            total: Seconds the whole answer may take, from the request to its ending silence.

        Returns:
            The answer, at most ``MAX_IDLE_ANSWER_BYTES`` long.

        Raises:
            PrinterTimeoutError: No byte within ``timeout`` seconds, or the answer did not end
                within ``total`` seconds.
            PrinterProtocolError: More than ``MAX_IDLE_ANSWER_BYTES`` arrived.
            TransportError: The port failed or is closed.
            ValueError: ``timeout``, ``idle`` or ``total`` is not a usable number of seconds.
        """
        started = time.monotonic()
        check_timeout(timeout)
        check_timeout(idle, name="idle")
        check_timeout(total, name="total")
        self._drop_waiting_input()
        self.send(request)
        answer = self._read_start(Wait.since(started, min(timeout, total)))
        return self._read_rest(answer, idle=idle, end=started + total, total=total)

    def close(self) -> None:
        """Close the port; safe to call twice."""
        self._port.close()

    def _drain(self) -> None:
        """Wait until the output queue is empty; the clock restarts whenever a byte leaves it.

        pyserial's ``flush`` is not used because it waits without any bound: ``tcdrain`` on POSIX
        (on macOS a pty nobody reads holds it forever) and an unbounded poll on Windows. A tty also
        waits for queued output when it is closed, so on a timeout the queue is dropped first.
        """
        if self._drain_timeout is None:
            return
        try:
            waiting = self._port.out_waiting
            deadline = time.monotonic() + self._drain_timeout
            while waiting:
                if time.monotonic() >= deadline:
                    self._drop_queued_output()
                    raise PrinterTimeoutError(
                        f"serial {self._uri.path}: the printer took no data for {self._drain_timeout:g} s "
                        f"({waiting} bytes not sent)"
                    )
                time.sleep(_DRAIN_POLL_SECONDS)
                still_waiting = self._port.out_waiting
                if still_waiting < waiting:
                    deadline = time.monotonic() + self._drain_timeout
                waiting = still_waiting
        except OSError as exc:
            raise TransportError(f"cannot write to serial {self._uri.path}: {exc}") from exc

    def _drop_queued_output(self) -> None:
        """Discard what the port has not sent yet; a port that refuses has nothing to discard."""
        with contextlib.suppress(OSError):
            self._port.reset_output_buffer()

    def _drop_waiting_input(self) -> None:
        """Discard what arrived before a request, which belongs to an earlier exchange."""
        try:
            self._port.reset_input_buffer()
        except OSError as exc:
            raise TransportError(f"cannot read from serial {self._uri.path}: {exc}") from exc

    def _read_start(self, wait: Wait) -> bytes:
        """Read until a byte other than flow control has come, before ``wait`` ends."""
        answer = b""
        while not answer_started(answer):
            remaining = wait.remaining()
            if remaining <= 0:
                raise no_answer(f"serial {self._uri.path}", wait=wait, received=len(answer))
            answer += self._read_available(wait=remaining, room=MAX_IDLE_ANSWER_BYTES + 1 - len(answer))
            self._check_answer_size(answer)
        return answer

    def _read_rest(self, answer: bytes, *, idle: float, end: float, total: float) -> bytes:
        """Read until ``idle`` seconds of silence; past ``end`` the answer did not end."""
        while True:
            wait = min(idle, end - time.monotonic())
            chunk = self._read_available(wait=wait, room=MAX_IDLE_ANSWER_BYTES + 1 - len(answer)) if wait > 0 else b""
            if not chunk and wait >= idle:
                return answer
            if not chunk:
                raise PrinterTimeoutError(
                    f"serial {self._uri.path}: the answer did not end within {total:g} s ({len(answer)} bytes read)"
                )
            answer += chunk
            self._check_answer_size(answer)

    def _check_answer_size(self, answer: bytes) -> None:
        """Refuse an answer past ``MAX_IDLE_ANSWER_BYTES``."""
        if len(answer) > MAX_IDLE_ANSWER_BYTES:
            raise PrinterProtocolError(f"serial {self._uri.path}: answer longer than {MAX_IDLE_ANSWER_BYTES} bytes")

    def _read_available(self, *, wait: float, room: int) -> bytes:
        """Wait up to ``wait`` seconds for a byte, then take what else has arrived, at most ``room`` bytes.

        Empty when nothing came in ``wait`` seconds. Reading one byte first makes the wait end at
        the first byte rather than after the whole ``wait``, so a silence is measured from it.
        """
        try:
            self._port.timeout = wait
            first = self._port.read(1)
            if not first:
                return b""
            return first + self._port.read(min(self._port.in_waiting, room - 1))
        except OSError as exc:
            raise TransportError(f"cannot read from serial {self._uri.path}: {exc}") from exc

    def _read_lines(self, terminator: bytes, *, wait: Wait, lines: int) -> bytes:
        """Read ``lines`` terminated chunks before the one overall ``wait`` ends."""
        answer = b""
        for _ in range(lines):
            remaining = wait.remaining()
            if remaining <= 0:
                break
            chunk = self._read(terminator, timeout=remaining)
            answer += chunk
            if not chunk.endswith(terminator) or len(answer) >= MAX_ANSWER_BYTES:
                break
        return answer

    def _read(self, terminator: bytes, *, timeout: float) -> bytes:
        """Read until ``terminator``, the size bound or ``timeout``, whichever comes first."""
        try:
            self._port.timeout = timeout
            return self._port.read_until(terminator, MAX_ANSWER_BYTES)
        except OSError as exc:
            raise TransportError(f"cannot read from serial {self._uri.path}: {exc}") from exc


__all__ = ["SerialPort", "SerialTransport"]
