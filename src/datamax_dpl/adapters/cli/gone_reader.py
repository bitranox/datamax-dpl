"""Tell a reader of the output that went away from a failure that needs saying."""

from __future__ import annotations

import errno
import os


def is_gone_reader_error(exc: OSError, *, windows: bool = os.name == "nt") -> bool:
    """Whether a failed write or flush to a standard stream means its reader left.

    The Windows C runtime reports a pipe with no reader as a bare EINVAL, on a write as much as on a
    flush. A bare one only: EINVAL from a bad file name carries the name, and from a socket a Windows
    error code, and those are real failures that must not read as "the reader left".

    Args:
        exc: The error the stream raised.
        windows: Whether the C runtime's Windows reporting applies.

    Returns:
        True for EPIPE everywhere and for a bare EINVAL on Windows.

    Example:
        >>> is_gone_reader_error(BrokenPipeError(errno.EPIPE, "Broken pipe"), windows=False)
        True
        >>> is_gone_reader_error(OSError(errno.EINVAL, "Invalid argument"), windows=True)
        True
        >>> is_gone_reader_error(OSError(errno.EINVAL, "Invalid argument"), windows=False)
        False
        >>> is_gone_reader_error(OSError(errno.EINVAL, "Invalid argument", "bad?name"), windows=True)
        False
    """
    if exc.errno == errno.EPIPE:
        return True
    bare = exc.filename is None and getattr(exc, "winerror", None) is None
    return windows and exc.errno == errno.EINVAL and bare


__all__ = ["is_gone_reader_error"]
