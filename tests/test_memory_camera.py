"""FakeCamera: the in-memory CaptureImage double."""

from __future__ import annotations

import struct
import zlib
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.memory import FakeCamera
from datamax_dpl.adapters.memory.camera import PNG_SIGNATURE, TINY_PNG
from datamax_dpl.domain.camera import CameraSettings

if TYPE_CHECKING:
    from pathlib import Path


def _chunks(png: bytes) -> list[tuple[bytes, bytes, int]]:
    """Split a PNG into (type, data, stored crc) triples."""
    chunks: list[tuple[bytes, bytes, int]] = []
    offset = len(PNG_SIGNATURE)
    while offset < len(png):
        (length,) = struct.unpack(">I", png[offset : offset + 4])
        kind = png[offset + 4 : offset + 8]
        data = png[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", png[offset + 8 + length : offset + 12 + length])
        chunks.append((kind, data, crc))
        offset += 12 + length
    return chunks


@pytest.mark.os_agnostic
def test_the_default_payload_is_a_well_formed_one_pixel_png() -> None:
    """Signature, IHDR 1x1, correct CRCs, IDAT that inflates to one filtered RGB row, IEND."""
    chunks = _chunks(TINY_PNG)

    assert TINY_PNG.startswith(PNG_SIGNATURE)
    assert [kind for kind, _, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    assert all(zlib.crc32(kind + data) == crc for kind, data, crc in chunks)
    assert struct.unpack(">II", chunks[0][1][:8]) == (1, 1)
    assert zlib.decompress(chunks[1][1]) == b"\x00\xff\xff\xff"


@pytest.mark.os_agnostic
def test_pillow_reads_the_default_payload(tmp_path: Path) -> None:
    """An independent decoder accepts the PNG (pillow is a dev dependency on Python 3.11+)."""
    image_module = pytest.importorskip("PIL.Image")
    path = tmp_path / "tiny.png"
    path.write_bytes(TINY_PNG)

    with image_module.open(path) as image:
        assert image.size == (1, 1)
        assert image.convert("RGB").getpixel((0, 0)) == (255, 255, 255)


@pytest.mark.os_agnostic
def test_each_call_writes_a_new_file_and_is_counted(tmp_path: Path) -> None:
    """Calls are counted and every capture gets its own file in output_dir."""
    camera = FakeCamera()

    first = camera(CameraSettings(), output_dir=tmp_path / "out")
    second = camera(CameraSettings(), output_dir=tmp_path / "out")

    assert camera.calls == 2
    assert first.path != second.path
    assert first.path.read_bytes() == TINY_PNG
    assert first.media_type == "image/png"
    assert first.captured_at.utcoffset() == timedelta(0)


@pytest.mark.os_agnostic
def test_a_jpeg_payload_is_written_as_jpeg(tmp_path: Path) -> None:
    """A payload with the JPEG signature is stored as .jpg with image/jpeg."""
    payload = b"\xff\xd8\xff\xe0 fake jpeg"

    image = FakeCamera(payload)(CameraSettings(), output_dir=tmp_path)

    assert image.path.suffix == ".jpg"
    assert image.media_type == "image/jpeg"
    assert image.path.read_bytes() == payload


@pytest.mark.os_agnostic
def test_a_payload_that_is_no_image_is_refused() -> None:
    """Bytes that are neither PNG nor JPEG are refused at construction."""
    with pytest.raises(ValueError, match="PNG or JPEG"):
        FakeCamera(b"GIF89a")
