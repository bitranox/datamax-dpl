"""PNG writer tests: decode the output with a tiny reader written here, and with Pillow if present."""

from __future__ import annotations

import io
import struct
import zlib
from dataclasses import dataclass

import pytest
from hypothesis import given
from hypothesis import strategies as st

from datamax_dpl.domain.enums import FillPattern
from datamax_dpl.domain.png import PNG_SIGNATURE, encode_png, encode_png_rgb
from datamax_dpl.domain.raster import Bitmap


@dataclass(frozen=True)
class DecodedPng:
    """What the test reader extracts from a PNG file."""

    width: int
    height: int
    depth: int
    colour: int
    scanlines: list[bytes]


def read_png(data: bytes) -> DecodedPng:
    """Parse a non-interlaced, unfiltered PNG; assert the structure is valid on the way."""
    assert data[:8] == PNG_SIGNATURE
    position = 8
    chunks: list[tuple[bytes, bytes]] = []
    while position < len(data):
        (length,) = struct.unpack(">I", data[position : position + 4])
        kind = data[position + 4 : position + 8]
        body = data[position + 8 : position + 8 + length]
        (crc,) = struct.unpack(">I", data[position + 8 + length : position + 12 + length])
        assert crc == zlib.crc32(kind + body), kind
        chunks.append((kind, body))
        position += 12 + length
    assert [kind for kind, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    assert (compression, filtering, interlace) == (0, 0, 0)
    raw = zlib.decompress(chunks[1][1])
    stride = (width * depth * (3 if colour == 2 else 1) + 7) // 8
    assert len(raw) == height * (stride + 1)
    lines = [raw[row * (stride + 1) : (row + 1) * (stride + 1)] for row in range(height)]
    assert all(line[0] == 0 for line in lines), "every scanline must use filter type 0"
    return DecodedPng(width, height, depth, colour, [line[1:] for line in lines])


def gray_pixel(png: DecodedPng, x: int, y: int) -> int:
    """Return the 1-bit sample (0 black, 1 white) of a decoded grayscale PNG."""
    return (png.scanlines[y][x // 8] >> (7 - x % 8)) & 1


def sample_bitmap() -> Bitmap:
    """A small bitmap with a diagonal line and a filled corner."""
    bitmap = Bitmap(11, 7)
    bitmap.draw_line(0, 0, 10, 6)
    bitmap.fill_rect(8, 0, 3, 2)
    return bitmap


# ======================== 1-bit grayscale ========================


@pytest.mark.os_agnostic
def test_gray_png_header_is_one_bit_grayscale() -> None:
    """The IHDR declares the bitmap size, bit depth 1 and colour type 0."""
    png = read_png(encode_png(sample_bitmap()))
    assert (png.width, png.height, png.depth, png.colour) == (11, 7, 1, 0)


@pytest.mark.os_agnostic
@given(
    st.integers(min_value=1, max_value=19).flatmap(
        lambda width: st.lists(st.text(alphabet="#.", min_size=width, max_size=width), min_size=1, max_size=6)
    ),
    st.integers(min_value=1, max_value=3),
)
def test_gray_png_pixels_match_bitmap_at_any_scale(rows: list[str], scale: int) -> None:
    """Black bitmap pixels are 0 and white ones 1, each repeated as a scale x scale block."""
    bitmap = Bitmap.from_rows(rows)
    png = read_png(encode_png(bitmap, scale=scale))
    assert (png.width, png.height) == (bitmap.width * scale, bitmap.height * scale)
    for y in range(png.height):
        for x in range(png.width):
            assert gray_pixel(png, x, y) == (0 if bitmap.get(x // scale, y // scale) else 1)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("scale", [0, -1])
def test_gray_png_rejects_scale_below_one(scale: int) -> None:
    """Scale must be at least 1."""
    with pytest.raises(ValueError, match="scale must be at least 1"):
        encode_png(Bitmap(2, 2), scale=scale)


@pytest.mark.os_agnostic
def test_large_white_label_compresses_well() -> None:
    """An almost white label image compresses to a small fraction of its raw size."""
    bitmap = Bitmap(812, 1218)
    bitmap.draw_circle(400, 600, 300, outline=4, fill=FillPattern.SHADE_12)
    data = encode_png(bitmap)
    assert len(data) < 812 * 1218 // 8 // 4


# ======================== 8-bit RGB ========================


@pytest.mark.os_agnostic
def test_rgb_png_round_trips_pixels() -> None:
    """RGB rows come back byte for byte under colour type 2, depth 8."""
    pixels = [bytes([255, 0, 0, 0, 255, 0]), bytes([0, 0, 255, 10, 20, 30])]
    png = read_png(encode_png_rgb(pixels, width=2, height=2))
    assert (png.width, png.height, png.depth, png.colour) == (2, 2, 8, 2)
    assert png.scanlines == pixels


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("pixels", "width", "height", "message"),
    [
        ([], 0, 0, "at least 1 x 1"),
        ([b"\x00\x00\x00"], 1, 2, "expected 2 rows"),
        ([b"\x00\x00"], 1, 1, "every row must be 3 bytes"),
        ([b"\x00" * 6, b"\x00" * 3], 2, 2, "every row must be 6 bytes"),
    ],
)
def test_rgb_png_rejects_bad_input(pixels: list[bytes], width: int, height: int, message: str) -> None:
    """Wrong sizes or row lengths raise ValueError."""
    with pytest.raises(ValueError, match=message):
        encode_png_rgb(pixels, width=width, height=height)


# ======================== cross-check with Pillow ========================


@pytest.mark.os_agnostic
def test_pillow_reads_gray_png_like_the_bitmap() -> None:
    """Where Pillow is installed it opens the 1-bit PNG with the same pixels."""
    image_module = pytest.importorskip("PIL.Image")
    bitmap = sample_bitmap()
    with image_module.open(io.BytesIO(encode_png(bitmap, scale=2))) as image:
        image.load()
        assert image.size == (22, 14)
        for y in range(14):
            for x in range(22):
                assert (image.getpixel((x, y)) == 0) == bitmap.get(x // 2, y // 2)


@pytest.mark.os_agnostic
def test_pillow_reads_rgb_png() -> None:
    """Where Pillow is installed it opens the RGB PNG with the same colours."""
    image_module = pytest.importorskip("PIL.Image")
    pixels = [bytes([255, 0, 0, 0, 255, 0]), bytes([0, 0, 255, 10, 20, 30])]
    with image_module.open(io.BytesIO(encode_png_rgb(pixels, width=2, height=2))) as image:
        image.load()
        assert image.mode == "RGB"
        assert image.getpixel((0, 0)) == (255, 0, 0)
        assert image.getpixel((1, 1)) == (10, 20, 30)
