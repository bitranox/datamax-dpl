"""Take a photo by running an external capture program (``[camera] kind = "command"``).

The configured argv is run directly, never through a shell. Every occurrence of
:data:`~datamax_dpl.domain.camera.OUTPUT_PLACEHOLDER` is replaced by the image path. Its stdout is
dropped and only the end of its stderr is kept; the capture is over when the program exits, whatever
helpers it left running. On a timeout Windows ends the whole process tree, POSIX the program.

Extension rule:
    The image is a ``.jpg`` unless the argv writes the placeholder immediately followed by an
    image suffix, as in ``"{output}.png"``: then that suffix (``.png``, ``.jpg`` or ``.jpeg``,
    any case) becomes the file's extension and the placeholder plus suffix is replaced by the
    full path. ``["fswebcam", "{output}"]`` writes ``capture-<stamp>.jpg``;
    ``["grab", "-o", "{output}.png"]`` writes ``capture-<stamp>.png``. Two different suffixes
    in one argv are refused, since only one file is collected.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess  # nosec B404 # running the operator's capture program is this module's job
import threading
import time
from typing import IO, TYPE_CHECKING

from ...domain.camera import OUTPUT_PLACEHOLDER
from ...domain.errors import CameraError
from ..config.sections import CAMERA_SECTION
from .file import MEDIA_TYPE_BY_SUFFIX
from .stored import StoredImage

if TYPE_CHECKING:
    from pathlib import Path

    from ...domain.camera import CameraSettings

#: Extension used when the argv does not name one next to the placeholder.
DEFAULT_EXTENSION = ".jpg"

#: How many trailing characters of the program's stderr an error message carries.
STDERR_TAIL_CHARS = 500

#: Bytes kept for that tail: the longest a character can be in UTF-8, so the cut never costs one.
_STDERR_TAIL_BYTES = STDERR_TAIL_CHARS * 4

#: Most bytes taken from the program's stderr in one read. No setting: it changes no outcome.
_READ_BYTES = 64 * 1024

_PLACEHOLDER_PATTERN = re.compile(re.escape(OUTPUT_PLACEHOLDER) + r"(\.(?:png|jpe?g))?", re.IGNORECASE)


def capture_with_command(settings: CameraSettings, *, stem: Path) -> StoredImage:
    """Run ``settings.command`` and return the image it wrote.

    Args:
        settings: The camera settings; ``command`` and ``timeout`` are used.
        stem: Output path without extension; the extension follows the rule in the module
            docstring.

    Returns:
        The non-empty image file the program wrote, with the media type its extension names.

    Raises:
        CameraError: The argv is empty or lacks the placeholder, the program is missing, it
            timed out, exited non-zero, or left no non-empty image behind.

    Example:
        >>> import sys, tempfile
        >>> from pathlib import Path
        >>> from datamax_dpl.domain.camera import CameraKind, CameraSettings
        >>> script = "import sys; open(sys.argv[1], 'wb').write(b'img')"
        >>> settings = CameraSettings(kind=CameraKind.COMMAND, command=[sys.executable, "-c", script, "{output}"])
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     image = capture_with_command(settings, stem=Path(tmp) / "capture")
        >>> image.path.name, image.media_type.value
        ('capture.jpg', 'image/jpeg')
    """
    argv = list(settings.command)
    if not argv:
        raise CameraError(f"no camera command configured: set {CAMERA_SECTION.setting('command')}")
    extension = _extension(argv)
    output = stem.with_suffix(extension)
    resolved = [_resolve_program(argv[0]), *(_substitute(arg, output=output) for arg in argv[1:])]
    _run(resolved, program=argv[0], timeout=settings.timeout)
    _require_image(output, program=argv[0])
    return StoredImage(path=output, media_type=MEDIA_TYPE_BY_SUFFIX[extension])


def _extension(argv: list[str]) -> str:
    """Return the image extension the argv asks for, refusing an argv without the placeholder."""
    matches = [match for arg in argv for match in _PLACEHOLDER_PATTERN.finditer(arg)]
    if not matches:
        raise CameraError(f"camera command has no {OUTPUT_PLACEHOLDER} argument to receive the image path")
    suffixes = {_normalise_suffix(match.group(1)) for match in matches if match.group(1)}
    if len(suffixes) > 1:
        raise CameraError(f"camera command names conflicting image suffixes: {sorted(suffixes)}")
    return suffixes.pop() if suffixes else DEFAULT_EXTENSION


def _normalise_suffix(suffix: str) -> str:
    """Lower-case a suffix so ``.PNG`` and ``.png`` name the same file type."""
    return suffix.lower()


def _substitute(arg: str, *, output: Path) -> str:
    """Replace every placeholder (with its optional suffix) in ``arg`` by the output path."""
    # A lambda keeps re from parsing the path as a replacement template: a Windows path's
    # backslashes would otherwise be read as escapes or group references.
    return _PLACEHOLDER_PATTERN.sub(lambda _match: str(output), arg)


def _resolve_program(program: str) -> str:
    """Return the absolute path of ``program``.

    Windows CreateProcess searches the parent's PATH rather than the child's, so the program is
    resolved here with the same PATH on every platform.
    """
    resolved = shutil.which(program)
    if resolved is None:
        raise CameraError(f"camera program not found: {program!r}")
    return resolved


class _StderrTail:
    """Drains a stream on its own thread and keeps only its end.

    A thread, so that nothing here waits for the stream to close: a helper the program left running
    holds it open after the program is gone. The program must never block on a full pipe either, so
    the rest is read and dropped rather than left unread.
    """

    def __init__(self, stream: IO[bytes] | None) -> None:
        self._kept = b""
        # Daemon: one still blocked on a helper's open pipe must not hold up the interpreter's exit.
        self._thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)
        self._thread.start()

    def _drain(self, stream: IO[bytes] | None) -> None:
        if stream is None:
            return
        # os.read, not stream.read: that one would wait for a full buffer or the stream's end, and
        # the tail of a failed program is wanted even though a helper keeps the pipe open.
        with stream:
            while chunk := os.read(stream.fileno(), _READ_BYTES):
                self._kept = (self._kept + chunk)[-_STDERR_TAIL_BYTES:]

    def text(self, *, wait: float) -> str:
        """The last :data:`STDERR_TAIL_CHARS` characters, decoded leniently, at the stream's end or after ``wait``."""
        self._thread.join(max(wait, 0.0))
        text = self._kept.decode("utf-8", errors="replace").strip()
        return text[-STDERR_TAIL_CHARS:] if text else "(no stderr output)"


def _kill_tree(process: subprocess.Popen[bytes], *, timeout: float) -> None:
    """End the program and, on Windows, everything it started.

    Windows gives a killed process's children no signal, and they keep running. POSIX stays with the
    program alone: a new session for the tree would also take it out of the reach of Ctrl-C.
    """
    taskkill = shutil.which("taskkill") if os.name == "nt" else None
    if taskkill is not None:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(  # noqa: S603 - the system's taskkill and a pid this process started # nosec B603
                [taskkill, "/T", "/F", "/PID", str(process.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
    process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout)


def _run(argv: list[str], *, program: str, timeout: float) -> None:
    """Run ``argv`` to completion, turning every failure into a :class:`CameraError`."""
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(  # noqa: S603 - operator-configured argv, no shell # nosec B603
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        # ValueError: a NUL in an argument, which no program can be started with.
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else exc
        raise CameraError(f"camera program {program!r} could not be started: {reason}") from exc
    stderr = _StderrTail(process.stderr)
    try:
        returncode = process.wait(timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_tree(process, timeout=timeout)
        raise CameraError(f"camera program {program!r} timed out after {timeout:g} s") from exc
    except BaseException:
        # An interrupt sent to this process alone (kill -INT, a supervisor) must not leave it running.
        _kill_tree(process, timeout=timeout)
        raise
    if returncode != 0:
        # What is left of the capture's time, so a helper holding stderr cannot stretch the capture.
        tail = stderr.text(wait=deadline - time.monotonic())
        raise CameraError(f"camera program {program!r} exited with status {returncode}: {tail}")


def _require_image(output: Path, *, program: str) -> None:
    """Refuse a run that left no image, or an empty one, at ``output``."""
    try:
        size = output.stat().st_size
    except OSError as exc:
        raise CameraError(f"camera program {program!r} wrote no image to {output}") from exc
    if size == 0:
        raise CameraError(f"camera program {program!r} wrote an empty image to {output}")


__all__ = ["DEFAULT_EXTENSION", "STDERR_TAIL_CHARS", "capture_with_command"]
