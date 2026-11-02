"""Camera settings and captured images, for photographing printed labels.

The ``[camera]`` configuration section maps onto :class:`CameraSettings`; a capture adapter turns
it into a :class:`CapturedImage` on disk. The photo is compared with the expected preview by a
person or an LLM; nothing here judges the image.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .enums import MediaType
from .timing import MAX_TIMEOUT_SECONDS


class CameraKind(str, Enum):
    """How a photo is taken.

    Example:
        >>> CameraKind("command") is CameraKind.COMMAND
        True
    """

    NONE = "none"
    COMMAND = "command"
    HTTP = "http"
    FILE = "file"


#: Placeholder in a capture command's argv that the adapter replaces by the output image path.
OUTPUT_PLACEHOLDER = "{output}"


def refuse_boolean(value: object) -> object:
    """Pass ``value`` on unless it is a boolean, for a number in a settings section.

    A setting stays lax otherwise, since ``.env`` delivers ``1.50`` as text and strict mode would
    refuse it. A boolean is never deliberate, and read as 1 or 0 it silently changes behaviour.

    Raises:
        ValueError: ``value`` is a boolean.

    Example:
        >>> refuse_boolean("1.50")
        '1.50'
        >>> refuse_boolean(True)
        Traceback (most recent call last):
        ...
        ValueError: needs a number, got true
    """
    if isinstance(value, bool):
        raise ValueError(f"needs a number, got {str(value).lower()}")
    return value


class CameraSettings(BaseModel):
    """The ``[camera]`` configuration section.

    Example:
        >>> CameraSettings().kind
        <CameraKind.NONE: 'none'>
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: CameraKind = CameraKind.NONE
    command: list[str] = Field(default_factory=list[str])
    url: str = ""
    user: str = ""
    password: SecretStr = SecretStr("")
    directory: str = ""
    settle_seconds: float = Field(default=1.0, ge=0, le=MAX_TIMEOUT_SECONDS)
    timeout: float = Field(default=20.0, gt=0, le=MAX_TIMEOUT_SECONDS)

    _numbers_are_no_booleans = field_validator("settle_seconds", "timeout", mode="before")(refuse_boolean)


class CapturedImage(BaseModel):
    """One photo on disk.

    Attributes:
        path: Where the image was written.
        media_type: Image format, e.g. ``MediaType.JPEG``.
        captured_at: When the capture finished (timezone-aware).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    media_type: MediaType
    captured_at: datetime


__all__ = ["OUTPUT_PLACEHOLDER", "CameraKind", "CameraSettings", "CapturedImage", "refuse_boolean"]
