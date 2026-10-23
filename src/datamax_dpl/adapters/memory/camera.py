"""In-memory camera: a :class:`~datamax_dpl.application.ports.CaptureImage` double.

:class:`FakeCamera` writes a fixed image payload instead of photographing anything, so the
"print, photograph, compare" flow can be tested without hardware. The default payload is a
valid 1x1 white PNG built here from :mod:`zlib` and :mod:`struct`.
"""

from __future__ import annotations

import struct
import zlib
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ...domain.camera import CapturedImage

if TYPE_CHECKING:
    from pathlib import Path

    from ...domain.camera import CameraSettings

#: The eight bytes every PNG file starts with (PNG specification, section 5.2).
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: The bytes every JPEG file starts with: the SOI marker plus the next marker's prefix.
JPEG_SIGNATURE = b"\xff\xd8\xff"

_PNG_BIT_DEPTH = 8
_PNG_COLOUR_TYPE_RGB = 2
_PNG_FILTER_NONE = b"\x00"
_WHITE_RGB = b"\xff\xff\xff"


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    """Return one PNG chunk: length, type, data and the CRC over type plus data."""
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _tiny_png() -> bytes:
    """Return a valid 1x1 white RGB PNG."""
    # Width, height, bit depth, colour type, then compression, filter and interlace methods,
    # all three 0 because the PNG specification defines no other value for them.
    header = struct.pack(">IIBBBBB", 1, 1, _PNG_BIT_DEPTH, _PNG_COLOUR_TYPE_RGB, 0, 0, 0)
    pixels = zlib.compress(_PNG_FILTER_NONE + _WHITE_RGB)
    return PNG_SIGNATURE + _png_chunk(b"IHDR", header) + _png_chunk(b"IDAT", pixels) + _png_chunk(b"IEND", b"")


#: The default payload: a valid 1x1 white PNG.
TINY_PNG = _tiny_png()


class FakeCamera:
    """A camera double that stores ``payload`` as the photo and counts its calls.

    Example:
        >>> import tempfile
        >>> from pathlib import Path
        >>> from datamax_dpl.domain.camera import CameraSettings
        >>> camera = FakeCamera()
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     image = camera(CameraSettings(), output_dir=Path(tmp))
        ...     (image.path.name, image.media_type, image.path.read_bytes() == TINY_PNG)
        ('capture-0001.png', 'image/png', True)
        >>> camera.calls
        1
    """

    def __init__(self, payload: bytes = TINY_PNG) -> None:
        """Keep ``payload``, which must be a PNG or a JPEG file's bytes.

        Args:
            payload: The image bytes every capture writes.

        Raises:
            ValueError: ``payload`` starts with neither the PNG nor the JPEG signature.
        """
        if payload.startswith(PNG_SIGNATURE):
            self._extension, self._media_type = ".png", "image/png"
        elif payload.startswith(JPEG_SIGNATURE):
            self._extension, self._media_type = ".jpg", "image/jpeg"
        else:
            raise ValueError("FakeCamera payload must be PNG or JPEG bytes")
        self._payload = payload
        self.calls = 0

    def __call__(self, settings: CameraSettings, *, output_dir: Path) -> CapturedImage:
        """Write the payload into ``output_dir`` and return it as the captured image.

        Args:
            settings: Ignored; accepted so the double matches the port.
            output_dir: Folder for the image; created when missing.

        Returns:
            The written image, named ``capture-<call number>.<ext>``.
        """
        self.calls += 1
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"capture-{self.calls:04d}{self._extension}"
        path.write_bytes(self._payload)
        return CapturedImage(path=path, media_type=self._media_type, captured_at=datetime.now(timezone.utc))


if TYPE_CHECKING:
    from ...application.ports import CaptureImage

    _assert_capture_image: CaptureImage = FakeCamera()


__all__ = ["JPEG_SIGNATURE", "PNG_SIGNATURE", "TINY_PNG", "FakeCamera"]
