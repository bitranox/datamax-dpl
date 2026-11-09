"""Write a batch's job to a file as it is rendered, so that a refused batch writes nothing.

A batch is written beside the target and takes its place only when every record passed: a job of
any length needs no memory, and an error in the last record still leaves the target as it was.
A target that would not stay what it was if replaced is written directly, after a pass that checks
every record first and keeps each in a spool (:mod:`.job_spool`), which the write then reads back
rather than rendering every record again: a device or a pipe, a file in a directory that takes no new file, a file this
user may not write (it refuses, as a single label's write does), one owned by someone else (who
would lose it), one with another hard link (which would keep the old bytes), or one with an
extended attribute a new file beside it does not get, or gets with another value (a POSIX ACL of
its own, a ``user.`` attribute, a changed SELinux label).
"""

from __future__ import annotations

import errno
import itertools
import os
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, NamedTuple, Protocol

from .interrupt import raise_if_interrupted
from .job_spool import SpoolWriteError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ...application.ports import JobSpool, OpenJobSpool
    from ...application.use_cases import BatchEntry, BatchReport

#: Names tried beside the target before giving up on a file of our own: another run may hold one.
_TEMP_NAME_TRIES = 100
#: Read-write for everyone before the umask, the mode any ordinary tool creates a file with.
_NEW_FILE_MODE = 0o666
#: O_BINARY exists on Windows only, where a file opened without it rewrites line endings in the job.
_TEMP_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)


class BatchCheck(Protocol):
    """Goes over a batch once (:func:`~datamax_dpl.application.use_cases.check_batch`) and returns its report.

    ``each`` is called with every record as it is checked; ``spool`` keeps each record for the
    write to read back.
    """

    def __call__(
        self, *, each: Callable[[BatchEntry], None] | None = None, spool: JobSpool | None = None
    ) -> BatchReport: ...


class _Beside(NamedTuple):
    """The new file a batch is written to.

    Attributes:
        path: Where it is.
        stream: It, open: the descriptor that made it, so nothing can stand in for it by name.
        mode: The mode it must end with; None for a target that does not exist yet, whose new file
            already has the mode a new file gets.
    """

    path: Path
    stream: BinaryIO
    mode: int | None


class _NewFile(NamedTuple):
    """A file just made beside the target, not yet wrapped in a stream.

    Attributes:
        path: Where it is.
        descriptor: The descriptor that made it.
    """

    path: Path
    descriptor: int


def _named_for(exc: OSError, target: str) -> OSError:
    """The same error naming ``target``: a reader never asked for the file beside it."""
    return OSError(exc.errno, exc.strerror, target)


class ReadAttributes(Protocol):
    """Reads a file's extended attributes, each name with its value; None where they cannot be read."""

    def __call__(self, path: Path, /) -> Mapping[str, bytes] | None: ...


def extended_attributes(path: Path) -> Mapping[str, bytes] | None:
    """``path``'s extended attributes, a POSIX ACL among them, each with its value; None where unknown.

    Linux alone, the one platform whose ``os`` reads them; elsewhere, and on a file system that
    keeps none, the answer is none.
    """
    if sys.platform != "linux":
        return {}
    try:
        return {name: os.getxattr(path, name) for name in os.listxattr(path)}
    except OSError as exc:
        # A file system that keeps none answers ENOTSUP; any other error, one removed between the
        # listing and its read included, leaves them unknown.
        return {} if exc.errno in {errno.ENOTSUP, errno.EOPNOTSUPP} else None


def _loses_attributes(target: Path, temp: Path, read_attributes: ReadAttributes) -> bool:
    """Whether the new file ``temp`` lacks an extended attribute ``target`` has, or has it with another value.

    Compared with a file just made beside it, not with none: a system that labels every file
    (SELinux puts ``security.selinux`` on each, a directory's default ACL puts
    ``system.posix_acl_access``) gives the new one the same names, and a target written in place
    is truncated first, so a write that fails then leaves it cut. The values count as well as the
    names: a target whose own ACL or label differs from what a new file gets would otherwise take
    the new file's, which can widen who may read it. Unknown attributes count as lost: the target
    is then written in place rather than replaced by a file that may lack them.
    """
    kept = read_attributes(target)
    if kept is None:
        return True
    if not kept:
        return False
    new = read_attributes(temp)
    return new is None or any(new.get(name) != value for name, value in kept.items())


def _replaceable(path: Path, found: os.stat_result) -> bool:
    """Whether a new file could take the place of the file ``path`` with nobody the worse for it."""
    if not os.access(path, os.W_OK) or found.st_nlink > 1:
        return False
    # No effective user id to compare on Windows, where st_uid reads 0 for every file.
    if sys.platform == "win32":
        return True
    return found.st_uid == os.geteuid()


def _create_temp(target: Path, mode: int) -> _NewFile | None:
    """Create a file of our own beside ``target`` with ``mode``, as the umask allows; None when none can be."""
    for attempt in itertools.count():
        temp = target.with_name(f".{target.name}.{os.getpid()}.{attempt}.tmp")
        try:
            # With the mode from the start: a target its owner made private stays so for the whole run.
            return _NewFile(temp, os.open(temp, _TEMP_FLAGS, mode))
        except FileExistsError:
            if attempt >= _TEMP_NAME_TRIES:
                return None
        except OSError:
            return None
    return None


def _remove(temp: Path) -> None:
    """Take the new file away; Windows refuses to delete a read-only one, whatever went wrong before."""
    try:
        temp.unlink(missing_ok=True)
    except PermissionError:
        temp.chmod(stat.S_IWRITE)
        temp.unlink(missing_ok=True)


def _open_beside(target: Path, found: os.stat_result | None, read_attributes: ReadAttributes) -> _Beside | None:
    """Make a new file beside ``target``, whose stat is ``found``, for the job; None for the direct route."""
    if found is not None and not _replaceable(target, found):
        return None
    mode = None if found is None else stat.S_IMODE(found.st_mode)
    created = _create_temp(target, _NEW_FILE_MODE if mode is None else mode)
    if created is None:
        return None
    temp, descriptor = created
    # A directory can give a new file another group than the target's, whose members would lose it.
    other_group = found is not None and os.fstat(descriptor).st_gid != found.st_gid
    if other_group or (found is not None and _loses_attributes(target, temp, read_attributes)):
        os.close(descriptor)
        _remove(temp)
        return None
    return _Beside(temp, os.fdopen(descriptor, "wb"), mode)


def _closed(stream: BinaryIO) -> OSError | None:
    """Close ``stream`` and return what that raised: bytes that would not go are flushed once more here."""
    try:
        stream.close()
    except OSError as exc:
        return exc
    return None


def _write_beside(
    target: Path,
    beside: _Beside,
    check: BatchCheck,
) -> BatchReport:
    """Render the batch into the new file, and put it in ``target``'s place if every record passed."""
    failed: list[OSError] = []
    bad_record = [False]

    def write(entry: BatchEntry) -> None:
        # A record in error refuses the job, so nothing after it is worth the disk; a full
        # disk matters only to a job that would have been written, as the verdict comes first.
        bad_record[0] = bad_record[0] or not entry.result.ok
        if bad_record[0] or failed:
            return
        try:
            beside.stream.write(entry.job)
        except OSError as exc:
            failed.append(exc)

    try:
        try:
            report = check(each=write)
        finally:
            # Kept, not raised: a bad record or an exception on its way out says more than the disk
            # that also filled up.
            close_failed = _closed(beside.stream)
        raise_if_interrupted()
        if report.ok:
            if failed:
                raise failed[0]
            if close_failed is not None:
                raise close_failed
            if beside.mode is not None:
                # Again at the end: the umask may have narrowed what the file was created with.
                beside.path.chmod(beside.mode)
            beside.path.replace(target)
        return report
    finally:
        _remove(beside.path)


def _write_direct(output: str, check: BatchCheck, *, open_spool: OpenJobSpool) -> BatchReport:
    """Check every record into a spool, then write the job from it to ``output``, which refuses or takes it."""
    with open_spool() as spool:
        report = check(spool=spool)
        if report.ok:
            with Path(output).open("wb") as stream:
                for job in report.record_jobs():
                    stream.write(job)
    return report


def _route(output: str, check: BatchCheck, *, open_spool: OpenJobSpool, read_attributes: ReadAttributes) -> BatchReport:
    """Write beside the target where that leaves it what it was, else directly."""
    try:
        # The path as given: /dev/stdout on a pipe resolves to a name nothing can open.
        found = Path(output).stat()
    except FileNotFoundError:
        found = None
    except OSError:
        return _write_direct(output, check, open_spool=open_spool)
    if found is not None and not stat.S_ISREG(found.st_mode):
        return _write_direct(output, check, open_spool=open_spool)
    # A link is written through, not replaced by a file of its own.
    target = Path(os.path.realpath(output))
    beside = _open_beside(target, found, read_attributes)
    if beside is None:
        return _write_direct(output, check, open_spool=open_spool)
    return _write_beside(target, beside, check)


def write_batch(
    output: str,
    check: BatchCheck,
    *,
    open_spool: OpenJobSpool,
    read_attributes: ReadAttributes = extended_attributes,
) -> BatchReport:
    """Write the job of a batch to the file ``output`` if every record passes; else leave it as it was.

    Args:
        output: The file.
        check: Goes over the batch once, with what it is given to call with each record or to
            keep each in (:func:`~datamax_dpl.application.use_cases.check_batch`), and returns its report.
        open_spool: Makes the spool a target written directly is checked into first.
        read_attributes: Reads a file's extended attributes; a target with one a new file beside
            it lacks, or gets with another value, is written directly, so that it keeps them.

    Returns:
        The report. Nothing was written unless its ``ok`` is True.

    Raises:
        SpoolWriteError: The spool a direct write is checked into could not be written; the
            message names it, in the temp directory.
        OSError: The file could not be written; the message names ``output``.
    """
    try:
        return _route(output, check, open_spool=open_spool, read_attributes=read_attributes)
    except SpoolWriteError:
        raise
    except OSError as exc:
        raise _named_for(exc, output) from exc


__all__ = ["BatchCheck", "ReadAttributes", "extended_attributes", "write_batch"]
