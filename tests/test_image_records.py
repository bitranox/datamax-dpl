"""Image record tests: the Datamax 7-bit ASCII image format, its repeats, limits and decoder."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from datamax_dpl.domain.image_records import decode_image, download_command, encode_image
from datamax_dpl.domain.raster import Bitmap


def black_set(bitmap: Bitmap) -> set[tuple[int, int]]:
    """Return every black pixel as (x, y)."""
    return {(x, y) for y in range(bitmap.height) for x in range(bitmap.width) if bitmap.get(x, y)}


def records(data: bytes) -> list[bytes]:
    """Split encoded data into its records, dropping the empty piece after the last CR."""
    pieces = data.split(b"\r")
    assert pieces[-1] == b""
    return pieces[:-1]


# ======================== encode ========================


@pytest.mark.os_agnostic
def test_encode_single_row_record_format() -> None:
    """A row record is 80, the byte count as two hex digits, then upper-case hex bytes and CR."""
    bitmap = Bitmap.from_rows(["#.#.####" + "....#..#" + "##......"])
    assert encode_image(bitmap) == b"8003AF09C0\rFFFF\r"


@pytest.mark.os_agnostic
def test_encode_emits_top_row_first() -> None:
    """The first row record is the top row of the bitmap (the reading taken in the module)."""
    bitmap = Bitmap.from_rows(["#.......", "........"])
    assert records(encode_image(bitmap)) == [b"800180", b"800100", b"FFFF"]


@pytest.mark.os_agnostic
def test_encode_folds_identical_rows_into_repeat_record() -> None:
    """Rows equal to the one before become a 0000FFnn repeat record."""
    bitmap = Bitmap.from_rows(["#......."] * 4 + ["........"])
    assert records(encode_image(bitmap)) == [b"800180", b"0000FF03", b"800100", b"FFFF"]


@pytest.mark.os_agnostic
def test_long_run_is_split_into_repeat_records_that_fit_two_hex_digits() -> None:
    """300 identical rows split so every repeat count is 01-FF and the total is still 300 rows."""
    bitmap = Bitmap(16, 300)
    bitmap.fill_rect(0, 0, 3, 300)
    parts = records(encode_image(bitmap))
    repeats = [int(part[6:], 16) for part in parts if part.startswith(b"0000FF")]
    rows = [part for part in parts if part.startswith(b"80")]
    assert all(1 <= count <= 0xFF for count in repeats)
    assert all(len(part) == 8 for part in parts if part.startswith(b"0000FF"))
    assert len(rows) + sum(repeats) == 300
    assert len(repeats) == 2
    assert black_set(decode_image(encode_image(bitmap))) == black_set(bitmap)


@pytest.mark.os_agnostic
def test_run_of_exactly_256_rows_needs_one_repeat() -> None:
    """One row record plus a repeat of FF covers 256 rows without a second record pair."""
    parts = records(encode_image(Bitmap(8, 256)))
    assert parts == [b"800100", b"0000FFFF", b"FFFF"]


@pytest.mark.os_agnostic
def test_encode_accepts_widest_row() -> None:
    """A row of 255 bytes (2040 pixels) still fits the two-digit byte count."""
    data = encode_image(Bitmap(255 * 8, 1))
    assert data.startswith(b"80FF")


@pytest.mark.os_agnostic
def test_encode_rejects_rows_wider_than_255_bytes() -> None:
    """A row needing 256 bytes is refused, naming the limit."""
    with pytest.raises(ValueError, match="at most 255 bytes"):
        encode_image(Bitmap(255 * 8 + 1, 1))


# ======================== decode ========================


@pytest.mark.os_agnostic
@given(
    st.integers(min_value=1, max_value=24).flatmap(
        lambda width: st.lists(
            st.sampled_from(["#" * width, "." * width]) | st.text(alphabet="#.", min_size=width, max_size=width),
            min_size=1,
            max_size=12,
        )
    )
)
def test_encode_decode_round_trip(rows: list[str]) -> None:
    """Decoding the encoded image gives back every pixel; padding columns come back white."""
    bitmap = Bitmap.from_rows(rows)
    decoded = decode_image(encode_image(bitmap))
    assert decoded.height == bitmap.height
    assert decoded.width == -(-bitmap.width // 8) * 8
    assert black_set(decoded) == black_set(bitmap)


@pytest.mark.os_agnostic
def test_decode_accepts_lower_case_hex_and_chained_repeats() -> None:
    """The decoder reads lower-case hex and a repeat record that follows another repeat."""
    decoded = decode_image(b"8001ff\r0000FF02\r0000FF01\rFFFF\r")
    assert decoded.height == 4
    assert decoded.black_pixels() == 32


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"800180\rFFFF", "end with a carriage return"),
        (b"800180\r", "FFFF record"),
        (b"\r", "FFFF record"),
        (b"FFFF\r", "no rows"),
        (b"0000FF01\rFFFF\r", "needs a row before it"),
        (b"800180\r0000FF00\rFFFF\r", "repeat count must be at least 1"),
        (b"800180\r0000FFG1\rFFFF\r", "repeat count must be two hex digits"),
        (b"8000\rFFFF\r", "row byte count must be at least 1"),
        (b"8002AB\rFFFF\r", "must hold 2 bytes"),
        (b"8001ZZ\rFFFF\r", "must hold 1 bytes"),
        (b"8001 A\rFFFF\r", "must hold 1 bytes"),
        (b"800180\r8002ABCD\rFFFF\r", "same byte count"),
        (b"800180\rFFFF\r800180\rFFFF\r", "unknown image record"),
        (b"800180\r1234\rFFFF\r", "unknown image record"),
        (b"800180\r0000FF0102\rFFFF\r", "unknown image record"),
    ],
)
def test_decode_rejects_malformed_data(data: bytes, message: str) -> None:
    """Malformed, truncated or inconsistent records raise ValueError naming the problem."""
    with pytest.raises(ValueError, match=message):
        decode_image(data)


# ======================== download_command ========================


@pytest.mark.os_agnostic
def test_download_command_wraps_records_in_header() -> None:
    """The command is STX IAAF, the name, CR, then the image records."""
    bitmap = Bitmap.from_rows(["#......."])
    assert download_command("LOGO_1", bitmap) == b"\x02IAAFLOGO_1\r800180\rFFFF\r"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("name", ["", "NINECHARS", "BAD-NAME", "A B", "X\n", "A" + chr(0x00E4)])
def test_download_command_rejects_bad_names(name: str) -> None:
    """Names must be 1-8 letters, digits or underscores."""
    with pytest.raises(ValueError, match="image name"):
        download_command(name, Bitmap(8, 1))
