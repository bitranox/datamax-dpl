"""An exception's message as a user reads it, for every adapter that puts one into its own message.

An ``OSError``'s ``str()`` quotes the file name with ``repr``, which doubles every backslash (a path
nobody typed on Windows), and a library that wraps one (pyserial's ``SerialException``) repeats
the name and the ``[Errno N]`` in its own text. A message that names the file itself wants only the
reason.
"""

from __future__ import annotations

import os


def _file_name(name: object) -> str:
    """A file name as the user gave it: one that is not UTF-8 keeps its bytes as surrogates."""
    return os.fsdecode(name) if isinstance(name, (str, bytes)) else str(name)


def error_text(exc: BaseException) -> str:
    """The exception's message as a user reads it, or its class where it has none (a bare ``MemoryError()``).

    An ``OSError`` naming a file says its reason and the file as given.

    Args:
        exc: The exception to describe.

    Returns:
        One line of text for a message.

    Example:
        >>> error_text(OSError(2, "No such file or directory", "C:\\\\temp\\\\job.dpl"))
        'No such file or directory: C:\\\\temp\\\\job.dpl'
        >>> error_text(OSError(28, "No space left on device"))
        '[Errno 28] No space left on device'
        >>> error_text(MemoryError())
        'MemoryError'
    """
    if isinstance(exc, OSError) and exc.strerror and exc.filename is not None:
        names = [_file_name(exc.filename)]
        if exc.filename2 is not None:
            names.append(_file_name(exc.filename2))
        return f"{exc.strerror}: {' -> '.join(names)}"
    return str(exc) or type(exc).__name__


def os_reason(exc: BaseException) -> str:
    """Why an operation failed, for a message that already names the file or port it was on.

    The innermost ``OSError`` with a reason, along the chain of causes, gives it: a library that
    caught one and raised its own (pyserial raises ``SerialException`` from inside ``except
    OSError``) wraps the name and the errno into its text, and the inner one holds the reason alone.
    With none on the chain, :func:`error_text` of ``exc``.

    Args:
        exc: The exception to describe.

    Returns:
        One line of text for a message.

    Example:
        >>> try:
        ...     try:
        ...         raise FileNotFoundError(2, "No such file or directory", "/dev/ttyS9")
        ...     except OSError as inner:
        ...         raise OSError(2, f"could not open port /dev/ttyS9: {inner}")
        ... except OSError as outer:
        ...     os_reason(outer)
        'No such file or directory'
        >>> os_reason(ValueError("baud rate out of range"))
        'baud rate out of range'
    """
    found: OSError | None = None
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and current.strerror:
            found = current
        current = current.__cause__ or current.__context__
    return found.strerror if found is not None and found.strerror else error_text(exc)


__all__ = ["error_text", "os_reason"]
