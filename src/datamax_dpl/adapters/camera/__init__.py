"""Camera adapters: take one photo of a printed label with the configured camera.

:func:`capture_image` implements the :class:`~datamax_dpl.application.ports.CaptureImage` port
and dispatches on ``[camera] kind``:

* ``command`` - :mod:`.command` runs a capture program that writes the image itself.
* ``http`` - :mod:`.http` fetches a snapshot URL.
* ``file`` - :mod:`.file` picks up the newest image a camera drops into a folder.

Every image is stored in ``output_dir`` as ``capture-<UTC timestamp>.<ext>``. Waiting
``settle_seconds`` after the print is the caller's job; the adapters take the photo at once.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from ...domain.camera import CameraKind, CapturedImage
from ...domain.errors import CameraError
from ..config.sections import CAMERA_SECTION
from .command import capture_with_command
from .file import capture_from_directory
from .http import capture_with_http

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ...domain.camera import CameraSettings
    from .stored import StoredImage


class _Capture(Protocol):
    """One capture strategy: write the image next to ``stem`` and return it with its media type."""

    def __call__(self, settings: CameraSettings, *, stem: Path) -> StoredImage: ...


#: The strategy of every camera kind but ``none``, which takes no photo.
_CAPTURE_BY_KIND: Mapping[CameraKind, _Capture] = MappingProxyType(
    {
        CameraKind.COMMAND: capture_with_command,
        CameraKind.HTTP: capture_with_http,
        CameraKind.FILE: capture_from_directory,
    }
)

#: strftime pattern of the timestamp in an image file name; microseconds keep names unique.
CAPTURE_STAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"


def capture_image(settings: CameraSettings, *, output_dir: Path) -> CapturedImage:
    """Take one photo with the configured camera and store it in ``output_dir``.

    Args:
        settings: The ``[camera]`` section.
        output_dir: Folder for the image; created when missing.

    Returns:
        The stored image with its media type and the (UTC) moment the capture finished.

    Raises:
        CameraError: No camera is configured, ``output_dir`` cannot be created, or the camera
            could not deliver an image.

    Example:
        >>> from pathlib import Path
        >>> from datamax_dpl.domain.camera import CameraSettings
        >>> capture_image(CameraSettings(), output_dir=Path("unused"))
        Traceback (most recent call last):
        ...
        datamax_dpl.domain.errors.CameraError: no camera configured: set [camera] kind
    """
    capture = _CAPTURE_BY_KIND.get(settings.kind)
    if capture is None:
        raise CameraError(f"no camera configured: set {CAMERA_SECTION.setting('kind')}")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CameraError(f"image folder {output_dir} cannot be created: {exc.strerror or exc}") from exc
    stem = output_dir / f"capture-{datetime.now(timezone.utc).strftime(CAPTURE_STAMP_FORMAT)}"
    stored = capture(settings, stem=stem)
    return CapturedImage(path=stored.path, media_type=stored.media_type, captured_at=datetime.now(timezone.utc))


if TYPE_CHECKING:
    from ...application.ports import CaptureImage

    _assert_capture_image: CaptureImage = capture_image


__all__ = [
    "CAPTURE_STAMP_FORMAT",
    "capture_from_directory",
    "capture_image",
    "capture_with_command",
    "capture_with_http",
]
