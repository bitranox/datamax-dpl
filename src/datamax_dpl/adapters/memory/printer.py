"""RecordingPrinter: an in-memory :class:`~datamax_dpl.application.ports.PrinterTransport` double.

It keeps every byte written to it and answers queries from a fixed table, so the CLI and the use
cases can be driven end to end without a printer and the test reads back exactly what would have
gone over the wire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamax_dpl.domain.errors import PrinterTimeoutError, TransportError, UnsupportedOperationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from datamax_dpl.application.ports import OpenPrinter, PrinterTransport


class RecordingPrinter:
    """A fake printer channel that records what is sent and answers from a table.

    Attributes:
        sent: Every chunk written, in order: each :meth:`send` payload and each query request,
            as the bytes would have appeared on the wire.
        queries: The request of each :meth:`query`, in order.
        opened: ``(uri, timeout)`` of every open through :func:`open_recording_printer`.
        closed: True once :meth:`close` ran.

    Example:
        >>> printer = RecordingPrinter(answers={b"\\x01A": b"NNNNNNNN\\r"})
        >>> printer.send(b"job")
        >>> printer.query(b"\\x01A", timeout=1.0)
        b'NNNNNNNN\\r'
        >>> printer.sent
        [b'job', b'\\x01A']
    """

    def __init__(self, *, answers: Mapping[bytes, bytes] | None = None, bidirectional: bool = True) -> None:
        """Answer each request in ``answers``; ``bidirectional`` False makes every query refused."""
        self._answers: dict[bytes, bytes] = dict(answers or {})
        self._bidirectional = bidirectional
        self.sent: list[bytes] = []
        self.queries: list[bytes] = []
        self.opened: list[tuple[str, float]] = []
        self.closed = False

    @property
    def bidirectional(self) -> bool:
        """As configured."""
        return self._bidirectional

    @property
    def data(self) -> bytes:
        """Everything written so far, joined."""
        return b"".join(self.sent)

    def send(self, data: bytes) -> None:
        """Record ``data``.

        Raises:
            TransportError: The printer was closed, as a real channel would refuse.
        """
        self._ensure_open()
        self.sent.append(bytes(data))

    def query(self, request: bytes, *, timeout: float, terminator: bytes = b"\r") -> bytes:
        """Record ``request`` and return its answer from the table.

        Raises:
            UnsupportedOperationError: The printer is configured one-way.
            PrinterTimeoutError: The table has no answer for ``request``, like a printer that
                stays silent.
            TransportError: The printer was closed.
        """
        if not self._bidirectional:
            raise UnsupportedOperationError("recording printer is one-way: it cannot read the printer's answer")
        self.send(request)
        self.queries.append(bytes(request))
        if request not in self._answers:
            raise PrinterTimeoutError(f"recording printer: no answer to {request!r} within {timeout} s")
        return self._answers[request]

    def close(self) -> None:
        """Mark the printer closed; safe to call twice."""
        self.closed = True

    def __enter__(self) -> RecordingPrinter:
        """Return the printer itself."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the printer."""
        self.close()

    def _ensure_open(self) -> None:
        """Refuse use after close."""
        if self.closed:
            raise TransportError("recording printer: the transport is closed")


def open_recording_printer(printer: RecordingPrinter) -> OpenPrinter:
    """Return an :class:`~datamax_dpl.application.ports.OpenPrinter` that always opens ``printer``.

    Each open records ``(uri, timeout)`` in ``printer.opened`` and reopens it, so a use case that
    opens, closes and opens again keeps writing into the same record.

    Example:
        >>> printer = RecordingPrinter()
        >>> opener = open_recording_printer(printer)
        >>> opener("tcp://label-printer:9100", timeout=2.0) is printer
        True
        >>> printer.opened
        [('tcp://label-printer:9100', 2.0)]
    """

    def _open(uri: str, *, timeout: float) -> PrinterTransport:
        printer.opened.append((uri, timeout))
        printer.closed = False
        return printer

    return _open


__all__ = ["RecordingPrinter", "open_recording_printer"]
