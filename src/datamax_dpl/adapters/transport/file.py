"""One-way transports that keep the job instead of printing it: a file, or standard output.

A file is opened in append mode, so opening it never destroys what an earlier job left there and
every send of one session lands after the previous one, as it would on a printer.
"""

from __future__ import annotations

import os
import sys
from typing import BinaryIO

from datamax_dpl.domain.errors import TransportError

from .device import WRITE_FLAGS, DescriptorTransport
from .uri import ClosingTransport, FileUri, StdoutUri, channel_name, transport_error, unsupported_query

# Read-write for everyone before the umask, the mode any ordinary tool creates a file with.
_NEW_FILE_MODE = 0o666


class FileTransport(DescriptorTransport):
    """A file the job bytes are appended to."""

    @classmethod
    def open(cls, uri: FileUri) -> FileTransport:
        """Open (and create when missing) the file ``uri`` names for appending.

        Raises:
            TransportError: The directory does not exist or the file cannot be written.
        """
        try:
            fd = os.open(uri.path, WRITE_FLAGS | os.O_CREAT, _NEW_FILE_MODE)
        except OSError as exc:
            raise transport_error(uri, action="open", error=exc) from exc
        return cls(fd, uri=uri)


class StdoutTransport(ClosingTransport):
    """Standard output, written as raw bytes."""

    def __init__(self, stream: BinaryIO | None = None) -> None:
        """Write to ``stream``, or to ``sys.stdout.buffer`` as it is at construction time.

        Raises:
            TransportError: ``stream`` is None and standard output has no byte buffer (it is
                closed, or replaced by a text-only stream).
        """
        self._uri = StdoutUri()
        self._stream = _stdout_bytes() if stream is None else stream

    @property
    def bidirectional(self) -> bool:
        """Standard output is written only."""
        return False

    def send(self, data: bytes) -> None:
        """Write ``data`` and flush, so a reader on a pipe gets it at once.

        Raises:
            TransportError: The write failed, for example because the reader went away.
        """
        _write(self._stream, data, uri=self._uri)

    def query(self, request: bytes, *, timeout: float, terminator: bytes = b"\r", lines: int = 1) -> bytes:
        """Refuse: standard output cannot answer.

        Raises:
            UnsupportedOperationError: Always.
        """
        raise unsupported_query(self._uri)

    def close(self) -> None:
        """Flush; standard output itself stays open for the rest of the process."""
        try:
            self._stream.flush()
        except (OSError, ValueError):
            # A reader that went away, or a stream closed elsewhere, leaves nothing to flush to.
            return


def _stdout_bytes() -> BinaryIO:
    """Return the byte stream under ``sys.stdout``, refusing a stdout that has none."""
    buffer: BinaryIO | None = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        raise TransportError("cannot write to stdout: standard output is not a byte stream")
    return buffer


def _write(stream: BinaryIO, data: bytes, *, uri: StdoutUri) -> None:
    """Write and flush ``data``, wrapping the failure into a ``TransportError``."""
    try:
        stream.write(data)
        stream.flush()
    except OSError as exc:
        raise transport_error(uri, action="write to", error=exc) from exc
    except ValueError as exc:
        raise TransportError(f"cannot write to {channel_name(uri)}: the stream is closed") from exc


__all__ = ["FileTransport", "StdoutTransport"]
