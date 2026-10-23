"""Folder camera: pick up the newest image dropped into a watched directory."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.camera import capture_image
from datamax_dpl.adapters.camera.file import POLL_INTERVAL_SECONDS, capture_from_directory
from datamax_dpl.domain.camera import CameraKind, CameraSettings
from datamax_dpl.domain.errors import CameraError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class FakeTime:
    """A clock that only advances when ``sleep`` is called, and an optional hook run on each sleep."""

    def __init__(self, on_sleep: Callable[[int], None] | None = None) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []
        self._on_sleep = on_sleep

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self._on_sleep is not None:
            self._on_sleep(len(self.sleeps))


def _settings(directory: Path, *, timeout: float = 5.0) -> CameraSettings:
    return CameraSettings(kind=CameraKind.FILE, directory=str(directory), timeout=timeout)


def _age(path: Path, *, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


@pytest.mark.os_agnostic
def test_the_new_image_is_returned_and_an_old_one_ignored(tmp_path: Path) -> None:
    """A file older than the capture start (minus the grace) is not the photo; a fresh one is."""
    watched = tmp_path / "watched"
    watched.mkdir()
    old = watched / "old.jpg"
    old.write_bytes(b"old image")
    _age(old, seconds=3600)
    (watched / "NEW.JPEG").write_bytes(b"new image")
    fake = FakeTime()

    path = capture_from_directory(_settings(watched), stem=tmp_path / "shot", sleep=fake.sleep, clock=fake.clock)

    assert path == tmp_path / "shot.jpeg"
    assert path.read_bytes() == b"new image"
    assert fake.sleeps == [POLL_INTERVAL_SECONDS]


@pytest.mark.os_agnostic
def test_the_newest_of_several_new_images_wins(tmp_path: Path) -> None:
    """Among new images the one with the latest mtime is taken; non-images are ignored."""
    first = tmp_path / "a.png"
    first.write_bytes(b"first")
    _age(first, seconds=0.5)
    (tmp_path / "b.png").write_bytes(b"second")
    (tmp_path / "notes.txt").write_bytes(b"not an image")
    fake = FakeTime()
    out = tmp_path / "out"
    out.mkdir()

    path = capture_from_directory(_settings(tmp_path), stem=out / "shot", sleep=fake.sleep, clock=fake.clock)

    assert path.read_bytes() == b"second"
    assert path.suffix == ".png"


@pytest.mark.os_agnostic
def test_an_image_appearing_while_polling_is_picked_up(tmp_path: Path) -> None:
    """The adapter keeps looking until the camera drops the file."""
    watched = tmp_path / "watched"
    watched.mkdir()

    def drop(sleep_count: int) -> None:
        if sleep_count == 3:
            (watched / "late.jpg").write_bytes(b"late image")

    fake = FakeTime(on_sleep=drop)

    path = capture_from_directory(_settings(watched), stem=tmp_path / "shot", sleep=fake.sleep, clock=fake.clock)

    assert path.read_bytes() == b"late image"
    assert len(fake.sleeps) == 4


@pytest.mark.os_agnostic
def test_a_file_still_growing_is_not_taken_until_its_size_settles(tmp_path: Path) -> None:
    """An upload in progress is collected only after two polls see the same size."""
    image = tmp_path / "upload.jpg"
    image.write_bytes(b"part")

    def grow(sleep_count: int) -> None:
        if sleep_count == 1:
            image.write_bytes(b"part and the rest")

    fake = FakeTime(on_sleep=grow)
    out = tmp_path / "out"
    out.mkdir()

    path = capture_from_directory(_settings(tmp_path), stem=out / "shot", sleep=fake.sleep, clock=fake.clock)

    assert path.read_bytes() == b"part and the rest"
    assert len(fake.sleeps) == 2


@pytest.mark.os_agnostic
def test_an_empty_folder_times_out_without_real_waiting(tmp_path: Path) -> None:
    """The injected clock passes the timeout; the test takes no wall-clock time."""
    fake = FakeTime()
    started = time.monotonic()

    with pytest.raises(CameraError, match="no new complete image"):
        capture_from_directory(
            _settings(tmp_path, timeout=2.0), stem=tmp_path / "shot", sleep=fake.sleep, clock=fake.clock
        )

    assert time.monotonic() - started < 1
    assert fake.now - 1000.0 >= 2.0
    assert len(fake.sleeps) == round(2.0 / POLL_INTERVAL_SECONDS)


@pytest.mark.os_agnostic
def test_only_old_images_time_out(tmp_path: Path) -> None:
    """A folder holding only images from before the capture is the same as an empty one."""
    old = tmp_path / "old.png"
    old.write_bytes(b"old")
    _age(old, seconds=60)
    fake = FakeTime()

    with pytest.raises(CameraError, match="within 1 s"):
        capture_from_directory(
            _settings(tmp_path, timeout=1.0), stem=tmp_path / "x", sleep=fake.sleep, clock=fake.clock
        )


@pytest.mark.os_agnostic
def test_an_empty_image_file_is_ignored(tmp_path: Path) -> None:
    """A zero-byte file (a camera that has only created the file) is not a photo."""
    (tmp_path / "empty.jpg").write_bytes(b"")
    fake = FakeTime()

    with pytest.raises(CameraError):
        capture_from_directory(
            _settings(tmp_path, timeout=0.5), stem=tmp_path / "x", sleep=fake.sleep, clock=fake.clock
        )


@pytest.mark.os_agnostic
def test_a_missing_directory_is_refused_at_once(tmp_path: Path) -> None:
    """A folder that does not exist is a configuration error, not a reason to wait."""
    fake = FakeTime()

    with pytest.raises(CameraError, match="does not exist"):
        capture_from_directory(_settings(tmp_path / "nope"), stem=tmp_path / "x", sleep=fake.sleep, clock=fake.clock)
    assert fake.sleeps == []


@pytest.mark.os_agnostic
def test_an_unset_directory_names_the_setting(tmp_path: Path) -> None:
    """kind = file with no directory says which setting to fill in."""
    settings = CameraSettings(kind=CameraKind.FILE)

    with pytest.raises(CameraError, match=r"set \[camera\] directory"):
        capture_from_directory(settings, stem=tmp_path / "x")


@pytest.mark.os_agnostic
def test_capture_image_dispatches_to_the_folder_camera(tmp_path: Path) -> None:
    """Through the port, a ready image is returned with its media type."""
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "photo.png").write_bytes(b"png bytes")

    image = capture_image(_settings(watched), output_dir=tmp_path / "out")

    assert image.media_type == "image/png"
    assert image.path.parent == tmp_path / "out"
    assert image.path.read_bytes() == b"png bytes"


@pytest.mark.os_agnostic
def test_capture_image_without_a_camera_is_refused(tmp_path: Path) -> None:
    """kind = none raises the configuration hint and creates nothing."""
    with pytest.raises(CameraError, match=r"^no camera configured: set \[camera\] kind$"):
        capture_image(CameraSettings(), output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()
