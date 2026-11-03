"""Keep a batch's records on disk as the check pass rendered them, for the send to read back.

A batch is checked whole before any of it goes out, which renders every record once. Without a
spool the send renders every record a second time; with one it reads the first render back, so a
batch takes one render per record, plus the renders a label's printer read asks for, which fill
from the values kept with it. Memory holds one record either way. The spool costs disk: per record
its job, the record's values, and about 400 bytes of what the send reads besides (the render's
fields as JSON), plus the text of each finding the batch's report keeps in full, which is at most
the report's limit over the whole batch; the other findings are kept as counts by code.

The file sits in a directory of its own in the system's temp directory, made by ``mkdtemp`` with
mode 0o700, and is created with mode 0o600. On POSIX only this user may enter the directory and read
the file. On Windows the modes give no access control: Python from 3.12.4 gives the directory an
ACL for this user alone (CVE-2024-4030), while an earlier Windows Python (3.10.11, the last 3.10
installer) leaves it the temp directory's inherited ACL. A user's own ``%TEMP%`` is that user's
alone anyway; a shared one (``C:\\Windows\\Temp``) then lets other users read it. Both are removed
when the spool closes, on every way out of the ``with`` block a Python exception takes, Ctrl-C
included, and Ctrl-Break on Windows. SIGTERM and SIGHUP end the process with no handler, as closing
the console window does on Windows, which leaves the directory (``datamax-dpl-spool-*``) behind.
"""

from __future__ import annotations

import errno
import os
import struct
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, TypedDict

from pydantic import BaseModel, ConfigDict

from ...application.use_cases import BatchEntry
from ...domain._invariants import require_update_fields
from ...domain.enums import FindingCode
from ...domain.errors import DatamaxDplError
from ...domain.frozen_mapping import FrozenMapping
from ...domain.render import RenderResult

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
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


class _Header(BaseModel):
    """What a spool keeps of a record besides its job: what the send reads, and the values it may render again from.

    Attributes:
        result: The record's render without its job, holding only the findings the report kept.
        record: The values the record was checked with.
        unkept: How many findings of each code it has beyond ``result.findings``.
    """

    # Only keys are cached: a batch reads one header back per record, and its values are new strings.
    model_config = ConfigDict(frozen=True, extra="forbid", cache_strings="keys")

    result: RenderResult
    record: dict[str, str] | None
    unkept: dict[FindingCode, int]


class SpoolWriteError(DatamaxDplError, OSError):
    """The spool's file could not be written or removed: the message names it, in the temp directory.

    Its own class, so that a caller naming the file it writes for the user leaves this one named.
    """


def _named(exc: OSError, path: Path, doing: str) -> SpoolWriteError:
    """``exc`` as a :class:`SpoolWriteError` naming the spool's ``path`` and what failed there."""
    reason = exc.strerror or type(exc).__name__
    return SpoolWriteError(exc.errno, f"{reason}, {doing} the batch's spool", str(path))


def _buffered(descriptor: int) -> BinaryIO:
    """The descriptor as a buffered binary file, the writer a spool uses by default."""
    return os.fdopen(descriptor, "wb")


class FileJobSpool:
    """A :class:`~datamax_dpl.application.ports.JobSpool` in a file of its own, removed when it closes.

    The file is made by the first :meth:`add`, so a spool nothing is added to takes no disk.
    Use it as a context manager: leaving the block closes every reader and removes the file.
    """

    def __init__(self, *, open_writer: Callable[[int], BinaryIO] = _buffered) -> None:
        """Start empty, with no file yet.

        Args:
            open_writer: Makes the writer from the file's descriptor; a test gives one that fails.
        """
        self._open_file = open_writer
        self._directory: Path | None = None
        self._writer: BinaryIO | None = None
        self._readers: set[BinaryIO] = set()
        self._sealed = False
        # Closing removes the file, so a closed spool must not read as a sealed one with nothing in it.
        self._closed = False

    @property
    def path(self) -> Path | None:
        """The file, None before the first record is added and after the spool is closed."""
        return None if self._directory is None else self._directory / _FILE_NAME

    def _open_writer(self) -> BinaryIO:
        self._directory = Path(tempfile.mkdtemp(prefix="datamax-dpl-spool-"))
        descriptor = os.open(self._directory / _FILE_NAME, _CREATE_FLAGS, _FILE_MODE)
        self._writer = self._open_file(descriptor)
        return self._writer

    def add(self, entry: BatchEntry) -> None:
        """Write ``entry`` to the end of the file: its render without the job, then the job's bytes.

        Raises:
            RuntimeError: The spool was sealed or closed.
            SpoolWriteError: The file could not be made or written (a full disk); nothing more can
                be added then, and the spool takes no more disk than it has.
        """
        self._refuse_closed()
        if self._sealed:
            msg = "the spool is sealed: no record may be added after the check pass"
            raise RuntimeError(msg)
        result = entry.result
        header = _Header(
            result=result.model_copy(update=_DataUpdate(data=b"")),
            record=None if entry.record is None else dict(entry.record),
            unkept=dict(entry.unkept),
        )
        header_bytes = header.model_dump_json().encode("utf-8")
        try:
            writer = self._writer or self._open_writer()
            writer.write(_FRAME.pack(len(header_bytes), len(result.data), entry.cut_after))
            writer.write(header_bytes)
            writer.write(result.data)
        except OSError as exc:
            self._sealed = True
            # Closed now, its error dropped: it is the one being raised, flushed again. A writer
            # left open would raise it once more when the spool closes, over a refusal that the
            # check pass still owes its caller.
            self._drop_writer()
            raise _named(exc, self._where(), "writing") from exc

    def _refuse_closed(self) -> None:
        if self._closed:
            msg = "the spool was closed and its file removed: it holds no records any more"
            raise RuntimeError(msg)

    def _where(self) -> Path:
        """The spool's file, or the temp directory where it was to be made."""
        return self.path or Path(tempfile.gettempdir())

    def _drop_writer(self) -> None:
        writer, self._writer = self._writer, None
        if writer is not None:
            try:
                writer.close()
            except OSError:
                return

    def seal(self) -> None:
        """Flush and close the file for writing; from now on it is only read.

        Raises:
            SpoolWriteError: The bytes still buffered could not be written.
        """
        self._sealed = True
        writer, self._writer = self._writer, None
        if writer is not None:
            try:
                writer.close()
            except OSError as exc:
                raise _named(exc, self._where(), "writing") from exc

    def entries(self) -> Iterator[BatchEntry]:
        """Read the records back from the first, each call on a handle of its own.

        Raises:
            RuntimeError: The spool is not sealed yet, or was closed.
        """
        self._refuse_closed()
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
                header = _Header.model_validate_json(_exactly(stream, header_size))
                data = _exactly(stream, data_size)
                yield BatchEntry(
                    result=header.result.model_copy(update=_DataUpdate(data=data)),
                    cut_after=cut,
                    record=header.record,
                    unkept=FrozenMapping(header.unkept),
                )
        finally:
            self._readers.discard(stream)
            stream.close()

    def close(self) -> None:
        """Close the file's handles and remove it and its directory, whatever closing a handle raised.

        Raises:
            SpoolWriteError: A handle could not be closed (its last bytes would not go), or the file
                or its directory could not be removed; the first of these, naming the file.
        """
        self._closed = True
        failed: list[SpoolWriteError] = []
        where = self._where()
        writer, self._writer = self._writer, None
        handles = ([writer] if writer is not None else []) + list(self._readers)
        self._readers.clear()
        failed.extend(_named(exc, where, "closing") for handle in handles if (exc := _close_error(handle)) is not None)
        directory, self._directory = self._directory, None
        if directory is not None:
            try:
                (directory / _FILE_NAME).unlink(missing_ok=True)
                directory.rmdir()
            except OSError as exc:
                failed.append(_named(exc, directory, "removing"))
        if failed:
            raise failed[0]

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


def _close_error(handle: BinaryIO) -> OSError | None:
    """Close ``handle`` and return what that raised: the next handle is closed whatever this one did."""
    try:
        handle.close()
    except OSError as exc:
        return exc
    return None


def _exactly(stream: BinaryIO, size: int, start: bytes = b"") -> bytes:
    """Read ``size`` bytes in all, ``start`` being those already read.

    Raises:
        OSError: The file ends before them: it was cut short behind this process's back.
    """
    data = start + stream.read(size - len(start)) if len(start) < size else start
    if len(data) != size:
        raise OSError(errno.EIO, f"the spool file ends inside a record: {len(data)} of {size} bytes", stream.name)
    return data


__all__ = ["FileJobSpool", "SpoolWriteError"]
