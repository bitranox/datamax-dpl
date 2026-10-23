"""Take a photo by picking up the newest image a camera drops into a folder (``kind = "file"``).

Cameras and phone apps that upload into a watched folder are handled here: the newest
``.jpg``/``.jpeg``/``.png`` whose modification time is at or after the moment the capture began
(minus :data:`MTIME_GRACE_SECONDS`, for coarse file-system timestamps and clock skew of a
network share) is copied into the output directory. A file is taken only once its size has
been the same on two consecutive polls, so an upload still in progress is not collected half
written.
"""

from __future__ import annotations

import shutil
import stat
import time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from ...domain.errors import CameraError

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...domain.camera import CameraSettings

#: Image file suffixes picked up, and the media type each one is reported with.
MEDIA_TYPE_BY_SUFFIX: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

#: A file this many seconds older than the start of the capture still counts as new.
MTIME_GRACE_SECONDS = 1.0

#: Pause between two looks at the folder.
POLL_INTERVAL_SECONDS = 0.25


class _Candidate(NamedTuple):
    """One image file seen in the folder, with the size it had when seen."""

    path: Path
    size: int


def capture_from_directory(
    settings: CameraSettings,
    *,
    stem: Path,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Path:
    """Wait for a new image in ``settings.directory`` and copy it next to ``stem``.

    Args:
        settings: The camera settings; ``directory`` and ``timeout`` are used.
        stem: Output path without extension; the source file's (lower-cased) suffix is kept.
        sleep: Pause between polls; injected so tests do not wait. Typed as a plain callable
            because ``time.sleep`` takes its argument positional-only, which the
            ``ports.Sleep`` Protocol (keyword-capable ``seconds``) does not accept.
        clock: Monotonic seconds for the deadline; injected so tests control time.

    Returns:
        The path of the copy.

    Raises:
        CameraError: No directory is configured, it cannot be read, or no new complete image
            appeared within ``settings.timeout`` seconds.
    """
    directory = _directory(settings)
    since = time.time() - MTIME_GRACE_SECONDS
    deadline = clock() + settings.timeout
    previous: _Candidate | None = None
    while True:
        current = _newest_image(directory, since=since)
        if current is not None and current == previous:
            return _copy(current.path, stem=stem)
        if clock() >= deadline:
            raise CameraError(f"no new complete image appeared in {directory} within {settings.timeout:g} s")
        previous = current
        sleep(POLL_INTERVAL_SECONDS)


def _directory(settings: CameraSettings) -> Path:
    """Return the configured folder, refusing an empty setting or a path that is not a folder."""
    if not settings.directory:
        raise CameraError("no camera directory configured: set [camera] directory")
    directory = Path(settings.directory)
    if not directory.is_dir():
        raise CameraError(f"camera directory {directory} does not exist or is not a directory")
    return directory


def _newest_image(directory: Path, *, since: float) -> _Candidate | None:
    """Return the newest non-empty image modified at or after ``since``, or None."""
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise CameraError(f"camera directory {directory} cannot be read: {exc.strerror or exc}") from exc
    best: tuple[float, _Candidate] | None = None
    for entry in entries:
        seen = _image_stat(entry, since=since)
        if seen is not None and (best is None or seen[0] > best[0]):
            best = seen
    return None if best is None else best[1]


def _image_stat(entry: Path, *, since: float) -> tuple[float, _Candidate] | None:
    """Return ``(mtime, candidate)`` for a new, non-empty image file, else None."""
    if entry.suffix.lower() not in MEDIA_TYPE_BY_SUFFIX:
        return None
    try:
        info = entry.stat()
    except OSError:
        # The camera software may rename or delete a file between the listing and the stat.
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_mtime < since or info.st_size == 0:
        return None
    return info.st_mtime, _Candidate(path=entry, size=info.st_size)


def _copy(source: Path, *, stem: Path) -> Path:
    """Copy ``source`` to ``stem`` with the source's lower-cased suffix."""
    output = stem.with_suffix(source.suffix.lower())
    try:
        shutil.copyfile(source, output)
    except OSError as exc:
        raise CameraError(f"camera image {source} could not be copied: {exc.strerror or exc}") from exc
    return output


__all__ = [
    "MEDIA_TYPE_BY_SUFFIX",
    "MTIME_GRACE_SECONDS",
    "POLL_INTERVAL_SECONDS",
    "capture_from_directory",
]
