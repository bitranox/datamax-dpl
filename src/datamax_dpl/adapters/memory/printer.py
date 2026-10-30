"""RecordingPrinter: an in-memory :class:`~datamax_dpl.application.ports.PrinterTransport` double.

It keeps every byte written to it and answers queries from a fixed table, so the CLI and the use
cases can be driven end to end without a printer and the test reads back exactly what would have
gone over the wire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamax_dpl.domain.errors import PrinterTimeoutError, TransportError, UnsupportedOperationError
from datamax_dpl.domain.timing import MAX_ANSWER_SECONDS, check_timeout

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from datamax_dpl.application.ports import OpenPrinter, PrinterTransport


class RecordingPrinter:
    """A fake printer channel that records what is sent and answers from a table.

    Attributes:
        sent: Every chunk written, in order: each :meth:`send` payload and each query request,
            as the bytes would have appeared on the wire.
        queries: The request of each :meth:`query` and :meth:`read_answer`, in order.
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

    def query(
        self,
        request: bytes,
        *,
        timeout: float,
        terminator: bytes = b"\r",
        lines: int = 1,
        accept: Callable[[bytes], bool] | None = None,
    ) -> bytes:
        """Record ``request`` and return its answer from the table.

        With ``accept``, the answer is cut into lines at ``terminator`` and the lines ``accept``
        refuses are skipped, as a real transport skips them: the first ``lines`` kept lines are the
        answer. Without it the whole table entry is returned.

        Raises:
            UnsupportedOperationError: The printer is configured one-way.
            PrinterTimeoutError: The table has no answer for ``request``, or fewer than ``lines``
                lines of it are accepted, like a printer that stays silent.
            TransportError: The printer was closed.

        Example:
            >>> printer = RecordingPrinter(answers={b"\\x01A": b"MODULE: A\\rNNNNNNNN\\r"})
            >>> printer.query(b"\\x01A", timeout=1.0, accept=lambda line: line.startswith(b"N"))
            b'NNNNNNNN\\r'
        """
        answer = self._answer(request, timeout=timeout)
        if accept is None:
            return answer
        kept = [line for line in _lines(answer, terminator) if accept(line)][:lines]
        if len(kept) < lines:
            raise PrinterTimeoutError(f"recording printer: no answer to {request!r} accepted within {timeout} s")
        return b"".join(kept)

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = MAX_ANSWER_SECONDS) -> bytes:
        """Record ``request`` and return its whole answer from the table, as a silence would end it.

        The seconds are checked like a real channel checks them, before anything is recorded.

        Raises:
            ValueError: ``timeout``, ``idle`` or ``total`` is not a usable number of seconds.
            UnsupportedOperationError: The printer is configured one-way.
            PrinterTimeoutError: The table has no answer for ``request``.
            TransportError: The printer was closed.
        """
        check_timeout(timeout)
        check_timeout(idle, name="idle")
        check_timeout(total, name="total")
        return self._answer(request, timeout=timeout)

    def close(self) -> None:
        """Mark the printer closed; safe to call twice."""
        self.closed = True

    def __enter__(self) -> RecordingPrinter:
        """Return the printer itself."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the printer."""
        self.close()

    def _answer(self, request: bytes, *, timeout: float) -> bytes:
        """Record ``request`` on the wire and in :attr:`queries`, and look up its answer."""
        if not self._bidirectional:
            raise UnsupportedOperationError("recording printer is one-way: it cannot read the printer's answer")
        self.send(request)
        self.queries.append(bytes(request))
        if request not in self._answers:
            raise PrinterTimeoutError(f"recording printer: no answer to {request!r} within {timeout} s")
        return self._answers[request]

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


def _lines(answer: bytes, terminator: bytes) -> list[bytes]:
    """The terminated lines of ``answer``, each with its terminator; an unterminated tail is dropped."""
    return [part + terminator for part in answer.split(terminator)[:-1]]


__all__ = ["RecordingPrinter", "open_recording_printer"]
