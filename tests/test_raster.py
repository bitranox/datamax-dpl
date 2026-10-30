"""Raster tests: pixels, clipping, lines, polygons, circles, fill patterns, packing, blits and combining."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from datamax_dpl.domain.enums import CombineMode, FillPattern
from datamax_dpl.domain.raster import TILE_SIZE, Bitmap, pattern_pixel

HUGE = 10**12
NOTHING: frozenset[tuple[int, int]] = frozenset()


def black_set(bitmap: Bitmap) -> set[tuple[int, int]]:
    """Return every black pixel as (x, y)."""
    return {(x, y) for y in range(bitmap.height) for x in range(bitmap.width) if bitmap.get(x, y)}


def black_rows(bitmap: Bitmap) -> list[int]:
    """Return the indices of rows holding at least one black pixel."""
    return [y for y in range(bitmap.height) if any(bitmap.get(x, y) for x in range(bitmap.width))]


# ======================== construction and pixels ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("width", "height"), [(0, 1), (1, 0), (-1, 5)])
def test_bitmap_rejects_empty_size(width: int, height: int) -> None:
    """A bitmap needs at least one pixel in each direction."""
    with pytest.raises(ValueError, match="at least 1 x 1"):
        Bitmap(width, height)


@pytest.mark.os_agnostic
def test_new_bitmap_is_white() -> None:
    """A fresh bitmap has no black pixels."""
    bitmap = Bitmap(17, 9)
    assert bitmap.black_pixels() == 0
    assert (bitmap.width, bitmap.height) == (17, 9)


@pytest.mark.os_agnostic
def test_set_and_get_round_trip() -> None:
    """A pixel set black reads back black and can be cleared again."""
    bitmap = Bitmap(10, 4)
    bitmap.set(9, 3)
    assert bitmap.get(9, 3)
    assert bitmap.black_pixels() == 1
    bitmap.set(9, 3, black=False)
    assert not bitmap.get(9, 3)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("x", "y"), [(-1, 0), (0, -1), (10, 0), (0, 4), (-HUGE, HUGE)])
def test_out_of_range_pixels_are_clipped(x: int, y: int) -> None:
    """Setting outside the image does nothing; reading outside is white."""
    bitmap = Bitmap(10, 4)
    bitmap.set(x, y)
    assert bitmap.black_pixels() == 0
    assert not bitmap.get(x, y)


@pytest.mark.os_agnostic
def test_repr_names_size_and_black_count() -> None:
    """The repr shows the size and how many pixels are black."""
    bitmap = Bitmap(3, 2)
    bitmap.set(1, 1)
    assert repr(bitmap) == "Bitmap(width=3, height=2, black=1)"


# ======================== from_rows / rows / from_packed_rows ========================


@pytest.mark.os_agnostic
def test_from_rows_reads_both_alphabets_top_row_first() -> None:
    """``1``/``#`` are black and ``0``/``.`` white; the first string is the top row."""
    bitmap = Bitmap.from_rows(["#..1", "0.0."])
    assert black_set(bitmap) == {(0, 0), (3, 0)}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([], "same, non-zero length"),
        (["", ""], "same, non-zero length"),
        (["##", "#"], "same, non-zero length"),
        (["#x"], "only contain"),
    ],
)
def test_from_rows_rejects_bad_rows(rows: list[str], message: str) -> None:
    """Empty, ragged or foreign-character rows are refused."""
    with pytest.raises(ValueError, match=message):
        Bitmap.from_rows(rows)


@pytest.mark.os_agnostic
def test_rows_pack_msb_first_and_pad_white() -> None:
    """Pixel 0 is the most significant bit; the padding bits of the last byte are 0."""
    bitmap = Bitmap.from_rows(["#........#", ".........."])
    assert bitmap.rows() == [b"\x80\x40", b"\x00\x00"]


@pytest.mark.os_agnostic
@given(
    st.integers(min_value=1, max_value=20).flatmap(
        lambda width: st.lists(
            st.text(alphabet="#.", min_size=width, max_size=width),
            min_size=1,
            max_size=6,
        )
    )
)
def test_packed_rows_round_trip(rows: list[str]) -> None:
    """``from_packed_rows(rows())`` rebuilds the same pixels."""
    bitmap = Bitmap.from_rows(rows)
    again = Bitmap.from_packed_rows(bitmap.rows(), width=bitmap.width)
    assert black_set(again) == black_set(bitmap)
    assert (again.width, again.height) == (bitmap.width, bitmap.height)


@pytest.mark.os_agnostic
def test_from_packed_rows_rejects_wrong_row_length() -> None:
    """A packed row must be exactly ceil(width / 8) bytes long."""
    with pytest.raises(ValueError, match="2 bytes for width 9"):
        Bitmap.from_packed_rows([b"\x00"], width=9)


@pytest.mark.os_agnostic
def test_from_packed_rows_rejects_no_rows() -> None:
    """No rows means no image."""
    with pytest.raises(ValueError, match="at least 1 x 1"):
        Bitmap.from_packed_rows([], width=8)


# ======================== fill_rect and patterns ========================


@pytest.mark.os_agnostic
def test_fill_rect_solid_covers_exactly_the_rectangle() -> None:
    """A solid fill blackens width x height pixels from its top-left corner."""
    bitmap = Bitmap(10, 10)
    bitmap.fill_rect(2, 3, 4, 5)
    assert black_set(bitmap) == {(x, y) for x in range(2, 6) for y in range(3, 8)}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("width", "height"), [(0, 5), (5, 0), (-3, 5)])
def test_fill_rect_with_empty_size_draws_nothing(width: int, height: int) -> None:
    """A zero or negative extent fills nothing."""
    bitmap = Bitmap(10, 10)
    bitmap.fill_rect(2, 2, width, height)
    assert bitmap.black_pixels() == 0


@pytest.mark.os_agnostic
def test_fill_rect_clips_huge_rectangle() -> None:
    """A rectangle far larger than the image fills the whole image and does not raise."""
    bitmap = Bitmap(13, 7)
    bitmap.fill_rect(-HUGE, -HUGE, 3 * HUGE, 3 * HUGE)
    assert bitmap.black_pixels() == 13 * 7


@pytest.mark.os_agnostic
def test_fill_rect_none_pattern_draws_nothing() -> None:
    """FillPattern.NONE leaves the area white."""
    bitmap = Bitmap(16, 16)
    bitmap.fill_rect(0, 0, 16, 16, pattern=FillPattern.NONE)
    assert bitmap.black_pixels() == 0


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("pattern", "nominal"),
    [
        (FillPattern.SHADE_6, 6),
        (FillPattern.SHADE_12, 12),
        (FillPattern.SHADE_25, 25),
        (FillPattern.SHADE_38, 38),
        (FillPattern.SHADE_50, 50),
    ],
)
def test_shade_ratio_is_within_three_points(pattern: FillPattern, nominal: int) -> None:
    """Each shade's black share over a 64 x 64 fill is within 3 percentage points of its name."""
    bitmap = Bitmap(64, 64)
    bitmap.fill_rect(0, 0, 64, 64, pattern=pattern)
    ratio = 100 * bitmap.black_pixels() / (64 * 64)
    assert abs(ratio - nominal) <= 3


@pytest.mark.os_agnostic
def test_shades_get_darker_in_order() -> None:
    """Every darker shade blackens a superset of the lighter one's pixels (ordered dither)."""
    shades = [FillPattern.SHADE_6, FillPattern.SHADE_12, FillPattern.SHADE_25, FillPattern.SHADE_38]
    for lighter, darker in zip(shades, [*shades[1:], FillPattern.SHADE_50], strict=True):
        cells = [(x, y) for x in range(TILE_SIZE) for y in range(TILE_SIZE)]
        assert all(pattern_pixel(darker, x, y) for x, y in cells if pattern_pixel(lighter, x, y))


@pytest.mark.os_agnostic
@pytest.mark.parametrize("pattern", list(FillPattern))
def test_every_pattern_tiles_with_period_eight(pattern: FillPattern) -> None:
    """Each pattern repeats every 8 pixels in both directions, also at negative coordinates."""
    for x in range(-8, 8):
        for y in range(-8, 8):
            assert pattern_pixel(pattern, x, y) == pattern_pixel(pattern, x + 8, y + 16)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "pattern",
    [
        FillPattern.DIAMONDS,
        FillPattern.CIRCLES,
        FillPattern.RIGHT_DIAGONAL,
        FillPattern.LEFT_DIAGONAL,
        FillPattern.GRID,
    ],
)
def test_line_patterns_are_partly_black(pattern: FillPattern) -> None:
    """The drawn patterns are neither empty nor solid."""
    black = sum(pattern_pixel(pattern, x, y) for x in range(8) for y in range(8))
    assert 0 < black < 64


@pytest.mark.os_agnostic
def test_diagonals_lean_the_right_way() -> None:
    """With row 0 at the top, "/" rises to the right and "\\" falls to the right."""
    assert pattern_pixel(FillPattern.RIGHT_DIAGONAL, 7, 0)
    assert pattern_pixel(FillPattern.RIGHT_DIAGONAL, 0, 7)
    assert pattern_pixel(FillPattern.LEFT_DIAGONAL, 0, 0)
    assert pattern_pixel(FillPattern.LEFT_DIAGONAL, 7, 7)


@pytest.mark.os_agnostic
def test_grid_has_full_lines() -> None:
    """The grid tile is a full top row plus a full left column."""
    assert all(pattern_pixel(FillPattern.GRID, x, 0) for x in range(8))
    assert all(pattern_pixel(FillPattern.GRID, 0, y) for y in range(8))
    assert not pattern_pixel(FillPattern.GRID, 3, 3)


@pytest.mark.os_agnostic
def test_fill_rect_pattern_matches_pattern_pixel() -> None:
    """A patterned fill blackens exactly the pixels pattern_pixel reports, anchored at the origin."""
    bitmap = Bitmap(30, 20)
    bitmap.fill_rect(3, 5, 20, 11, pattern=FillPattern.DIAMONDS)
    expected = {(x, y) for x in range(3, 23) for y in range(5, 16) if pattern_pixel(FillPattern.DIAMONDS, x, y)}
    assert black_set(bitmap) == expected


# ======================== lines ========================


@pytest.mark.os_agnostic
def test_thick_horizontal_line_covers_exactly_three_rows() -> None:
    """A thickness-3 horizontal line blackens exactly 3 rows, centred on the line."""
    bitmap = Bitmap(40, 20)
    bitmap.draw_line(5, 10, 30, 10, thickness=3)
    assert black_rows(bitmap) == [9, 10, 11]
    assert bitmap.black_pixels() == 3 * (26 + 2)


@pytest.mark.os_agnostic
def test_thin_line_hits_both_endpoints() -> None:
    """A one-pixel line includes its start and end points."""
    bitmap = Bitmap(20, 20)
    bitmap.draw_line(2, 3, 17, 11)
    assert bitmap.get(2, 3)
    assert bitmap.get(17, 11)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("x0", "y0", "x1", "y1"), [(0, 0, 9, 9), (9, 0, 0, 9), (3, 0, 5, 9), (9, 2, 0, 6)])
def test_slanted_line_is_one_pixel_per_major_step(x0: int, y0: int, x1: int, y1: int) -> None:
    """A thin slanted line has exactly one black pixel per step along its longer axis."""
    bitmap = Bitmap(10, 10)
    bitmap.draw_line(x0, y0, x1, y1)
    assert bitmap.black_pixels() == max(abs(x1 - x0), abs(y1 - y0)) + 1


@pytest.mark.os_agnostic
def test_line_direction_does_not_matter() -> None:
    """Drawing a line backwards gives the same pixels."""
    forward, backward = Bitmap(30, 30), Bitmap(30, 30)
    forward.draw_line(1, 2, 27, 19, thickness=2)
    backward.draw_line(27, 19, 1, 2, thickness=2)
    assert black_set(forward) == black_set(backward)


@pytest.mark.os_agnostic
def test_single_point_line_stamps_one_square() -> None:
    """A line whose ends coincide stamps one thickness x thickness square."""
    bitmap = Bitmap(10, 10)
    bitmap.draw_line(5, 5, 5, 5, thickness=2)
    assert black_set(bitmap) == {(5, 5), (6, 5), (5, 6), (6, 6)}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("x0", "y0", "x1", "y1"),
    [(-HUGE, 5, HUGE, 5), (5, -HUGE, 5, HUGE), (-HUGE, -HUGE, HUGE, HUGE), (HUGE, HUGE, HUGE + 3, HUGE)],
)
def test_huge_lines_clip_without_raising(x0: int, y0: int, x1: int, y1: int) -> None:
    """Lines with enormous coordinates draw their visible part only and return promptly."""
    bitmap = Bitmap(20, 20)
    bitmap.draw_line(x0, y0, x1, y1, thickness=3)
    assert bitmap.black_pixels() <= 20 * 20


@pytest.mark.os_agnostic
def test_huge_horizontal_line_crosses_whole_image() -> None:
    """A clipped horizontal line still spans the full width."""
    bitmap = Bitmap(20, 20)
    bitmap.draw_line(-HUGE, 5, HUGE, 5)
    assert black_set(bitmap) == {(x, 5) for x in range(20)}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("x0", "y0", "x1", "y1"), [(-5, 5, -1, 5), (5, -9, 5, -1), (20, 5, 26, 5), (5, 20, 5, 30)])
def test_thick_line_just_outside_still_reaches_the_edge(x0: int, y0: int, x1: int, y1: int) -> None:
    """A thick line lying entirely outside the image paints the part of its width that overlaps."""
    bitmap = Bitmap(20, 20)
    bitmap.draw_line(x0, y0, x1, y1, thickness=3)
    assert bitmap.black_pixels() == 3


@pytest.mark.os_agnostic
@pytest.mark.parametrize("thickness", [0, -2])
def test_line_rejects_thickness_below_one(thickness: int) -> None:
    """Thickness must be at least 1."""
    with pytest.raises(ValueError, match="at least 1"):
        Bitmap(5, 5).draw_line(0, 0, 4, 4, thickness=thickness)


# ======================== polygons ========================


TRIANGLE = [(10, 10), (50, 10), (30, 40)]


@pytest.mark.os_agnostic
def test_solid_triangle_has_black_centroid_and_white_outside() -> None:
    """A SOLID triangle blackens its centroid; a point outside stays white."""
    bitmap = Bitmap(60, 60)
    bitmap.draw_polygon(TRIANGLE, fill=FillPattern.SOLID)
    assert bitmap.get(30, 20)
    assert not bitmap.get(5, 5)
    assert not bitmap.get(48, 30)


@pytest.mark.os_agnostic
def test_unfilled_triangle_has_white_centroid_and_black_vertices() -> None:
    """Without a fill only the outline is drawn, through every vertex."""
    bitmap = Bitmap(60, 60)
    bitmap.draw_polygon(TRIANGLE)
    assert not bitmap.get(30, 20)
    assert all(bitmap.get(x, y) for x, y in TRIANGLE)


@pytest.mark.os_agnostic
def test_polygon_without_outline_draws_fill_only() -> None:
    """``outline=0`` fills the half-open interior and draws no edge."""
    bitmap = Bitmap(10, 10)
    bitmap.draw_polygon([(0, 0), (4, 0), (4, 4), (0, 4)], outline=0, fill=FillPattern.SOLID)
    assert black_set(bitmap) == {(x, y) for x in range(4) for y in range(4)}


@pytest.mark.os_agnostic
def test_polygon_fill_uses_even_odd_rule() -> None:
    """A self-overlapping pentagram leaves its centre white under the even-odd rule."""
    star = [(50, 0), (79, 90), (2, 34), (98, 34), (21, 90)]
    bitmap = Bitmap(100, 100)
    bitmap.draw_polygon(star, outline=0, fill=FillPattern.SOLID)
    assert not bitmap.get(50, 50)
    assert bitmap.get(50, 15)


@pytest.mark.os_agnostic
def test_polygon_with_shared_vertex_rows_fills_cleanly() -> None:
    """A diamond whose left and right vertices share a row has no gap on that row."""
    bitmap = Bitmap(21, 21)
    bitmap.draw_polygon([(10, 0), (20, 10), (10, 20), (0, 10)], outline=0, fill=FillPattern.SOLID)
    assert all(bitmap.get(x, 10) for x in range(20))


@pytest.mark.os_agnostic
def test_polygon_rejects_empty_points() -> None:
    """A polygon needs at least one point."""
    with pytest.raises(ValueError, match="at least one point"):
        Bitmap(5, 5).draw_polygon([])


@pytest.mark.os_agnostic
def test_polygon_rejects_negative_outline() -> None:
    """The outline thickness cannot be negative."""
    with pytest.raises(ValueError, match="outline must not be negative"):
        Bitmap(5, 5).draw_polygon([(0, 0), (4, 0), (4, 4)], outline=-1)


@pytest.mark.os_agnostic
def test_huge_polygon_clips_without_raising() -> None:
    """A polygon far larger than the image fills all of it."""
    bitmap = Bitmap(12, 8)
    bitmap.draw_polygon([(-HUGE, -HUGE), (HUGE, -HUGE), (HUGE, HUGE), (-HUGE, HUGE)], fill=FillPattern.SOLID)
    assert bitmap.black_pixels() == 12 * 8


# ======================== circles ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize("radius", [1, 2, 5, 17])
def test_unfilled_circle_has_black_extremes_and_white_centre(radius: int) -> None:
    """The four extreme points of an outline circle are black and its centre stays white."""
    bitmap = Bitmap(50, 50)
    bitmap.draw_circle(25, 25, radius)
    extremes = [(25 + radius, 25), (25 - radius, 25), (25, 25 + radius), (25, 25 - radius)]
    assert all(bitmap.get(x, y) for x, y in extremes)
    assert not bitmap.get(25, 25)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("radius", [1, 5, 17])
def test_solid_circle_has_black_centre(radius: int) -> None:
    """A SOLID circle blackens its centre and nothing beyond the radius on the axes."""
    bitmap = Bitmap(50, 50)
    bitmap.draw_circle(25, 25, radius, fill=FillPattern.SOLID)
    assert bitmap.get(25, 25)
    assert not bitmap.get(25 + radius + 1, 25)
    assert not bitmap.get(25, 25 - radius - 1)


@pytest.mark.os_agnostic
def test_circle_outline_is_closed_ring() -> None:
    """Every row and column the circle spans holds at least one outline pixel on each side."""
    bitmap = Bitmap(41, 41)
    bitmap.draw_circle(20, 20, 15)
    for offset in range(-15, 16):
        row = [x for x in range(41) if bitmap.get(x, 20 + offset)]
        assert row
        assert min(row) <= 20 <= max(row)


@pytest.mark.os_agnostic
def test_thick_outline_grows_inward() -> None:
    """An outline of thickness 3 covers radius, radius-1 and radius-2 on the axis, not the centre."""
    bitmap = Bitmap(41, 41)
    bitmap.draw_circle(20, 20, 10, outline=3)
    assert [x for x in range(41) if bitmap.get(x, 20) and x > 20] == [28, 29, 30]


@pytest.mark.os_agnostic
def test_outline_thicker_than_radius_is_a_disc() -> None:
    """When the outline reaches the centre the circle is solid."""
    ring, disc = Bitmap(21, 21), Bitmap(21, 21)
    ring.draw_circle(10, 10, 4, outline=9)
    disc.draw_circle(10, 10, 4, outline=0, fill=FillPattern.SOLID)
    assert black_set(ring) == black_set(disc)


@pytest.mark.os_agnostic
def test_radius_zero_is_one_pixel() -> None:
    """A circle of radius 0 is its centre pixel."""
    bitmap = Bitmap(5, 5)
    bitmap.draw_circle(2, 2, 0)
    assert black_set(bitmap) == {(2, 2)}


@pytest.mark.os_agnostic
def test_patterned_circle_stays_inside_disc() -> None:
    """A patterned fill only blackens pattern pixels within the disc."""
    bitmap = Bitmap(41, 41)
    bitmap.draw_circle(20, 20, 12, outline=0, fill=FillPattern.SHADE_50)
    for x, y in black_set(bitmap):
        assert (x - 20) ** 2 + (y - 20) ** 2 <= 12 * 12 + 12
        assert pattern_pixel(FillPattern.SHADE_50, x, y)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("radius", "outline"), [(-1, 1), (3, -1)])
def test_circle_rejects_negative_sizes(radius: int, outline: int) -> None:
    """Radius and outline must not be negative."""
    with pytest.raises(ValueError, match="must not be negative"):
        Bitmap(5, 5).draw_circle(2, 2, radius, outline=outline)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("cx", "cy", "radius"), [(-HUGE, -HUGE, 5), (HUGE, 3, 2), (5, 5, HUGE), (-HUGE, 4, HUGE)])
def test_huge_circles_clip_without_raising(cx: int, cy: int, radius: int) -> None:
    """Circles far outside or far larger than the image clip without error."""
    bitmap = Bitmap(10, 10)
    bitmap.draw_circle(cx, cy, radius, outline=2, fill=FillPattern.SOLID)
    assert bitmap.black_pixels() <= 100


# ======================== blit ========================


@pytest.mark.os_agnostic
def test_blit_ors_other_bitmap_at_offset() -> None:
    """Black pixels of the source land at the offset; existing black pixels stay."""
    canvas = Bitmap(10, 10)
    canvas.set(0, 0)
    canvas.blit(Bitmap.from_rows(["#.", ".#"]), x=4, y=6)
    assert black_set(canvas) == {(0, 0), (4, 6), (5, 7)}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("x", "y", "expected"),
    [
        (-1, -1, {(0, 0), (0, 1)}),
        (8, 8, {(8, 8), (9, 8), (8, 9), (9, 9)}),
        (9, 0, {(9, 0), (9, 1), (9, 2)}),
        (-2, 0, NOTHING),
        (10, 0, NOTHING),
        (-HUGE, 0, NOTHING),
        (HUGE, 0, NOTHING),
        (0, -HUGE, NOTHING),
        (0, HUGE, NOTHING),
    ],
)
def test_blit_clips_at_every_edge(x: int, y: int, expected: frozenset[tuple[int, int]]) -> None:
    """Parts of the source outside the target are dropped, never wrapped."""
    canvas = Bitmap(10, 10)
    canvas.blit(Bitmap.from_rows(["##", "##", "##"]), x=x, y=y)
    assert black_set(canvas) == expected


@pytest.mark.os_agnostic
def test_blit_of_wider_source_clips_right_edge() -> None:
    """A source wider than the target keeps only its overlapping columns."""
    canvas = Bitmap(4, 1)
    canvas.blit(Bitmap.from_rows(["#.#.#.#."]), x=-1, y=0)
    assert black_set(canvas) == {(1, 0), (3, 0)}


# ======================== combine and bounds ========================


def square(size: int) -> Bitmap:
    """A solid black ``size`` x ``size`` bitmap."""
    return Bitmap.from_rows(["#" * size] * size)


def overlapping(mode: CombineMode) -> Bitmap:
    """Two 4x4 squares, the second put 2 dots right and down of the first with ``mode``."""
    canvas = Bitmap(8, 8)
    canvas.combine(square(4), 0, 0, mode=CombineMode.OR)
    canvas.combine(square(4), 2, 2, mode=mode)
    return canvas


FIRST = {(x, y) for x in range(4) for y in range(4)}
SECOND = {(x, y) for x in range(2, 6) for y in range(2, 6)}


@pytest.mark.os_agnostic
def test_combine_xor_leaves_the_overlap_white() -> None:
    assert black_set(overlapping(CombineMode.XOR)) == FIRST ^ SECOND


@pytest.mark.os_agnostic
def test_combine_or_keeps_the_overlap_black() -> None:
    assert black_set(overlapping(CombineMode.OR)) == FIRST | SECOND


@pytest.mark.os_agnostic
def test_combine_opaque_whitens_the_target_under_the_layer_rectangle_first() -> None:
    """A ring layer put opaque on black: its white middle whitens what it covers too."""
    canvas = square(8)
    ring = Bitmap.from_rows(["####", "#..#", "#..#", "####"])
    canvas.combine(ring, 2, 2, mode=CombineMode.OPAQUE)
    middle = {(3, 3), (4, 3), (3, 4), (4, 4)}
    assert black_set(canvas) == {(x, y) for x in range(8) for y in range(8)} - middle


@pytest.mark.os_agnostic
@pytest.mark.parametrize("mode", list(CombineMode))
@pytest.mark.parametrize(("x", "y"), [(-3, -3), (6, 6), (-HUGE, 0), (HUGE, 0), (0, HUGE), (0, -HUGE)])
def test_combine_clips_at_every_edge(mode: CombineMode, x: int, y: int) -> None:
    """Only the overlapping part of the layer acts, in every mode; nothing wraps round."""
    canvas = square(8)
    canvas.combine(square(4), x, y, mode=mode)
    covered = {(x + u, y + v) for u in range(4) for v in range(4)} & {(a, b) for a in range(8) for b in range(8)}
    whole = {(a, b) for a in range(8) for b in range(8)}
    assert black_set(canvas) == (whole - covered if mode is CombineMode.XOR else whole)


@pytest.mark.os_agnostic
def test_blit_is_combine_or() -> None:
    blitted, combined = Bitmap(8, 8), Bitmap(8, 8)
    for canvas in (blitted, combined):
        canvas.set(3, 3)
    blitted.blit(square(4), x=2, y=2)
    combined.combine(square(4), 2, 2, mode=CombineMode.OR)
    assert black_set(blitted) == black_set(combined)


@pytest.mark.os_agnostic
def test_bounds_of_a_blank_bitmap_are_none() -> None:
    assert Bitmap(5, 5).bounds() is None


@pytest.mark.os_agnostic
def test_bounds_hold_every_black_pixel_tightly() -> None:
    bitmap = Bitmap(20, 10)
    for x, y in ((3, 7), (11, 2), (6, 5)):
        bitmap.set(x, y)
    assert bitmap.bounds() == (3, 2, 9, 6)
