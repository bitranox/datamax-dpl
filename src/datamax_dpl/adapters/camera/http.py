"""Take a photo by fetching a camera's snapshot URL (``[camera] kind = "http"``).

The request is made with :mod:`http.client` rather than :mod:`urllib.request`: the connection
class is chosen from the scheme, so only ``http`` and ``https`` can ever be opened (no
``file:`` or ``ftp:`` reaches a handler), and redirects are not followed, so the basic-auth
header can never be replayed to another host. A redirect is reported as an error naming the
status; configure the final snapshot URL instead.

The response ``Content-Type`` decides the media type and so the extension: ``image/jpeg`` (and
the common non-standard ``image/jpg``) give a JPEG stored as ``.jpg``, ``image/png`` a PNG stored
as ``.png``; anything else is refused. The password is never logged and never appears in an error message.
"""

from __future__ import annotations

import base64
import http.client
import ssl
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING
from urllib.parse import SplitResult, unquote, urlsplit

from ...domain.enums import MediaType
from ...domain.errors import CameraError
from ..config.sections import CAMERA_SECTION
from .stored import StoredImage

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ...domain.camera import CameraSettings

#: Largest image body accepted, in bytes (50 MiB).
MAX_IMAGE_BYTES = 50 * 1024 * 1024

#: The media type each accepted ``Content-Type`` names, in the order the request's ``Accept`` lists
#: them. Keyed by the header text because cameras also send ``image/jpg``, which is no registered
#: media type: it is an alias of ``image/jpeg``.
MEDIA_TYPE_BY_CONTENT_TYPE: Mapping[str, MediaType] = MappingProxyType(
    {
        MediaType.JPEG.value: MediaType.JPEG,
        "image/jpg": MediaType.JPEG,
        MediaType.PNG.value: MediaType.PNG,
    }
)

#: The extension an image of each media type is stored with.
EXTENSION_BY_MEDIA_TYPE: Mapping[MediaType, str] = MappingProxyType({MediaType.JPEG: ".jpg", MediaType.PNG: ".png"})

_HTTP_OK = 200


class _Scheme(str, Enum):
    """The URL schemes a camera may be fetched over."""

    HTTP = "http"
    HTTPS = "https"


@dataclass(frozen=True, slots=True)
class _CameraUrl:
    """A camera URL split, with the scheme it was accepted under."""

    parts: SplitResult
    scheme: _Scheme


def capture_with_http(settings: CameraSettings, *, stem: Path) -> StoredImage:
    """GET ``settings.url`` and store the image it returns.

    Args:
        settings: The camera settings; ``url``, ``user``, ``password`` and ``timeout`` are used.
        stem: Output path without extension; the extension follows the response's media type.

    Returns:
        The image written, with the media type the response named.

    Raises:
        CameraError: The URL is not ``http``/``https``, the camera is unreachable, answers a
            status other than 200, sends a media type that is not JPEG or PNG, an empty body,
            or more than :data:`MAX_IMAGE_BYTES`.
    """
    url = _parse_url(settings.url)
    connection = _connect(url, timeout=settings.timeout)
    target = url.parts
    try:
        response = _get(connection, target=target, headers=_headers(settings, target=target))
        media_type = _media_type(response)
        body = _read_body(response)
    finally:
        connection.close()
    output = stem.with_suffix(EXTENSION_BY_MEDIA_TYPE[media_type])
    try:
        output.write_bytes(body)
    except OSError as exc:
        raise CameraError(f"camera image could not be written to {output}: {exc.strerror or exc}") from exc
    return StoredImage(path=output, media_type=media_type)


def _parse_url(url: str) -> _CameraUrl:
    """Split ``url``, refusing anything but an ``http``/``https`` URL with a host."""
    if not url:
        raise CameraError(f"no camera URL configured: set {CAMERA_SECTION.setting('url')}")
    target = urlsplit(url)
    try:
        scheme = _Scheme(target.scheme.lower())
    except ValueError:
        raise CameraError(
            f"camera URL scheme {target.scheme or '(none)'!r} is not supported: use http or https"
        ) from None
    if not target.hostname:
        raise CameraError("camera URL has no host")
    return _CameraUrl(parts=target, scheme=scheme)


def _connect(url: _CameraUrl, *, timeout: float) -> http.client.HTTPConnection:
    """Return an unopened connection of the class the scheme asks for."""
    target = url.parts
    try:
        port = target.port
    except ValueError as exc:
        raise CameraError(f"camera URL has an invalid port for host {target.hostname!r}") from exc
    host = target.hostname or ""
    if url.scheme is _Scheme.HTTPS:
        return http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl.create_default_context())
    return http.client.HTTPConnection(host, port, timeout=timeout)


def _headers(settings: CameraSettings, *, target: SplitResult) -> dict[str, str]:
    """Return the request headers, with basic auth when a user or password is configured.

    Credentials in the URL's userinfo are used only when ``[camera] user`` is empty.
    """
    headers = {"Accept": ", ".join(MEDIA_TYPE_BY_CONTENT_TYPE)}
    user = settings.user or unquote(target.username or "")
    password = settings.password.get_secret_value() or unquote(target.password or "")
    if user or password:
        token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    return headers


def _get(
    connection: http.client.HTTPConnection, *, target: SplitResult, headers: dict[str, str]
) -> http.client.HTTPResponse:
    """Send the GET request and return a 200 response, or raise :class:`CameraError`."""
    path = target.path or "/"
    if target.query:
        path = f"{path}?{target.query}"
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
    except TimeoutError as exc:
        raise CameraError(f"camera at {target.hostname!r} did not answer in time") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise CameraError(f"camera at {target.hostname!r} is unreachable: {type(exc).__name__}") from exc
    if response.status != _HTTP_OK:
        raise CameraError(f"camera at {target.hostname!r} answered HTTP {response.status} {response.reason}")
    return response


def _media_type(response: http.client.HTTPResponse) -> MediaType:
    """Return the media type the response names, refusing a non-image."""
    content_type = response.headers.get_content_type()
    media_type = MEDIA_TYPE_BY_CONTENT_TYPE.get(content_type)
    if media_type is None:
        expected = f"{MediaType.JPEG.value} or {MediaType.PNG.value}"
        raise CameraError(f"camera sent media type {content_type!r}, expected {expected}")
    return media_type


def _read_body(response: http.client.HTTPResponse) -> bytes:
    """Read the body, refusing an empty one or one larger than :data:`MAX_IMAGE_BYTES`."""
    try:
        # One byte past the cap is enough to tell "too large" without reading the rest.
        body = response.read(MAX_IMAGE_BYTES + 1)
    except TimeoutError as exc:
        raise CameraError("camera stopped sending the image before it was complete") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise CameraError(f"camera image could not be read: {type(exc).__name__}") from exc
    if len(body) > MAX_IMAGE_BYTES:
        raise CameraError(f"camera image is larger than {MAX_IMAGE_BYTES} bytes")
    if not body:
        raise CameraError("camera sent an empty image")
    return body


__all__ = ["EXTENSION_BY_MEDIA_TYPE", "MAX_IMAGE_BYTES", "MEDIA_TYPE_BY_CONTENT_TYPE", "capture_with_http"]
