"""A Ctrl-C the command finds again, however the raise of it ended.

Python raises ``KeyboardInterrupt`` wherever the main thread happens to be, and that can be inside
a callback of a C extension: pydantic-core reports what its ``isinstance`` check of a model instance
raises as "unraisable" and validates on. A batch spends most of its time there, so 3 to 5 of 100
Ctrl-C on a long print were lost and the whole job went out with exit 0. So the handler notes the
interrupt before it raises it, and each pass over the records looks at the note.

A send can render again (a two-way label re-rendered after its directory read, a single label, a
calibration label), so the pass over the records is not the last place a Ctrl-C can be lost. The
printer channel the command opens looks at the note before every byte it sends or asks for, which
is the one place every route that reaches a printer passes, whatever renders before it.
"""

from __future__ import annotations

import signal
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

from ...domain.timing import MAX_ANSWER_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from types import FrameType

    from ...application.ports import OpenPrinter, PrinterTransport


class _Note:
    """Whether a Ctrl-C came during this run: a class attribute, since a signal handler takes no argument."""

    interrupted = False


def _note_and_raise(signum: int, frame: FrameType | None) -> None:
    """Note the interrupt, then raise it as Python's own handler does."""
    _Note.interrupted = True
    signal.default_int_handler(signum, frame)


def raise_if_interrupted() -> None:
    """Raise the Ctrl-C that came during this run and was lost on its way up.

    Raises:
        KeyboardInterrupt: One came.
    """
    if _Note.interrupted:
        raise KeyboardInterrupt


@contextmanager
def noting_interrupts() -> Generator[None, None, None]:
    """Note each Ctrl-C for the length of one run of the command.

    A handler the host set, or ``SIG_IGN`` from a shell that started this in the background, stays:
    it is not ours to replace, and the raise it may make is not ours to find again. Only the main
    thread can set a handler at all.
    """
    _Note.interrupted = False
    owned = threading.current_thread() is threading.main_thread()
    if not owned or signal.getsignal(signal.SIGINT) is not signal.default_int_handler:
        yield
        return
    signal.signal(signal.SIGINT, _note_and_raise)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        _Note.interrupted = False


class _InterruptCheckedPrinter:
    """A printer channel that raises a noted Ctrl-C before each byte it would send or ask for.

    ``peer_closed`` and ``close`` only look and release, so they stay unchecked: the close has to
    run on the way out of the interrupt it would otherwise raise again.
    """

    def __init__(self, printer: PrinterTransport) -> None:
        self._printer = printer

    @property
    def bidirectional(self) -> bool:
        """The channel's own answer."""
        return self._printer.bidirectional

    def send(self, data: bytes) -> None:
        """Send ``data``, unless a Ctrl-C came during this run."""
        raise_if_interrupted()
        self._printer.send(data)

    def query(
        self,
        request: bytes,
        *,
        timeout: float,
        terminator: bytes = b"\r",
        lines: int = 1,
        accept: Callable[[bytes], bool] | None = None,
    ) -> bytes:
        """Ask, unless a Ctrl-C came during this run."""
        raise_if_interrupted()
        return self._printer.query(request, timeout=timeout, terminator=terminator, lines=lines, accept=accept)

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = MAX_ANSWER_SECONDS) -> bytes:
        """Ask, unless a Ctrl-C came during this run."""
        raise_if_interrupted()
        return self._printer.read_answer(request, timeout=timeout, idle=idle, total=total)

    def peer_closed(self) -> bool:
        """The channel's own answer."""
        return self._printer.peer_closed()

    def close(self) -> None:
        """Release the channel."""
        self._printer.close()


def interrupt_checked(open_printer: OpenPrinter) -> OpenPrinter:
    """``open_printer``, with each channel it opens raising a noted Ctrl-C before it sends or asks.

    Outside a run that notes interrupts the note is never set, so the channel behaves as the one
    it wraps.

    Examples:
        >>> from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
        >>> printer = RecordingPrinter()
        >>> channel = interrupt_checked(open_recording_printer(printer))("tcp://192.0.2.1:9100", timeout=1.0)
        >>> channel.send(b"A")
        >>> printer.sent
        [b'A']
    """

    def _open(uri: str, *, timeout: float) -> PrinterTransport:
        raise_if_interrupted()
        return _InterruptCheckedPrinter(open_printer(uri, timeout=timeout))

    return _open


__all__ = ["interrupt_checked", "noting_interrupts", "raise_if_interrupted"]
