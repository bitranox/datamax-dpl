"""Take a photo by running an external capture program (``[camera] kind = "command"``).

The configured argv is run directly, never through a shell. Every occurrence of
:data:`~datamax_dpl.domain.camera.OUTPUT_PLACEHOLDER` is replaced by the image path.

Extension rule:
    The image is a ``.jpg`` unless the argv writes the placeholder immediately followed by an
    image suffix, as in ``"{output}.png"``: then that suffix (``.png``, ``.jpg`` or ``.jpeg``,
    any case) becomes the file's extension and the placeholder plus suffix is replaced by the
    full path. ``["fswebcam", "{output}"]`` writes ``capture-<stamp>.jpg``;
    ``["grab", "-o", "{output}.png"]`` writes ``capture-<stamp>.png``. Two different suffixes
    in one argv are refused, since only one file is collected.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404 # running the operator's capture program is this module's job
from typing import TYPE_CHECKING

from ...domain.camera import OUTPUT_PLACEHOLDER
from ...domain.errors import CameraError

if TYPE_CHECKING:
    from pathlib import Path

    from ...domain.camera import CameraSettings

#: Extension used when the argv does not name one next to the placeholder.
DEFAULT_EXTENSION = ".jpg"

#: How many trailing characters of the program's stderr an error message carries.
STDERR_TAIL_CHARS = 500

_PLACEHOLDER_PATTERN = re.compile(re.escape(OUTPUT_PLACEHOLDER) + r"(\.(?:png|jpe?g))?", re.IGNORECASE)


def capture_with_command(settings: CameraSettings, *, stem: Path) -> Path:
    """Run ``settings.command`` and return the image it wrote.

    Args:
        settings: The camera settings; ``command`` and ``timeout`` are used.
        stem: Output path without extension; the extension follows the rule in the module
            docstring.

    Returns:
        The path of the non-empty image file the program wrote.

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
        ...     capture_with_command(settings, stem=Path(tmp) / "capture").name
        'capture.jpg'
    """
    argv = list(settings.command)
    if not argv:
        raise CameraError("no camera command configured: set [camera] command")
    output = stem.with_suffix(_extension(argv))
    resolved = [_resolve_program(argv[0]), *(_substitute(arg, output=output) for arg in argv[1:])]
    _run(resolved, program=argv[0], timeout=settings.timeout)
    _require_image(output, program=argv[0])
    return output


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


def _run(argv: list[str], *, program: str, timeout: float) -> None:
    """Run ``argv`` to completion, turning every failure into a :class:`CameraError`."""
    try:
        completed = subprocess.run(  # noqa: S603 - operator-configured argv, no shell # nosec B603
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CameraError(f"camera program {program!r} timed out after {timeout:g} s") from exc
    except OSError as exc:
        raise CameraError(f"camera program {program!r} could not be started: {exc.strerror or exc}") from exc
    if completed.returncode != 0:
        tail = _stderr_tail(completed.stderr)
        raise CameraError(f"camera program {program!r} exited with status {completed.returncode}: {tail}")


def _stderr_tail(stderr: bytes) -> str:
    """Return the last :data:`STDERR_TAIL_CHARS` characters of ``stderr``, decoded leniently."""
    text = stderr.decode("utf-8", errors="replace").strip()
    return text[-STDERR_TAIL_CHARS:] if text else "(no stderr output)"


def _require_image(output: Path, *, program: str) -> None:
    """Refuse a run that left no image, or an empty one, at ``output``."""
    try:
        size = output.stat().st_size
    except OSError as exc:
        raise CameraError(f"camera program {program!r} wrote no image to {output}") from exc
    if size == 0:
        raise CameraError(f"camera program {program!r} wrote an empty image to {output}")


__all__ = ["DEFAULT_EXTENSION", "STDERR_TAIL_CHARS", "capture_with_command"]
