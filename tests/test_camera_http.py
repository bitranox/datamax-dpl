"""HTTP camera: fetch a snapshot from a real http.server on 127.0.0.1."""

from __future__ import annotations

import base64
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr

from datamax_dpl.adapters.camera import capture_image
from datamax_dpl.adapters.camera.http import MAX_IMAGE_BYTES, capture_with_http
from datamax_dpl.adapters.memory.camera import TINY_PNG
from datamax_dpl.domain.camera import CameraKind, CameraSettings
from datamax_dpl.domain.enums import MediaType
from datamax_dpl.domain.errors import CameraError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

PASSWORD = "s3cret-Pa55word"


@dataclass(frozen=True)
class Route:
    """What the test server answers on one path."""

    status: int = 200
    content_type: str = "image/png"
    body: bytes = TINY_PNG
    credentials: tuple[str, str] | None = None
    location: str = ""


@dataclass
class Camera:
    """A running test server and the Authorization headers it received."""

    base: str
    routes: dict[str, Route]
    seen_auth: list[str | None] = field(default_factory=list[str | None])


def _handler(camera: Camera) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            camera.seen_auth.append(self.headers.get("Authorization"))
            route = camera.routes.get(self.path)
            if route is None:
                self.send_error(404)
                return
            if route.credentials is not None and not self._authorised(route.credentials):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="cam"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(route.status)
            self.send_header("Content-Type", route.content_type)
            self.send_header("Content-Length", str(len(route.body)))
            if route.location:
                self.send_header("Location", route.location)
            self.end_headers()
            self.wfile.write(route.body)

        def _authorised(self, credentials: tuple[str, str]) -> bool:
            token = base64.b64encode(":".join(credentials).encode()).decode()
            return self.headers.get("Authorization") == f"Basic {token}"

    return Handler


@pytest.fixture
def camera() -> Iterator[Camera]:
    """Serve configurable routes on an ephemeral 127.0.0.1 port."""
    state = Camera(base="", routes={})
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    state.base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def _settings(url: str, *, user: str = "", password: str = "") -> CameraSettings:
    return CameraSettings(kind=CameraKind.HTTP, url=url, user=user, password=SecretStr(password), timeout=5.0)


@pytest.mark.os_agnostic
def test_a_png_snapshot_is_stored_as_png(camera: Camera, tmp_path: Path) -> None:
    """image/png gives a .png file holding exactly the body."""
    camera.routes["/snap.png"] = Route()

    image = capture_image(_settings(f"{camera.base}/snap.png"), output_dir=tmp_path)

    assert image.path.suffix == ".png"
    assert image.media_type is MediaType.PNG
    assert image.path.read_bytes() == TINY_PNG
    assert camera.seen_auth == [None]


@pytest.mark.os_agnostic
def test_a_jpeg_snapshot_with_parameters_is_stored_as_jpg(camera: Camera, tmp_path: Path) -> None:
    """Content-Type parameters are ignored and the query string reaches the camera."""
    camera.routes["/cgi?res=high"] = Route(content_type="image/jpeg; charset=binary", body=b"\xff\xd8\xff-jpeg")

    image = capture_image(_settings(f"{camera.base}/cgi?res=high"), output_dir=tmp_path)

    assert image.path.suffix == ".jpg"
    assert image.media_type is MediaType.JPEG


@pytest.mark.os_agnostic
def test_a_non_image_media_type_is_refused(camera: Camera, tmp_path: Path) -> None:
    """text/html is named in the error and nothing is written."""
    camera.routes["/"] = Route(content_type="text/html", body=b"<html>login</html>")

    with pytest.raises(CameraError, match="media type 'text/html', expected"):
        capture_with_http(_settings(f"{camera.base}/"), stem=tmp_path / "shot")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.os_agnostic
def test_basic_auth_with_the_right_password_succeeds(camera: Camera, tmp_path: Path) -> None:
    """User and password from the settings are sent as basic auth."""
    camera.routes["/snap"] = Route(credentials=("admin", PASSWORD))

    path = capture_with_http(_settings(f"{camera.base}/snap", user="admin", password=PASSWORD), stem=tmp_path / "s")

    assert path.read_bytes() == TINY_PNG


@pytest.mark.os_agnostic
def test_basic_auth_with_a_wrong_password_fails_without_leaking_it(camera: Camera, tmp_path: Path) -> None:
    """A 401 is a CameraError whose message holds neither the password sent nor the right one."""
    camera.routes["/snap"] = Route(credentials=("admin", PASSWORD))
    wrong = "wrong-Pa55word-xyz"

    with pytest.raises(CameraError, match="answered HTTP 401") as caught:
        capture_with_http(_settings(f"{camera.base}/snap", user="admin", password=wrong), stem=tmp_path / "s")

    rendered = f"{caught.value} {caught.value!r} {caught.value.__cause__!r}"
    assert wrong not in rendered
    assert PASSWORD not in rendered


@pytest.mark.os_agnostic
def test_credentials_in_the_url_are_used_and_not_leaked(camera: Camera, tmp_path: Path) -> None:
    """URL userinfo authenticates when [camera] user is empty, and stays out of a failure message."""
    camera.routes["/snap"] = Route(credentials=("admin", PASSWORD))
    url = camera.base.replace("http://", f"http://admin:{PASSWORD}@")

    assert capture_with_http(_settings(f"{url}/snap"), stem=tmp_path / "ok").read_bytes() == TINY_PNG
    with pytest.raises(CameraError) as caught:
        capture_with_http(_settings(f"{url}/missing"), stem=tmp_path / "bad")
    assert PASSWORD not in str(caught.value)


@pytest.mark.os_agnostic
def test_a_redirect_is_not_followed(camera: Camera, tmp_path: Path) -> None:
    """A 302 is reported; the credentials are never replayed to the redirect target."""
    camera.routes["/old"] = Route(status=302, body=b"", location=f"{camera.base}/new", content_type="text/plain")
    camera.routes["/new"] = Route()

    with pytest.raises(CameraError, match="HTTP 302"):
        capture_with_http(_settings(f"{camera.base}/old", user="admin", password=PASSWORD), stem=tmp_path / "s")
    assert len(camera.seen_auth) == 1


@pytest.mark.os_agnostic
def test_an_empty_body_is_refused(camera: Camera, tmp_path: Path) -> None:
    """A 200 image/png with no bytes is not an image."""
    camera.routes["/snap"] = Route(body=b"")

    with pytest.raises(CameraError, match="empty image"):
        capture_with_http(_settings(f"{camera.base}/snap"), stem=tmp_path / "s")


@pytest.mark.os_agnostic
def test_a_body_past_the_cap_is_refused(camera: Camera, tmp_path: Path) -> None:
    """One byte more than MAX_IMAGE_BYTES is refused rather than written."""
    camera.routes["/huge"] = Route(body=bytes(MAX_IMAGE_BYTES + 1))

    with pytest.raises(CameraError, match="larger than"):
        capture_with_http(_settings(f"{camera.base}/huge"), stem=tmp_path / "s")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.os_agnostic
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://127.0.0.1/snap.jpg", "127.0.0.1/snap.jpg", "gopher://x"])
def test_a_url_that_is_not_http_is_refused_before_any_connection(url: str, tmp_path: Path) -> None:
    """file:, ftp:, a bare host and other schemes are rejected by scheme, without opening anything."""
    with pytest.raises(CameraError, match="not supported"):
        capture_with_http(_settings(url), stem=tmp_path / "s")


@pytest.mark.os_agnostic
def test_an_empty_url_is_a_configuration_error(tmp_path: Path) -> None:
    """kind = http with no url says which setting to fill in."""
    with pytest.raises(CameraError, match=r"set \[camera\] url"):
        capture_with_http(_settings(""), stem=tmp_path / "s")


@pytest.mark.os_agnostic
def test_an_unreachable_camera_is_a_camera_error(tmp_path: Path) -> None:
    """A refused connection becomes a CameraError naming the host."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_address[1]
    server.server_close()

    with pytest.raises(CameraError, match=re.escape("'127.0.0.1' is unreachable")):
        capture_with_http(_settings(f"http://127.0.0.1:{port}/snap"), stem=tmp_path / "s")


@pytest.mark.os_agnostic
def test_an_invalid_port_is_a_camera_error(tmp_path: Path) -> None:
    """A port that is not a number is reported, not raised as ValueError."""
    with pytest.raises(CameraError, match="invalid port"):
        capture_with_http(_settings("http://127.0.0.1:notaport/snap"), stem=tmp_path / "s")
