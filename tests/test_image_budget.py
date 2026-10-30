"""The image memory budget: what a job's image downloads cost, and what happens past the budget.

Every case reads the job's own bytes: a download is decoded the way the printer reads it, and a
placing record field by field, so the tests hold whatever route the render takes to them.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import pytest
from lib_layered_config import Config

from datamax_dpl.adapters import cli as cli_mod
from datamax_dpl.adapters.cli.settings import PrinterOverrides, printer_settings, render_options
from datamax_dpl.domain.enums import FallbackPolicy, FindingCode, QrError, Severity
from datamax_dpl.domain.errors import ConfigurationError
from datamax_dpl.domain.image_budget import halve, image_data_bytes, magnify
from datamax_dpl.domain.image_records import decode_image, encode_image
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.preview import SentAs, canvas_for, preview_bitmap
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.qr import qr_bitmap
from datamax_dpl.domain.raster import Bitmap
from datamax_dpl.domain.render import RenderResult, build_job, effective_calibration
from datamax_dpl.domain.render_context import RenderOptions, drawn_image_name

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner

STX = b"\x02"
#: The Allegro 2's default budget: 48631 free bytes of module A less a 4096-byte margin.
ALLEGRO_2_BUDGET = 44535
#: 160 x 64 dots: 20 bytes a row, 1280 bytes; halved 80 x 32, 320 bytes.
USER_ROWS = ["#" * 160] + ["#" + "." * 158 + "#"] * 62 + ["#" * 160]
#: Allegro 2 has no circle record, so this circle is drawn as an image (3381 bytes; halved 891).
CIRCLE = {"type": "circle", "x": 30, "y": 30, "radius": 10}
USER_IMAGE = {"type": "image", "x": 50, "y": 5, "name": "LOGO", "bitmap": USER_ROWS}


def black_set(bitmap: Bitmap) -> set[tuple[int, int]]:
    return {(x, y) for y in range(bitmap.height) for x in range(bitmap.width) if bitmap.get(x, y)}


def label(*elements: dict[str, Any]) -> Label:
    return Label.model_validate({"elements": list(elements)})


def allegro(budget: int | None = None, *, policy: FallbackPolicy = FallbackPolicy.DEGRADE) -> RenderOptions:
    return RenderOptions(model="allegro-2", image_memory_bytes=budget, policy=policy)


def downloaded(data: bytes, name: str) -> Bitmap:
    """Decode the image the job downloads under ``name``, bottom row first as the printer reads it."""
    head = STX + b"IAAF" + name.encode("ascii") + b"\r"
    start = data.index(head) + len(head)
    # A black row's hex holds FFFF too: the end record is the one standing between two <CR>.
    end = data.index(b"\rFFFF\r", start - 1) + len(b"\rFFFF\r")
    return decode_image(data[start:end])


def placements(data: bytes) -> list[bytes]:
    """The image records of the format, ``1Y`` onward."""
    return [line for line in data[data.index(STX + b"L\r") :].split(b"\r") if line.startswith(b"1Y")]


def codes(result: RenderResult) -> list[FindingCode]:
    return [finding.code for finding in result.findings]


def only(result: RenderResult, code: FindingCode) -> Any:
    [finding] = [f for f in result.findings if f.code is code]
    return finding


def trimmed(bitmap: Bitmap, width: int) -> Bitmap:
    """The decoded image cut back to ``width`` columns (the decoder pads to whole bytes)."""
    return Bitmap.from_rows(
        ["".join("#" if bitmap.get(x, y) else "." for x in range(width)) for y in range(bitmap.height)]
    )


# ======================== halving and magnifying a bitmap ========================


@pytest.mark.os_agnostic
def test_halve_ors_each_2x2_block_so_a_one_dot_line_survives() -> None:
    rows = ["." * 8] * 8
    rows[3] = "#" * 8
    horizontal = halve(Bitmap.from_rows(rows))
    vertical = halve(Bitmap.from_rows(["...#...."] * 8))
    assert (horizontal.width, horizontal.height) == (4, 4)
    assert black_set(horizontal) == {(x, 1) for x in range(4)}
    assert black_set(vertical) == {(1, y) for y in range(4)}


@pytest.mark.os_agnostic
def test_halve_keeps_the_lower_left_corner_where_the_printer_anchors_the_image() -> None:
    """3 x 3 with one dot at the lower left: the blocks start at the bottom row and the left column."""
    halved = halve(Bitmap.from_rows(["...", "...", "#.."]))
    assert (halved.width, halved.height) == (2, 2)
    assert black_set(halved) == {(0, 1)}
    top_right = halve(Bitmap.from_rows(["..#", "...", "..."]))
    assert black_set(top_right) == {(1, 0)}


@pytest.mark.os_agnostic
def test_halve_of_a_single_dot_is_one_dot() -> None:
    assert black_set(halve(Bitmap.from_rows(["#"]))) == {(0, 0)}


@pytest.mark.os_agnostic
def test_magnify_prints_each_dot_as_a_square() -> None:
    doubled = magnify(Bitmap.from_rows(["#.", ".#"]), 2)
    assert (doubled.width, doubled.height) == (4, 4)
    assert black_set(doubled) == {(0, 0), (1, 0), (0, 1), (1, 1), (2, 2), (3, 2), (2, 3), (3, 3)}


# ======================== what a download costs ========================


@pytest.mark.os_agnostic
def test_image_data_bytes_counts_the_dot_row_bytes_the_records_carry() -> None:
    """10 x 3 dots pack into 2 bytes a row; the hex text and the record framing are not counted."""
    assert image_data_bytes(encode_image(Bitmap(10, 3))) == 6


@pytest.mark.os_agnostic
def test_image_data_bytes_counts_the_rows_a_repeat_record_adds() -> None:
    assert image_data_bytes(b"800190\r0000FF02\rFFFF\r") == 3


@pytest.mark.os_agnostic
def test_the_result_reports_the_bytes_of_the_images_it_downloads() -> None:
    result = build_job(label(USER_IMAGE), RenderOptions(model="dmx-600"))
    assert result.image_bytes == 1280


@pytest.mark.os_agnostic
def test_a_stored_image_the_job_only_places_costs_nothing() -> None:
    stored = {"type": "image", "x": 5, "y": 5, "name": "LOGO"}
    result = build_job(label(stored), allegro(1))
    assert result.image_bytes == 0
    assert result.ok, result.findings


@pytest.mark.os_agnostic
@pytest.mark.parametrize("stored_first", [True, False])
def test_a_stored_only_reference_to_a_name_the_job_downloads_need_not_be_on_the_printer(stored_first: bool) -> None:
    """The job itself downloads LOGO, whichever element comes first, so nothing must be stored beforehand."""
    stored = {"type": "image", "x": 5, "y": 5, "name": "LOGO"}
    elements = (stored, USER_IMAGE) if stored_first else (USER_IMAGE, stored)
    result = build_job(label(*elements), RenderOptions(model="dmx-600"))
    assert FindingCode.IMAGE_MUST_BE_STORED not in codes(result)
    assert result.downloads == ("LOGO",)


@pytest.mark.os_agnostic
def test_a_stored_only_image_no_element_downloads_must_be_on_the_printer() -> None:
    stored = {"type": "image", "x": 5, "y": 5, "name": "LOGO"}
    other = {**USER_IMAGE, "name": "OTHER"}
    finding = only(build_job(label(stored, other), RenderOptions(model="dmx-600")), FindingCode.IMAGE_MUST_BE_STORED)
    assert (finding.element, finding.message) == (0, "image LOGO must already be on the printer")


# ======================== the budget and where it comes from ========================


@pytest.mark.os_agnostic
def test_the_allegro_2_ships_a_budget_and_other_models_none() -> None:
    assert effective_calibration(RenderOptions(model="allegro-2")).image_memory_bytes == ALLEGRO_2_BUDGET
    assert effective_calibration(RenderOptions(model="dmx-600")).image_memory_bytes is None


@pytest.mark.os_agnostic
def test_a_budget_in_the_options_wins_over_the_models() -> None:
    assert effective_calibration(allegro(5000)).image_memory_bytes == 5000
    assert effective_calibration(RenderOptions(model="dmx-600", image_memory_bytes=700)).image_memory_bytes == 700


@pytest.mark.os_agnostic
def test_a_configured_budget_reaches_the_render_options() -> None:
    settings = printer_settings(cli_mod_config({"printer": {"image_memory_bytes": 1500}}), PrinterOverrides())
    assert render_options(settings).image_memory_bytes == 1500


@pytest.mark.os_agnostic
def test_a_budget_of_zero_is_refused_with_the_key_named() -> None:
    with pytest.raises(ConfigurationError, match=r"^printer\.image_memory_bytes: Input should be greater than 0$"):
        printer_settings(cli_mod_config({"printer": {"image_memory_bytes": 0}}), PrinterOverrides())


def cli_mod_config(data: dict[str, Any]) -> Config:
    return Config(data, {})


# ======================== within budget ========================


@pytest.mark.os_agnostic
def test_a_job_within_the_budget_is_left_as_it_is() -> None:
    within = build_job(label(CIRCLE, USER_IMAGE), allegro(10_000))
    unlimited = build_job(label(CIRCLE, USER_IMAGE), RenderOptions(model="allegro-2", image_memory_bytes=10**9))
    assert within.data == unlimited.data
    assert FindingCode.IMAGE_HALVED not in codes(within)
    assert FindingCode.IMAGE_MEMORY_EXCEEDED not in codes(within)
    assert within.halved_elements == ()


@pytest.mark.os_agnostic
def test_a_job_whose_images_fill_the_budget_exactly_is_within_it() -> None:
    result = build_job(label(USER_IMAGE), RenderOptions(model="dmx-600", image_memory_bytes=1280))
    assert result.halved_elements == ()
    assert result.ok, result.findings


@pytest.mark.os_agnostic
def test_a_model_without_a_budget_never_halves() -> None:
    big = {"type": "image", "x": 0, "y": 0, "name": "BIG", "bitmap": ["#" * 800] * 600}
    result = build_job(label(big), RenderOptions(model="dmx-600"))
    assert result.image_bytes == 100 * 600
    assert result.halved_elements == ()


# ======================== over budget under degrade ========================


def circle_frame() -> tuple[int, int]:
    """The circle's drawn width and height in dots, as the unhalved job reports them."""
    full = build_job(label(CIRCLE), allegro(10**9))
    fallback = only(full, FindingCode.CIRCLE_RASTERIZED).fallback
    match = re.search(r"\((\d+)x(\d+) dots\)", fallback or "")
    assert match is not None, fallback
    return int(match.group(1)), int(match.group(2))


def packed_bytes(width: int, height: int) -> int:
    return -(-width // 8) * height


@pytest.mark.os_agnostic
def test_over_budget_every_image_is_halved_and_placed_at_double_magnification() -> None:
    full = build_job(label(CIRCLE, USER_IMAGE), allegro(10**9))
    result = build_job(label(CIRCLE, USER_IMAGE), allegro(1500))
    width, height = circle_frame()

    assert result.ok, result.findings
    assert result.halved_elements == (0, 1)
    halved_circle = halve(trimmed(downloaded(full.data, full.downloads[0]), width))
    assert (halved_circle.width, halved_circle.height) == (-(-width // 2), -(-height // 2))
    assert result.downloads[0] == drawn_image_name(halved_circle)
    sent_circle = trimmed(downloaded(result.data, result.downloads[0]), halved_circle.width)
    assert black_set(sent_circle) == black_set(halved_circle)
    halved_logo = halve(Bitmap.from_rows(USER_ROWS))
    assert result.downloads[1] == drawn_image_name(halved_logo)
    assert black_set(trimmed(downloaded(result.data, result.downloads[1]), 80)) == black_set(halved_logo)
    assert all(record.startswith(b"1Y22000") for record in placements(result.data))
    assert result.image_bytes == packed_bytes(halved_circle.width, halved_circle.height) + 320


@pytest.mark.os_agnostic
def test_halving_is_reported_once_listing_the_elements() -> None:
    full = build_job(label(CIRCLE, USER_IMAGE), allegro(10**9))
    result = build_job(label(CIRCLE, USER_IMAGE), allegro(1500))
    finding = only(result, FindingCode.IMAGE_HALVED)
    assert (finding.severity, finding.element) == (Severity.WARNING, None)
    assert f"hold {full.image_bytes} bytes" in finding.message
    assert "budget is 1500 bytes" in finding.message
    assert finding.fallback is not None
    assert "elements 0, 1" in finding.fallback
    assert f"{result.image_bytes} bytes" in finding.fallback
    assert "unverified on paper" in finding.fallback


@pytest.mark.os_agnostic
def test_the_halved_circle_is_named_by_its_own_content() -> None:
    full = build_job(label(CIRCLE), allegro(10**9))
    halved = build_job(label(CIRCLE), allegro(full.image_bytes - 1))
    assert halved.halved_elements == (0,)
    assert halved.downloads != full.downloads


@pytest.mark.os_agnostic
def test_the_allegro_2_default_budget_halves_a_job_past_it_and_names_where_it_comes_from() -> None:
    """800 x 450 dots: 100 bytes a row, 45000 bytes, past the 44535-byte default."""
    big = {"type": "image", "x": 0, "y": 0, "name": "BIG", "bitmap": ["#" * 800] * 450}
    result = build_job(label(big), RenderOptions(model="allegro-2"))
    finding = only(result, FindingCode.IMAGE_HALVED)
    assert f"budget is {ALLEGRO_2_BUDGET} bytes" in finding.message
    assert "48631 free bytes" in finding.message
    assert result.halved_elements == (0,)
    assert result.ok, result.findings


@pytest.mark.os_agnostic
def test_a_configured_budget_is_named_as_configured() -> None:
    finding = only(build_job(label(USER_IMAGE), allegro(500)), FindingCode.IMAGE_HALVED)
    assert "the configured image memory budget is 500 bytes" in finding.message


@pytest.mark.os_agnostic
def test_a_job_still_over_the_budget_after_one_halving_is_refused_and_not_halved_twice() -> None:
    once = build_job(label(CIRCLE, USER_IMAGE), allegro(1500)).image_bytes
    result = build_job(label(CIRCLE, USER_IMAGE), allegro(once - 1))
    finding = only(result, FindingCode.IMAGE_MEMORY_EXCEEDED)
    assert (finding.severity, finding.element) == (Severity.ERROR, None)
    assert f"hold {once} bytes after halving" in finding.message
    assert all(record.startswith(b"1Y22000") for record in placements(result.data))
    assert result.image_bytes == once


@pytest.mark.os_agnostic
def test_an_image_whose_doubled_magnification_passes_9_is_refused_instead_of_halved() -> None:
    magnified = {**USER_IMAGE, "magnification": 5}
    result = build_job(label(magnified), allegro(500))
    [finding] = [f for f in result.findings if f.code is FindingCode.IMAGE_MEMORY_EXCEEDED and f.element == 0]
    assert finding.severity is Severity.ERROR
    assert "magnification 5; halving it would need 10" in finding.message
    assert FindingCode.IMAGE_HALVED not in codes(result)
    assert placements(result.data)[0].startswith(b"1Y55000")


@pytest.mark.os_agnostic
def test_an_image_at_magnification_4_is_halved_to_8() -> None:
    """Placed at x = 20 mm (dot 160), where the halved 80 dots at magnification 8 end at dot 800 of 832."""
    result = build_job(label({**USER_IMAGE, "x": 20, "magnification": 4}), allegro(500))
    assert result.ok, result.findings
    assert placements(result.data)[0].startswith(b"1Y88000")


# ======================== QR Codes are never halved ========================


@pytest.mark.os_agnostic
def test_a_qr_code_is_never_halved_and_counts_against_the_budget() -> None:
    qr = {"type": "qr", "x": 5, "y": 5, "data": "HELLO", "error": "Q", "module_dots": 4}
    result = build_job(label(qr, USER_IMAGE), allegro(500))
    symbol = qr_bitmap("HELLO", error=QrError.Q)
    assert result.ok, result.findings
    assert result.halved_elements == (1,)
    assert result.downloads[0] == drawn_image_name(symbol)
    assert placements(result.data)[0].startswith(b"1Y44000")
    assert result.image_bytes == 63 + 320


@pytest.mark.os_agnostic
def test_a_job_over_the_budget_because_of_qr_codes_is_refused() -> None:
    qr = {"type": "qr", "x": 5, "y": 5, "data": "HELLO", "error": "Q", "module_dots": 2}
    result = build_job(label(qr), allegro(50))
    assert FindingCode.IMAGE_MEMORY_EXCEEDED in codes(result)
    assert not result.ok
    assert placements(result.data)[0].startswith(b"1Y22000")


# ======================== strict ========================


@pytest.mark.os_agnostic
def test_under_strict_a_job_over_the_budget_is_refused_without_halving() -> None:
    full = build_job(label(CIRCLE, USER_IMAGE), allegro(10**9, policy=FallbackPolicy.STRICT))
    result = build_job(label(CIRCLE, USER_IMAGE), allegro(1500, policy=FallbackPolicy.STRICT))
    finding = only(result, FindingCode.IMAGE_MEMORY_EXCEEDED)
    assert finding.severity is Severity.ERROR
    assert f"hold {full.image_bytes} bytes" in finding.message
    assert result.data == full.data
    assert FindingCode.IMAGE_HALVED not in codes(result)
    assert result.halved_elements == ()


# ======================== user image magnification ========================


@pytest.mark.os_agnostic
def test_an_image_element_places_its_magnification() -> None:
    stored = {"type": "image", "x": 5, "y": 5, "name": "LOGO", "magnification": 3}
    assert placements(build_job(label(stored), RenderOptions(model="dmx-600")).data)[0].startswith(b"1Y33000")


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("value", "rule"), [(0, "greater than or equal to 1"), (10, "less than or equal to 9")])
def test_an_image_magnification_outside_1_to_9_is_refused(value: int, rule: str) -> None:
    with pytest.raises(ValueError, match=rule):
        label({"type": "image", "x": 5, "y": 5, "name": "LOGO", "magnification": value})


# ======================== the preview ========================


def drawn_near(picture: Bitmap, *, left: int, bottom: int, size: int) -> set[tuple[int, int]]:
    """The black pixels of the ``size`` square whose lower-left corner is (left, bottom), from its top-left."""
    top = bottom - size + 1
    return {(x - left, y - top) for x in range(left, left + size) for y in range(top, bottom + 1) if picture.get(x, y)}


@pytest.mark.os_agnostic
def test_the_preview_draws_a_halved_image_at_half_resolution_twice_as_large() -> None:
    small = ["#.#.", "....", "#...", "...."]
    image = {"type": "image", "x": 2, "y": 2, "name": "LOGO", "bitmap": small}
    document = Label.model_validate({"width": 10, "height": 10, "elements": [image]})
    profile = get_profile("allegro-2")
    left, bottom = canvas_for(document, profile).point(2, 2)
    picture = preview_bitmap(document, profile, sent_as=SentAs(halved=frozenset({0})))
    expected = magnify(halve(Bitmap.from_rows(small)), 2)
    assert drawn_near(picture, left=left, bottom=bottom, size=4) == black_set(expected)
    plain = preview_bitmap(document, profile)
    assert drawn_near(plain, left=left, bottom=bottom, size=4) == black_set(Bitmap.from_rows(small))


@pytest.mark.os_agnostic
def test_the_preview_draws_an_image_at_its_magnification() -> None:
    image = {"type": "image", "x": 2, "y": 2, "name": "LOGO", "bitmap": ["#.", ".."], "magnification": 3}
    document = Label.model_validate({"width": 10, "height": 10, "elements": [image]})
    profile = get_profile("allegro-2")
    left, bottom = canvas_for(document, profile).point(2, 2)
    picture = preview_bitmap(document, profile)
    assert drawn_near(picture, left=left, bottom=bottom, size=6) == {(x, y) for x in range(3) for y in range(3)}


@pytest.mark.os_agnostic
def test_the_preview_draws_a_halved_circle_in_2x2_blocks() -> None:
    document = Label.model_validate({"width": 60, "height": 60, "elements": [CIRCLE]})
    profile = get_profile("allegro-2")
    plain = preview_bitmap(document, profile)
    halved = preview_bitmap(document, profile, sent_as=SentAs(halved=frozenset({0})))
    assert black_set(plain) < black_set(halved)


@pytest.mark.os_agnostic
def test_the_preview_command_draws_what_the_budget_prints(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    source = tmp_path / "label.json"
    source.write_text(json.dumps({"width": 100, "height": 30, "elements": [USER_IMAGE]}), encoding="utf-8")

    def preview(config: dict[str, Any], name: str) -> bytes:
        output = tmp_path / name
        args = ["preview", str(source), "-o", str(output)]
        result = cli_runner.invoke(cli_mod.cli, args, obj=config_cli_context(config))
        assert result.exit_code == 0, result.output
        return output.read_bytes()

    roomy = preview({"printer": {"model": "allegro-2"}}, "roomy.png")
    tight = preview({"printer": {"model": "allegro-2", "image_memory_bytes": 500}}, "tight.png")
    same = preview({"printer": {"model": "allegro-2", "image_memory_bytes": 1280}}, "same.png")
    assert tight != roomy
    assert same == roomy


# ======================== through the CLI ========================


@pytest.mark.os_agnostic
def test_a_configured_budget_wins_over_the_models_through_render(
    cli_runner: CliRunner, config_cli_context: Callable[[dict[str, Any]], Callable[[], Any]], tmp_path: Path
) -> None:
    source = tmp_path / "label.json"
    source.write_text(json.dumps({"elements": [CIRCLE, USER_IMAGE]}), encoding="utf-8")
    args = ["--json", "render", str(source), "--model", "allegro-2"]

    default = cli_runner.invoke(cli_mod.cli, args, obj=config_cli_context({}))
    tight = cli_runner.invoke(cli_mod.cli, args, obj=config_cli_context({"printer": {"image_memory_bytes": 1500}}))

    assert default.exit_code == 0, default.output
    assert tight.exit_code == 0, tight.output
    default_codes = [f["code"] for f in json.loads(default.stdout)["data"]["findings"]]
    tight_codes = [f["code"] for f in json.loads(tight.stdout)["data"]["findings"]]
    assert "image_halved" not in default_codes
    assert "image_halved" in tight_codes


# ======================== a halved user image never takes the user's name ========================

#: 64 x 40 dots: 8 bytes a row, 320 bytes; halved 32 x 20, 80 bytes.
LOGO_ROWS = ["#" * 64] * 40
LOGO_WITH_BITMAP = {"type": "image", "x": 5, "y": 5, "name": "LOGO", "bitmap": LOGO_ROWS}
LOGO_STORED = {"type": "image", "x": 40, "y": 5, "name": "LOGO"}


def dmx(budget: int | None) -> RenderOptions:
    return RenderOptions(model="dmx-600", image_memory_bytes=budget)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("bitmap_first", [True, False], ids=["bitmap-first", "stored-first"])
def test_a_halved_user_image_and_a_stored_reference_to_it_print_alike(bitmap_first: bool) -> None:
    """The name LOGO keeps meaning the full bitmap: the halved bits go under their content name,
    and the stored-only reference in the same job is placed as that halved image."""
    elements = [LOGO_WITH_BITMAP, LOGO_STORED] if bitmap_first else [LOGO_STORED, LOGO_WITH_BITMAP]
    result = build_job(label(*elements), dmx(100))
    halved = halve(Bitmap.from_rows(LOGO_ROWS))

    assert result.ok, result.findings
    assert result.downloads == (drawn_image_name(halved),)
    assert "LOGO" not in result.downloads
    assert re.search(rb"\x02I[A-E]AFLOGO\r", result.data) is None
    records = placements(result.data)
    assert len(records) == 2
    assert all(
        record.startswith(b"1Y22000") and record.endswith(drawn_image_name(halved).encode()) for record in records
    )
    assert result.halved_elements == (0, 1)


@pytest.mark.os_agnostic
def test_within_the_budget_the_user_image_keeps_its_name() -> None:
    result = build_job(label(LOGO_WITH_BITMAP, LOGO_STORED), dmx(10_000))
    assert result.downloads == ("LOGO",)
    assert all(record.startswith(b"1Y11000") and record.endswith(b"LOGO") for record in placements(result.data))


@pytest.mark.os_agnostic
def test_a_stored_reference_keeps_its_own_magnification_doubled() -> None:
    stored = {**LOGO_STORED, "magnification": 2}
    result = build_job(label(LOGO_WITH_BITMAP, stored), dmx(100))
    assert [record[:4] for record in placements(result.data)] == [b"1Y22", b"1Y44"]


@pytest.mark.os_agnostic
def test_a_stored_image_this_job_does_not_download_is_left_alone_when_halving() -> None:
    other = {"type": "image", "x": 40, "y": 5, "name": "OTHER"}
    result = build_job(label(LOGO_WITH_BITMAP, other), dmx(100))
    assert placements(result.data)[1].startswith(b"1Y11000")
    assert placements(result.data)[1].endswith(b"OTHER")
    assert result.halved_elements == (0,)


@pytest.mark.os_agnostic
def test_a_halved_raster_fallback_reports_the_size_and_magnification_it_is_sent_at() -> None:
    width, height = circle_frame()
    full = build_job(label(CIRCLE), allegro(10**9))
    finding = only(build_job(label(CIRCLE), allegro(full.image_bytes - 1)), FindingCode.CIRCLE_RASTERIZED)
    assert finding.fallback is not None
    assert f"({-(-width // 2)}x{-(-height // 2)} dots, placed at magnification 2)" in finding.fallback
