"""What a capture strategy hands back: the image it stored, and the media type it stored it as.

Each strategy knows the media type where it decides the file's extension (the response's
``Content-Type``, the argv's suffix, the source file's suffix), so it reports it there and nothing
downstream reads it back from the file name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ...domain.enums import MediaType


@dataclass(frozen=True, slots=True)
class StoredImage:
    """An image a capture strategy wrote.

    Attributes:
        path: Where the image was written.
        media_type: The image format it was written as, matching the path's extension.
    """

    path: Path
    media_type: MediaType


__all__ = ["StoredImage"]
