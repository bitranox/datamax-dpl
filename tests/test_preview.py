"""The domain preview against the encoder: what the job places is what the picture shows.

Expected geometry is read back from the rendered job (the record's numbers, in the DPL unit the job
announces), never worked out by hand, so a test here fails when preview and encoder disagree.
"""

from __future__ import annotations

import re
import time

import pytest

from datamax_dpl.domain.decoder import explain
from datamax_dpl.domain.enums import Attribute, Rotation, Unit
from datamax_dpl.domain.label import Barcode, Box, Element, Image, Label, Line, ScalableText, Text
from datamax_dpl.domain.preview import canvas_for, mask_area, preview_bitmap, text_box
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.raster import Bitmap
from datamax_dpl.domain.render import build_job, job_context
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.symbology import Symbology
from datamax_dpl.domain.units import MM_PER_INCH, Axis, DplUnit

#: A 203-dpi model with metric formats and no calibration defaults: the job is nominal geometry.
MODEL = "dmx-400"
#: Inch formats at 2.03 dots per unit; the Allegro 2's 2-dot unit with its along scale and offset;
#: metric formats at nominal geometry.
MODELS = ["generic", "allegro-2", "dmx-400"]
LINE_RECORD = re.compile(rb"1X11000(\d{4})(\d{4})[Ll](\d{3,4}?)(\d{3,4})\r")


def black_set(bitmap: Bitmap) -> set[tuple[int, int]]:
    return {(x, y) for y in range(bitmap.height) for x in range(bitmap.width) if bitmap.get(x, y)}


def framed(label: Label, model: str = MODEL) -> Bitmap:
    """The preview of an empty label: just the one-dot frame, to subtract from a drawing."""
    canvas = canvas_for(label, get_profile(model))
    frame = Bitmap(canvas.width, canvas.height)
    frame.draw_polygon([(0, 0), (canvas.width - 1, 0), (canvas.width - 1, canvas.height - 1), (0, canvas.height - 1)])
    return frame


def drawn(label: Label, model: str = MODEL, options: RenderOptions | None = None) -> set[tuple[int, int]]:
    """The black pixels the preview adds to the frame."""
    picture = preview_bitmap(label, get_profile(model), options=options)
    return black_set(picture) - black_set(framed(label, model))


def mm_label(*elements: Element) -> Label:
    return Label(unit=Unit.MM, width=50, height=30, elements=list(elements))


def encoded_rectangle(label: Label, model: str = MODEL, options: RenderOptions | None = None) -> set[tuple[int, int]]:
    """The pixels of the label's one line record, read back from the job and put on nominal paper.

    The record's row and column are the rectangle's lower-left corner; its horizontal and vertical
    sizes run right and up from there. Each number turns into the dots the printer prints for it
    (``printed_dots``), then the job's scale and offset are undone: the paper at nominal size.
    """
    options = options or RenderOptions(model=model)
    result = build_job(label, options)
    assert result.ok, result.errors
    [record] = LINE_RECORD.findall(result.data)
    explanation = explain(result.data)
    ctx = job_context(label, options)
    assert explanation.label is not None
    assert explanation.label.unit is (Unit.MM if ctx.dpl_unit is DplUnit.MM_TENTHS else Unit.INCH)
    assert label.unit is Unit.MM

    def paper(units: int, axis: Axis) -> float:
        return ctx.printed_dots(units) * ctx.correction(axis).scale

    def position(units: int, axis: Axis) -> int:
        return round(paper(units, axis) - ctx.offset_mm(axis) / MM_PER_INCH * ctx.profile.dpi)

    row, column, horizontal, vertical = (int(value) for value in record)
    left, up = position(column, Axis.ACROSS), position(row, Axis.ALONG)
    width, height = round(paper(horizontal, Axis.ACROSS)), round(paper(vertical, Axis.ALONG))
    bottom = canvas_for(label, get_profile(model)).height - 1 - up
    return {(x, y) for x in range(left, left + width) for y in range(bottom - height + 1, bottom + 1)}


@pytest.mark.os_agnostic
def test_a_horizontal_line_covers_exactly_the_encoder_rectangle() -> None:
    label = mm_label(Line(x=10, y=10, x2=30, y2=10, thickness=1))
    expected = encoded_rectangle(label)
    assert expected
    assert drawn(label) == expected


@pytest.mark.os_agnostic
def test_a_vertical_line_covers_exactly_the_encoder_rectangle() -> None:
    label = mm_label(Line(x=12, y=25, x2=12, y2=5, thickness=0.5))
    expected = encoded_rectangle(label)
    assert expected
    assert drawn(label) == expected


@pytest.mark.os_agnostic
def test_a_line_drawn_right_to_left_covers_the_same_rectangle() -> None:
    label = mm_label(Line(x=30, y=10, x2=10, y2=10, thickness=1))
    assert drawn(label) == encoded_rectangle(label)


#: Label positions in mm: short, a whole inch, mid-label and near the print width.
POSITIONS_MM = [10, 25, 50, 90]


def big_label(element: Element) -> Label:
    return Label(unit=Unit.MM, width=100, height=100, elements=[element])


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mm", POSITIONS_MM)
def test_a_horizontal_line_sits_on_the_row_the_encoder_sends(model: str, mm: int) -> None:
    label = big_label(Line(x=5, y=mm, x2=mm, y2=mm, thickness=0.5))
    expected = encoded_rectangle(label, model)
    assert expected
    assert drawn(label, model) == expected


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mm", POSITIONS_MM)
def test_a_vertical_line_sits_on_the_column_the_encoder_sends(model: str, mm: int) -> None:
    label = big_label(Line(x=mm, y=5, x2=mm, y2=mm, thickness=0.5))
    expected = encoded_rectangle(label, model)
    assert expected
    assert drawn(label, model) == expected


@pytest.mark.os_agnostic
def test_generic_draws_10_mm_at_the_79_dots_its_39_hundredths_print() -> None:
    """10 mm is 79.9 dots, but the job writes 39/100 in, which a 203-dpi printhead prints as 79."""
    label = mm_label(Line(x=10, y=10, x2=30, y2=10, thickness=1))
    canvas = canvas_for(label, get_profile("generic"))
    assert canvas.point(10, 10) == (79, canvas.height - 1 - 79)


@pytest.mark.os_agnostic
def test_the_preview_follows_the_calibration_the_job_is_rendered_with() -> None:
    """An offset moves the record, and the rounding of the moved value decides the dot."""
    options = RenderOptions(model="generic", offset_across=0.3, offset_along=-0.3)
    label = mm_label(Line(x=10, y=10, x2=30, y2=10, thickness=1))
    assert drawn(label, "generic", options) == encoded_rectangle(label, "generic", options)
    assert drawn(label, "generic", options) != drawn(label, "generic")


@pytest.mark.os_agnostic
def test_a_dots_label_with_an_offset_previews_where_the_equal_mm_label_does() -> None:
    """The offset is millimetres in either unit, so undoing it leaves both on the same nominal row.

    203 dots and 25.4 mm are one inch on generic's 203-dpi printhead.
    """
    options = RenderOptions(model="generic", offset_across=10.0, offset_along=10.0)
    in_dots = Label(unit=Unit.DOTS, width=406, height=406, elements=[Line(x=203, y=203, x2=406, y2=203, thickness=2)])
    in_mm = Label(
        unit=Unit.MM, width=50.8, height=50.8, elements=[Line(x=25.4, y=25.4, x2=50.8, y2=25.4, thickness=0.25)]
    )
    assert drawn(in_mm, "generic", options) == encoded_rectangle(in_mm, "generic", options)
    assert drawn(in_dots, "generic", options) == drawn(in_mm, "generic", options)


@pytest.mark.os_agnostic
def test_options_for_another_model_than_the_profile_are_refused() -> None:
    with pytest.raises(ValueError, match=r"options are for dmx-400, not generic"):
        label = mm_label(Line(x=10, y=10, x2=30, y2=10, thickness=1))
        preview_bitmap(label, get_profile("generic"), options=RenderOptions(model="dmx-400"))


@pytest.mark.os_agnostic
def test_a_slanted_line_keeps_the_centred_stamp() -> None:
    label = mm_label(Line(x=5, y=5, x2=40, y2=25, thickness=1))
    canvas = canvas_for(label, get_profile(MODEL))
    stamped = framed(label)
    x0, y0 = canvas.point(5, 5)
    x1, y1 = canvas.point(40, 25)
    stamped.draw_line(x0, y0, x1, y1, thickness=canvas.length(1, axis=Axis.ACROSS))
    assert drawn(label) == black_set(stamped) - black_set(framed(label))


# ======================== rotation (manual p. 94 and Figure 7-1, p. 92) ========================

Rect = tuple[int, int, int, int]


def bounds(pixels: set[tuple[int, int]]) -> Rect:
    """(left, top, width, height) of a set of pixels."""
    xs, ys = [x for x, _ in pixels], [y for _, y in pixels]
    return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def turned_text(text: str, rotation: Rotation) -> Label:
    """A dots label with one font-2 text whose reference point sits in the middle of the canvas."""
    element = Text(x=100, y=100, text=text, font=2, rotation=rotation)
    return Label(unit=Unit.DOTS, width=200, height=200, elements=[element])


def reference(label: Label) -> tuple[int, int]:
    """The pixel of the label's only element's reference point."""
    [element] = label.elements
    assert isinstance(element, Text | ScalableText | Barcode)
    return canvas_for(label, get_profile(MODEL)).point(element.x, element.y)


def upright_size(text: str) -> tuple[int, int]:
    """(length, height) in dots of the text written at rotation 0."""
    label = turned_text(text, Rotation.DEG_0)
    [element] = label.elements
    assert isinstance(element, Text)
    _, _, length, height = text_box(element, canvas_for(label, get_profile(MODEL)))
    return length, height


def expected_turn(label: Label, length: int, height: int, rotation: Rotation) -> Rect:
    """The manual's rule: the field turns clockwise about its reference point (lower-left at 0).

    0 runs right and up, 90 down and right, 180 left and down, 270 up and left.
    """
    x, y = reference(label)
    return {
        Rotation.DEG_0: (x, y - height + 1, length, height),
        Rotation.DEG_90: (x, y, height, length),
        Rotation.DEG_180: (x - length + 1, y, length, height),
        Rotation.DEG_270: (x - height + 1, y - length + 1, height, length),
    }[rotation]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("rotation", list(Rotation))
def test_a_turned_text_box_follows_the_manuals_clockwise_rule(rotation: Rotation) -> None:
    label = turned_text("ABC", rotation)
    [element] = label.elements
    assert isinstance(element, Text)
    length, height = upright_size("ABC")
    assert text_box(element, canvas_for(label, get_profile(MODEL))) == expected_turn(label, length, height, rotation)
    # The box holds the gap after the last character; the drawn cells end before it.
    _, _, inked, _ = bounds(drawn(turned_text("ABC", Rotation.DEG_0)))
    assert bounds(drawn(label)) == expected_turn(label, inked, height, rotation)


@pytest.mark.os_agnostic
def test_a_text_at_90_is_taller_than_wide_and_runs_down_from_the_reference_point() -> None:
    label = turned_text("ABC", Rotation.DEG_90)
    left, top, width, height = bounds(drawn(label))
    assert height > width
    assert (left, top) == reference(label)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("rotation", list(Rotation))
def test_the_first_character_sits_at_the_reference_point(rotation: Rotation) -> None:
    """Only the A of "A  " is drawn: its cell is the one the characters advance from."""
    _, _, cell, height = bounds(drawn(turned_text("A  ", Rotation.DEG_0)))
    label = turned_text("A  ", rotation)
    assert bounds(drawn(label)) == expected_turn(label, cell, height, rotation)


@pytest.mark.os_agnostic
def test_the_four_rotations_draw_four_different_pictures() -> None:
    pictures = {frozenset(drawn(turned_text("ABC", rotation))) for rotation in Rotation}
    assert len(pictures) == len(Rotation)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("rotation", list(Rotation))
def test_a_turned_barcode_turns_about_its_reference_point(rotation: Rotation) -> None:
    upright = Barcode(x=150, y=150, symbology=Symbology.CODE128, data="12", height=40, narrow_dots=1)
    label = Label(unit=Unit.DOTS, width=300, height=300, elements=[upright.model_copy(update={"rotation": rotation})])
    canvas = canvas_for(label, get_profile(MODEL))
    _, _, length, height = text_box(upright, canvas)
    assert bounds(drawn(label)) == expected_turn(label, length, height, rotation)


@pytest.mark.os_agnostic
def test_a_turned_scalable_text_follows_the_same_rule() -> None:
    upright = ScalableText(x=100, y=100, text="AB", height_points=12)
    turned = upright.model_copy(update={"rotation": Rotation.DEG_270})
    label = Label(unit=Unit.DOTS, width=200, height=200, elements=[turned])
    _, _, length, height = text_box(upright, canvas_for(label, get_profile(MODEL)))
    assert bounds(drawn(label)) == expected_turn(label, length, height, Rotation.DEG_270)


@pytest.mark.os_agnostic
def test_a_label_without_height_is_tall_enough_for_text_turned_upward() -> None:
    element = Text(x=50, y=2, text="ABCDEFGH", font=2, rotation=Rotation.DEG_270)
    label = Label(unit=Unit.DOTS, width=200, elements=[element])
    _, top, _, _ = text_box(element, canvas_for(label, get_profile(MODEL)))
    assert top >= 1


# ======================== overlapping fields: the attribute decides ========================

#: A box outline and a solid image covering exactly the same 20 x 20 dots.
BOX = Box(x=40, y=40, width=20, height=20, thickness=2)
SQUARE = Image(x=40, y=40, name="SQUARE", bitmap=["#" * 20] * 20)
ATTRIBUTE_LINE = re.compile(rb"\rA[0-9]\r")


def dots_label(*elements: Element, attribute: Attribute | None = None) -> Label:
    return Label(unit=Unit.DOTS, width=120, height=120, attribute=attribute, elements=list(elements))


def alone(element: Element, model: str) -> set[tuple[int, int]]:
    """What the preview draws for ``element`` on its own."""
    return drawn(dots_label(element), model)


def covered(pixels: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Every pixel of the smallest rectangle holding ``pixels``."""
    left, top, width, height = bounds(pixels)
    return {(x, y) for x in range(left, left + width) for y in range(top, top + height)}


@pytest.mark.os_agnostic
def test_allegro_2_a_box_over_an_image_of_the_same_size_leaves_only_the_difference() -> None:
    """The 2026-10-29 observation on paper: the Allegro 2 has no A command, so fields XOR."""
    image, box = alone(SQUARE, "allegro-2"), alone(BOX, "allegro-2")
    assert box < image
    assert drawn(dots_label(SQUARE, BOX), "allegro-2") == image - box


@pytest.mark.os_agnostic
def test_allegro_2_ignores_a_transparent_attribute_it_cannot_send() -> None:
    label = dots_label(SQUARE, BOX, attribute=Attribute.TRANSPARENT)
    assert not ATTRIBUTE_LINE.search(build_job(label, RenderOptions(model="allegro-2")).data)
    assert drawn(label, "allegro-2") == alone(SQUARE, "allegro-2") ^ alone(BOX, "allegro-2")


@pytest.mark.os_agnostic
def test_dmx_600_transparent_attribute_keeps_both_fields_black() -> None:
    label = dots_label(SQUARE, BOX, attribute=Attribute.TRANSPARENT)
    assert b"\rA2\r" in build_job(label, RenderOptions(model="dmx-600")).data
    assert drawn(label, "dmx-600") == alone(SQUARE, "dmx-600") | alone(BOX, "dmx-600")


@pytest.mark.os_agnostic
def test_dmx_600_without_an_attribute_shows_the_format_default_xor() -> None:
    """No A command is sent; the manual makes XOR each format's default (the panel may differ)."""
    label = dots_label(SQUARE, BOX)
    assert not ATTRIBUTE_LINE.search(build_job(label, RenderOptions(model="dmx-600")).data)
    assert drawn(label, "dmx-600") == alone(SQUARE, "dmx-600") ^ alone(BOX, "dmx-600")


@pytest.mark.os_agnostic
def test_dmx_600_opaque_attribute_lets_the_later_field_whiten_what_it_covers() -> None:
    label = dots_label(SQUARE, BOX, attribute=Attribute.OPAQUE)
    image, box = alone(SQUARE, "dmx-600"), alone(BOX, "dmx-600")
    assert drawn(label, "dmx-600") == (image - covered(box)) | box


@pytest.mark.os_agnostic
def test_a_text_fields_own_attribute_overrides_the_labels() -> None:
    text = Text(x=42, y=45, text="AB", font=2, attribute=Attribute.OPAQUE)
    image, letters = alone(SQUARE, "dmx-600"), alone(text, "dmx-600")
    assert letters & image
    assert drawn(dots_label(SQUARE, text), "dmx-600") == (image - covered(letters)) | letters


@pytest.mark.os_agnostic
def test_inverse_has_no_combine_mode_and_shows_as_xor() -> None:
    label = dots_label(SQUARE, BOX, attribute=Attribute.INVERSE)
    assert drawn(label, "dmx-600") == alone(SQUARE, "dmx-600") ^ alone(BOX, "dmx-600")


@pytest.mark.os_agnostic
def test_a_field_on_the_frame_keeps_the_frame() -> None:
    """The frame marks the label edge; it is not printed, so no field XORs it away."""
    label = dots_label(Line(x=0, y=0, x2=119, y2=0, thickness=1))
    assert black_set(framed(label, "dmx-600")) <= black_set(preview_bitmap(label, get_profile("dmx-600")))


#: Seconds 200 small fields may take; a normal run needs a small fraction of it.
LOOSE_CEILING_S = 5


@pytest.mark.os_agnostic
def test_two_hundred_small_fields_preview_quickly() -> None:
    """A layer per field must not make a busy label slow: a loose ceiling, not a benchmark."""
    elements: list[Element] = [Text(x=2 + 10 * (i % 10), y=2 + 5 * (i // 10), text="A1", font=1) for i in range(200)]
    label = Label(unit=Unit.MM, width=104, height=150, elements=elements)
    started = time.perf_counter()
    preview_bitmap(label, get_profile("dmx-600"))
    assert time.perf_counter() - started < LOOSE_CEILING_S


# ======================== a stored reference to an image the job downloads ========================

#: An image no placeholder grid draws by chance: a frame with one corner filled.
LOGO_ROWS = ["#" * 16] + ["#" + "#" * 4 + "." * 10 + "#"] * 4 + ["#" + "." * 14 + "#"] * 10 + ["#" * 16]


def picture_at(picture: Bitmap, canvas_point: tuple[int, int], *, size: int) -> set[tuple[int, int]]:
    """The black pixels of the ``size`` square whose lower-left corner is ``canvas_point``, from its top-left."""
    left, bottom = canvas_point
    top = bottom - size + 1
    return {(x - left, y - top) for x in range(left, left + size) for y in range(top, bottom + 1) if picture.get(x, y)}


def logo_label(stored_name: str = "LOGO") -> Label:
    """LOGO downloaded at (2, 2) mm and a stored-only reference to ``stored_name`` at (40, 2) mm."""
    downloaded = Image(x=2, y=2, name="LOGO", bitmap=LOGO_ROWS)
    return Label(unit=Unit.MM, width=80, height=30, elements=[downloaded, Image(x=40, y=2, name=stored_name)])


@pytest.mark.os_agnostic
@pytest.mark.parametrize("stored_first", [False, True], ids=["download-first", "stored-first"])
def test_a_stored_reference_to_a_name_the_job_downloads_draws_that_image(stored_first: bool) -> None:
    """The printer prints the downloaded LOGO at both places, so the preview draws its pixels at both."""
    label = logo_label()
    if stored_first:
        label = label.model_copy(update={"elements": list(reversed(label.elements))})
    profile = get_profile(MODEL)
    canvas = canvas_for(label, profile)
    picture = preview_bitmap(label, profile)
    expected = black_set(Bitmap.from_rows(LOGO_ROWS))
    assert picture_at(picture, canvas.point(2, 2), size=16) == expected
    assert picture_at(picture, canvas.point(40, 2), size=16) == expected


@pytest.mark.os_agnostic
def test_a_stored_name_no_element_downloads_still_draws_the_placeholder_grid() -> None:
    label = logo_label("OTHER")
    alone_label = Label(unit=Unit.MM, width=80, height=30, elements=[label.elements[1]])
    profile = get_profile(MODEL)
    point = canvas_for(label, profile).point(40, 2)
    grid = picture_at(preview_bitmap(alone_label, profile), point, size=40)
    assert grid
    assert picture_at(preview_bitmap(label, profile), point, size=40) == grid


@pytest.mark.os_agnostic
def test_a_label_without_height_is_as_tall_as_the_downloaded_image_a_stored_reference_prints() -> None:
    """The reference prints LOGO's 16 rows, not the 40-dot placeholder, so the canvas ends where LOGO would."""
    downloaded = Image(x=2, y=2, name="LOGO", bitmap=LOGO_ROWS)
    stored = Image(x=40, y=20, name="LOGO")
    label = Label(unit=Unit.MM, width=80, elements=[downloaded, stored])
    twin = label.model_copy(update={"elements": [downloaded, stored.model_copy(update={"bitmap": LOGO_ROWS})]})
    profile = get_profile(MODEL)
    assert canvas_for(label, profile).height == canvas_for(twin, profile).height


# ======================== text combined by its glyph pixels ========================

#: A two-letter text field and a 4-dot line crossing it beyond both ends, in dots on a 120-dot label.
MASKED_TEXT = Text(x=20, y=40, text="AB", font=2)
CROSSING = Line(x=10, y=45, x2=100, y2=45, thickness=4)
MASKED_BARCODE = Barcode(x=10, y=30, symbology=Symbology.CODE128, data="AB", height=40)


def stripes(width: int, height: int) -> Bitmap:
    """A mask with every third column black: glyph-like dots that leave most of the field white."""
    return Bitmap.from_rows(["".join("#" if x % 3 == 0 else "." for x in range(width))] * height)


def mask_for(element: Text | Barcode, label: Label, model: str) -> tuple[Bitmap, set[tuple[int, int]]]:
    """A stripe mask the size of the area a mask covers for ``element``, and where its dots land."""
    left, top, width, height = mask_area(element, canvas_for(label, get_profile(model))).box()
    mask = stripes(width, height)
    return mask, {(left + x, top + y) for x, y in black_set(mask)}


def drawn_masked(label: Label, model: str, masks: dict[int, Bitmap]) -> set[tuple[int, int]]:
    picture = preview_bitmap(label, get_profile(model), masks=masks)
    return black_set(picture) - black_set(framed(label, model))


@pytest.mark.os_agnostic
def test_without_masks_a_line_through_text_xors_the_whole_character_cells() -> None:
    label = dots_label(MASKED_TEXT, CROSSING)
    assert drawn(label, "allegro-2") == alone(MASKED_TEXT, "allegro-2") ^ alone(CROSSING, "allegro-2")


@pytest.mark.os_agnostic
def test_a_text_mask_takes_the_place_of_the_cells_so_a_line_gaps_only_under_its_dots() -> None:
    label = dots_label(MASKED_TEXT, CROSSING)
    mask, dots = mask_for(MASKED_TEXT, label, "allegro-2")
    line = alone(CROSSING, "allegro-2")
    assert dots & line
    assert line - covered(dots)
    assert drawn_masked(label, "allegro-2", {0: mask}) == line ^ dots


@pytest.mark.os_agnostic
def test_a_barcode_mask_letters_the_top_band_over_the_hatching() -> None:
    label = dots_label(MASKED_BARCODE)
    mask, dots = mask_for(MASKED_BARCODE, label, "allegro-2")
    hatched = alone(MASKED_BARCODE, "allegro-2")
    left, top, width, height = bounds(hatched)
    # The top quarter of the 40-dot bars, read off the drawn hatching, not off mask_area.
    band = {(x, y) for x in range(left, left + width) for y in range(top, top + height // 4)}
    assert (width, height) == (44, 40)
    assert {(left + x, top + y) for x, y in black_set(mask)} == dots
    assert drawn_masked(label, "allegro-2", {0: mask}) == (hatched - band) | dots


@pytest.mark.os_agnostic
def test_a_mask_of_another_size_than_its_field_is_refused() -> None:
    label = dots_label(MASKED_TEXT)
    with pytest.raises(ValueError, match="mask for element 0 is 3x2"):
        preview_bitmap(label, get_profile("allegro-2"), masks={0: stripes(3, 2)})


@pytest.mark.os_agnostic
def test_a_mask_for_an_element_that_is_neither_text_nor_barcode_is_refused() -> None:
    label = dots_label(MASKED_TEXT, CROSSING)
    with pytest.raises(ValueError, match="element 1 is not a text field or barcode"):
        preview_bitmap(label, get_profile("allegro-2"), masks={1: stripes(3, 2)})
