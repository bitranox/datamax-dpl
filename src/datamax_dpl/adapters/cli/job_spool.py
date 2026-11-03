"""Keep a batch's records on disk as the check pass rendered them, for the send to read back.

A batch is checked whole before any of it goes out, which renders every record once. Without a
spool the send renders every record a second time; with one it reads the first render back, so a
batch takes one render per record, plus the renders a label's printer read asks for. Memory holds
one record either way: the spool costs disk, about the size of the job plus each record's findings.

The file sits in a directory of its own in the system's temp directory, which only this user may
enter (``mkdtemp``), and is created for this user alone. Both are removed when the spool closes,
on every way out of the ``with`` block a Python exception takes, Ctrl-C included. SIGTERM and SIGHUP
end the process with no handler, which leaves the directory (``datamax-dpl-spool-*``) behind.
"""

from __future__ import annotations

import errno
import os
import struct
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, TypedDict

from ...application.use_cases import BatchEntry
from ...domain._invariants import require_update_fields
from ...domain.render import RenderResult

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import TracebackType

#: Each record's frame: its header's length, its job's length, whether a batch cut follows it.
_FRAME = struct.Struct("<IQ?")
#: O_BINARY exists on Windows only, where a file opened without it rewrites line endings in the job.
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
#: Read and write for this user alone: the job may carry what the records hold.
_FILE_MODE = 0o600
_FILE_NAME = "jobs"


class _DataUpdate(TypedDict):
    """The field a record's render is kept without: its job goes in the frame as raw bytes."""

    data: bytes


require_update_fields(_DataUpdate, RenderResult)


class FileJobSpool:
    """A :class:`~datamax_dpl.application.ports.JobSpool` in a file of its own, removed when it closes.

    The file is made by the first :meth:`add`, so a spool nothing is added to takes no disk.
    Use it as a context manager: leaving the block closes every reader and removes the file.
    """

    def __init__(self) -> None:
        """Start empty, with no file yet."""
        self._directory: Path | None = None
        self._writer: BinaryIO | None = None
        self._readers: set[BinaryIO] = set()
        self._sealed = False

    @property
    def path(self) -> Path | None:
        """The file, None before the first record is added and after the spool is closed."""
        return None if self._directory is None else self._directory / _FILE_NAME

    def _open_writer(self) -> BinaryIO:
        self._directory = Path(tempfile.mkdtemp(prefix="datamax-dpl-spool-"))
        descriptor = os.open(self._directory / _FILE_NAME, _CREATE_FLAGS, _FILE_MODE)
        self._writer = os.fdopen(descriptor, "wb")
        return self._writer

    def add(self, entry: BatchEntry) -> None:
        """Write ``entry`` to the end of the file: its render without the job, then the job's bytes.

        Raises:
            RuntimeError: The spool was sealed.
            OSError: The file could not be made or written (a full disk).
        """
        if self._sealed:
            msg = "the spool is sealed: no record may be added after the check pass"
            raise RuntimeError(msg)
        writer = self._writer or self._open_writer()
        result = entry.result
        header = result.model_copy(update=_DataUpdate(data=b"")).model_dump_json().encode("utf-8")
        writer.write(_FRAME.pack(len(header), len(result.data), entry.cut_after))
        writer.write(header)
        writer.write(result.data)

    def seal(self) -> None:
        """Flush and close the file for writing; from now on it is only read.

        Raises:
            OSError: The bytes still buffered could not be written.
        """
        self._sealed = True
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()

    def entries(self) -> Iterator[BatchEntry]:
        """Read the records back from the first, each call on a handle of its own.

        Raises:
            RuntimeError: The spool is not sealed yet.
        """
        if not self._sealed:
            msg = "the spool is read only once the check pass has sealed it"
            raise RuntimeError(msg)
        path = self.path
        if path is None:
            return iter(())
        stream = path.open("rb")
        self._readers.add(stream)
        return self._read(stream)

    def _read(self, stream: BinaryIO) -> Iterator[BatchEntry]:
        try:
            while frame := stream.read(_FRAME.size):
                header_size, data_size, cut = _FRAME.unpack(_exactly(stream, _FRAME.size, frame))
                result = RenderResult.model_validate_json(_exactly(stream, header_size))
                data = _exactly(stream, data_size)
                yield BatchEntry(result=result.model_copy(update=_DataUpdate(data=data)), cut_after=cut)
        finally:
            self._readers.discard(stream)
            stream.close()

    def close(self) -> None:
        """Close the file's handles and remove it and its directory.

        Raises:
            OSError: The file or its directory could not be removed; the message names it.
        """
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
        for reader in tuple(self._readers):
            reader.close()
        self._readers.clear()
        directory, self._directory = self._directory, None
        if directory is not None:
            (directory / _FILE_NAME).unlink(missing_ok=True)
            directory.rmdir()

    def __enter__(self) -> FileJobSpool:
        """Return the spool."""
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Remove the file. A failure to is raised only when nothing else is: it must not hide why the block ended."""
        try:
            self.close()
        except OSError:
            if exc is None:
                raise


def _exactly(stream: BinaryIO, size: int, start: bytes = b"") -> bytes:
    """Read ``size`` bytes in all, ``start`` being those already read.

    Raises:
        OSError: The file ends before them: it was cut short behind this process's back.
    """
    data = start + stream.read(size - len(start)) if len(start) < size else start
    if len(data) != size:
        raise OSError(errno.EIO, f"the spool file ends inside a record: {len(data)} of {size} bytes", stream.name)
    return data


__all__ = ["FileJobSpool"]
