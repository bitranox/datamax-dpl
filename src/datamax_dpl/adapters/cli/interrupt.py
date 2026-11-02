"""A Ctrl-C the command finds again, however the raise of it ended.

Python raises ``KeyboardInterrupt`` wherever the main thread happens to be, and that can be inside
a callback of a C extension: pydantic-core reports what its ``isinstance`` check of a model instance
raises as "unraisable" and validates on. A batch spends most of its time there, so 3 to 5 of 100
Ctrl-C on a long print were lost and the whole job went out with exit 0. So the handler notes the
interrupt before it raises it, and each pass over the records looks at the note.
"""

from __future__ import annotations

import signal
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import FrameType


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
def noting_interrupts() -> Iterator[None]:
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


__all__ = ["noting_interrupts", "raise_if_interrupted"]
