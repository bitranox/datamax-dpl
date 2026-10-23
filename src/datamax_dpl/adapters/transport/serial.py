"""Serial port transport over pyserial.

The transport talks to the port only through :class:`SerialPort`, the handful of pyserial calls it
needs, so the rest of the code never depends on pyserial's wider surface. Ports are opened with
``serial.serial_for_url``, which also takes pyserial's URL handlers (``loop://``, ``rfc2217://``,
``socket://``) as the port path.
"""

from __future__ import annotations

import time
from typing import Protocol

import serial

from datamax_dpl.domain.errors import PrinterProtocolError, PrinterTimeoutError, TransportError

from .tcp import MAX_ANSWER_BYTES
from .uri import ClosingTransport, SerialUri, check_timeout


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

    def reset_input_buffer(self) -> None:
        """Drop every byte received and not read yet."""
        ...

    def close(self) -> None:
        """Close the port."""
        ...


class SerialTransport(ClosingTransport):
    """An open serial port to one printer.

    Use :meth:`open`; the constructor takes an already open :class:`SerialPort` so a caller
    holding one can wrap it.
    """

    def __init__(self, port: SerialPort, *, uri: SerialUri) -> None:
        """Wrap the open ``port``."""
        self._port = port
        self._uri = uri

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
        return cls(port, uri=uri)

    @property
    def bidirectional(self) -> bool:
        """A serial port reads the printer's answers."""
        return True

    def send(self, data: bytes) -> None:
        """Write ``data`` and wait until it has left the port.

        Raises:
            PrinterTimeoutError: The printer held the line (flow control) past the write timeout.
            TransportError: The port failed or is closed.
        """
        try:
            self._port.write(data)
            self._port.flush()
        except serial.SerialTimeoutException as exc:
            raise PrinterTimeoutError(f"serial {self._uri.path}: write timed out") from exc
        except OSError as exc:
            raise TransportError(f"cannot write to serial {self._uri.path}: {exc}") from exc

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
        try:
            self._port.reset_input_buffer()
        except OSError as exc:
            raise TransportError(f"cannot read from serial {self._uri.path}: {exc}") from exc
        self.send(request)
        answer = self._read_lines(terminator, timeout=timeout, lines=lines)
        if answer.endswith(terminator):
            return answer
        if len(answer) >= MAX_ANSWER_BYTES:
            raise PrinterProtocolError(f"serial {self._uri.path}: no answer terminator within {MAX_ANSWER_BYTES} bytes")
        raise PrinterTimeoutError(f"serial {self._uri.path}: no complete answer in time ({len(answer)} bytes read)")

    def close(self) -> None:
        """Close the port; safe to call twice."""
        self._port.close()

    def _read_lines(self, terminator: bytes, *, timeout: float, lines: int) -> bytes:
        """Read ``lines`` terminated chunks within one overall ``timeout``."""
        deadline = time.monotonic() + timeout
        answer = b""
        for _ in range(lines):
            remaining = deadline - time.monotonic()
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
