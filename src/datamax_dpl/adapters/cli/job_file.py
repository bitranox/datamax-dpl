"""Write a batch's job to a file as it is rendered, so that a refused batch writes nothing.

A batch is written beside the target and takes its place only when every record passed: a job of
any length needs no memory, and an error in the last record still leaves the target as it was.
A target that cannot be replaced that way (a device such as ``/dev/null``, a file in a directory
that takes no new file) is written directly, after a pass that checks every record first.
"""

from __future__ import annotations

import itertools
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...application.use_cases import BatchEntry, BatchReport

#: Names tried beside the target before giving up on a file of our own: another run may hold one.
_TEMP_NAME_TRIES = 100


def _named_for(exc: OSError, target: str) -> OSError:
    """The same error naming ``target``: a reader never asked for the file beside it."""
    return OSError(exc.errno, exc.strerror, target)


def _open_beside(target: Path) -> tuple[Path, int | None] | None:
    """Make a new file beside ``target`` for the job, and return it with the mode it must end with.

    Returns:
        The new file's path and the target's mode, None for a target that does not exist yet (the
        new file already has the mode a new file gets); None instead of a pair where the file
        cannot be used: the target is no regular file, or the directory takes no new file.
    """
    try:
        found = target.stat()
    except FileNotFoundError:
        mode = None
    except OSError:
        return None
    else:
        if not stat.S_ISREG(found.st_mode):
            return None
        mode = stat.S_IMODE(found.st_mode)
    for attempt in itertools.count():
        temp = target.with_name(f".{target.name}.{os.getpid()}.{attempt}.tmp")
        try:
            temp.open("xb").close()
        except FileExistsError:
            if attempt >= _TEMP_NAME_TRIES:
                return None
        except OSError:
            return None
        else:
            return temp, mode
    return None


def _write_beside(
    target: Path,
    temp: Path,
    mode: int | None,
    check: Callable[[Callable[[BatchEntry], None]], BatchReport],
) -> BatchReport:
    """Render the batch into ``temp``, and put it in ``target``'s place if every record passed."""
    failed: list[OSError] = []
    bad_record = [False]
    try:
        with temp.open("wb") as stream:

            def write(entry: BatchEntry) -> None:
                # A record in error refuses the job, so nothing after it is worth the disk; a full
                # disk matters only to a job that would have been written, as the verdict comes first.
                bad_record[0] = bad_record[0] or not entry.result.ok
                if bad_record[0] or failed:
                    return
                try:
                    stream.write(entry.job)
                except OSError as exc:
                    failed.append(exc)

            report = check(write)
        if report.ok:
            if failed:
                raise failed[0]
            if mode is not None:
                temp.chmod(mode)
            temp.replace(target)
        return report
    finally:
        temp.unlink(missing_ok=True)


def write_batch(output: str, check: Callable[[Callable[[BatchEntry], None]], BatchReport]) -> BatchReport:
    """Write the job of a batch to the file ``output`` if every record passes; else leave it as it was.

    Args:
        output: The file.
        check: Goes over the batch once, calling what it is given with each record
            (:func:`~datamax_dpl.application.use_cases.check_batch`), and returns its report.

    Returns:
        The report. Nothing was written unless its ``ok`` is True.

    Raises:
        OSError: The file could not be written; the message names ``output``.
    """
    # A link is written through, not replaced by a file of its own.
    target = Path(os.path.realpath(output))
    beside = _open_beside(target)
    try:
        if beside is not None:
            return _write_beside(target, beside[0], beside[1], check)
        report = check(lambda _entry: None)
        if report.ok:
            with target.open("wb") as stream:
                for job in report.record_jobs():
                    stream.write(job)
    except OSError as exc:
        raise _named_for(exc, output) from exc
    return report


__all__ = ["write_batch"]
