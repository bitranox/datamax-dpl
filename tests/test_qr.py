"""The ``qr`` element: segno's module matrix, downloaded once at one dot per module, placed magnified.

The bytes are read back the way the printer reads them: the download through ``decode_image`` (the
first row record is the BOTTOM row, measured on an Allegro 2) and the placing record field by field.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import pytest
import segno
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory import RecordingPrinter
from datamax_dpl.application.use_cases import render_batch
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.decoder import explain
from datamax_dpl.domain.enums import CommandKind, FallbackPolicy, FindingCode, QrError, Severity, Unit
from datamax_dpl.domain.errors import LabelRejectedError
from datamax_dpl.domain.findings import FindingCollector
from datamax_dpl.domain.image_records import decode_image
from datamax_dpl.domain.label import Image, Label, Qr
from datamax_dpl.domain.placeholders import fill_label, label_placeholders
from datamax_dpl.domain.preview import preview_bitmap
from datamax_dpl.domain.profiles import get_profile, list_profiles
from datamax_dpl.domain.qr import qr_bitmap
from datamax_dpl.domain.raster import Bitmap
from datamax_dpl.domain.records import image_record
from datamax_dpl.domain.render import RenderResult, build_job, render
from datamax_dpl.domain.render_context import RenderContext, RenderOptions, drawn_image_name
from datamax_dpl.domain.units import DplUnit

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner, Result

STX = b"\x02"
PRINTER = "tcp://192.0.2.60:9100"
QR_DOCUMENT = {"elements": [{"type": "qr", "x": 5, "y": 5, "data": "HELLO", "error": "Q", "module_dots": 4}]}
END_RECORD = b"FFFF"


def segno_bitmap(data: str, error: str) -> Bitmap:
    """segno's own matrix for ``data`` (no border), top row first, as a bitmap."""
    matrix = segno.make(data, error=error, micro=False, boost_error=False).matrix
    return Bitmap.from_rows(["".join("#" if module else "." for module in row) for row in matrix])


def black_set(bitmap: Bitmap) -> set[tuple[int, int]]:
    return {(x, y) for y in range(bitmap.height) for x in range(bitmap.width) if bitmap.get(x, y)}


def download_records(data: bytes, name: str) -> list[bytes]:
    """The image records sent after ``<STX>IAAF<name><CR>``, up to and including ``FFFF``."""
    head = STX + b"IAAF" + name.encode("ascii") + b"\r"
    records: list[bytes] = []
    for record in data[data.index(head) + len(head) :].split(b"\r"):
        records.append(record)
        if record == END_RECORD:
            return records
    raise AssertionError(f"download {name} has no end record")


def image_records_of(data: bytes) -> list[bytes]:
    start = data.index(STX + b"L\r")
    return [line for line in data[start:].split(b"\r") if line.startswith(b"1Y")]


def qr_label(**fields: object) -> Label:
    return Label.model_validate({"elements": [{"type": "qr", "x": 5, "y": 5, "data": "HELLO", **fields}]})


def dots_label(**fields: object) -> Label:
    return Label.model_validate(
        {"unit": "dots", "elements": [{"type": "qr", "x": 0, "y": 0, "data": "HELLO", **fields}]}
    )


def codes(result: RenderResult) -> set[FindingCode]:
    return {finding.code for finding in result.findings}


# ======================== the module matrix ========================


@pytest.mark.os_agnostic
def test_qr_bitmap_is_segnos_matrix_row_for_row_without_a_border() -> None:
    bitmap = qr_bitmap("HELLO", error=QrError.M)
    expected = segno_bitmap("HELLO", "m")
    assert (bitmap.width, bitmap.height) == (21, 21)
    assert bitmap.rows() == expected.rows()


@pytest.mark.os_agnostic
def test_qr_bitmap_keeps_the_requested_error_level_instead_of_boosting_it() -> None:
    """segno raises the level by default when the symbol has room; the caller's level is kept."""
    low = qr_bitmap("HELLO", error=QrError.L)
    assert low.rows() == segno_bitmap("HELLO", "l").rows()
    boosted = segno.make("HELLO", error="l", micro=False).matrix
    assert low.rows() != Bitmap.from_rows(["".join("#" if m else "." for m in row) for row in boosted]).rows()


@pytest.mark.os_agnostic
def test_a_qr_never_becomes_a_micro_qr() -> None:
    """A single digit fits a Micro QR (11 modules); the element always makes a full QR (21)."""
    assert qr_bitmap("1", error=QrError.L).width == 21


# ======================== download and placement ========================


@pytest.mark.os_agnostic
def test_a_qr_on_the_allegro_2_downloads_one_image_holding_the_matrix() -> None:
    result = build_job(qr_label(), RenderOptions(model="allegro-2"))
    assert result.ok, result.findings
    assert len(result.downloads) == 1
    records = download_records(result.data, result.downloads[0])
    decoded = decode_image(b"\r".join(records) + b"\r")
    assert black_set(decoded) == black_set(segno_bitmap("HELLO", "m"))


@pytest.mark.os_agnostic
def test_the_first_row_record_is_the_matrix_bottom_row_with_the_bottom_left_finder() -> None:
    """The printer draws the first row record at the bottom; segno puts finders top-left, top-right, bottom-left."""
    matrix = segno.make("HELLO", error="m", micro=False, boost_error=False).matrix
    assert list(matrix[-1][:7]) == [1] * 7, "segno's bottom row starts with the bottom-left finder"
    assert list(matrix[-1][-7:]) != [1] * 7, "segno has no finder bottom-right"
    result = build_job(qr_label(), RenderOptions(model="allegro-2"))
    first = download_records(result.data, result.downloads[0])[0]
    assert first == b"8003" + segno_bitmap("HELLO", "m").rows()[-1].hex().upper().encode("ascii")
    decoded = decode_image(b"\r".join(download_records(result.data, result.downloads[0])) + b"\r")
    corners = [(0, 0), (20, 0), (0, 20)]
    assert all(decoded.get(x, y) for x, y in corners)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("module_dots", "digits"), [(1, b"11"), (3, b"33"), (9, b"99")])
def test_the_placing_record_magnifies_by_module_dots(module_dots: int, digits: bytes) -> None:
    result = build_job(qr_label(module_dots=module_dots), RenderOptions(model="allegro-2"))
    [record] = image_records_of(result.data)
    assert record.startswith(b"1Y" + digits + b"000")
    assert record.endswith(result.downloads[0].encode("ascii"))


@pytest.mark.os_agnostic
def test_without_a_quiet_zone_the_qr_sits_where_an_image_at_the_same_point_sits() -> None:
    qr = build_job(qr_label(quiet_zone=0), RenderOptions(model="dmx-600"))
    image = build_job(
        Label(elements=[Image(x=5, y=5, name="SAME", bitmap=["#"])]),
        RenderOptions(model="dmx-600"),
    )
    [qr_record] = image_records_of(qr.data)
    [image_record_] = image_records_of(image.data)
    assert qr_record[7:15] == image_record_[7:15]


@pytest.mark.os_agnostic
def test_the_quiet_zone_moves_the_symbol_in_by_quiet_zone_times_module_dots() -> None:
    """dmx-600 at 300 dpi; a label in dots is written in 1/100 in: 300 dots = 100, 150 dots = 50,
    and the zone of 4 modules x 3 dots = 12 dots = 4."""
    label = Label.model_validate({"unit": "dots", "elements": [{"type": "qr", "x": 300, "y": 150, "data": "HELLO"}]})
    result = build_job(label, RenderOptions(model="dmx-600"))
    [record] = image_records_of(result.data)
    assert record == b"1Y33000" + b"0054" + b"0104" + result.downloads[0].encode("ascii")


# ======================== refusals ========================


@pytest.mark.os_agnostic
def test_data_no_qr_version_holds_at_the_level_is_refused() -> None:
    label = qr_label(data="A" * 3000, error="H")
    result = build_job(label, RenderOptions(model="allegro-2"))
    assert FindingCode.QR_DATA_TOO_LONG in codes(result)
    assert result.downloads == ()
    with pytest.raises(LabelRejectedError, match="does not fit any QR Code version at error level H"):
        render(label, RenderOptions(model="allegro-2"))


@pytest.mark.os_agnostic
def test_the_same_data_fits_at_a_lower_level() -> None:
    assert build_job(qr_label(data="A" * 3000, error="L"), RenderOptions(model="allegro-2")).ok


@pytest.mark.os_agnostic
def test_a_qr_whose_right_edge_with_its_quiet_zone_passes_the_print_width_is_refused() -> None:
    """Allegro 2: 4.10 in at 203 dpi is 832 dots; HELLO is 21 modules, so (21 + 2*4) * 3 = 87 dots wide."""
    fits = build_job(dots_label(x=745), RenderOptions(model="allegro-2"))
    assert fits.ok, fits.findings
    past = build_job(dots_label(x=746), RenderOptions(model="allegro-2"))
    assert FindingCode.QR_PAST_PRINT_WIDTH in codes(past)
    with pytest.raises(LabelRejectedError, match="passes the 832-dot print width"):
        render(dots_label(x=746), RenderOptions(model="allegro-2"))


@pytest.mark.os_agnostic
def test_a_qr_pushed_off_the_bottom_by_the_models_default_offset_is_placed_where_no_offset_puts_it() -> None:
    """D10: the Allegro 2's default offset_along of -0.25 mm would move a QR at y=0 off the label."""
    clamped = build_job(qr_label(y=0), RenderOptions(model="allegro-2"))
    assert FindingCode.MODEL_OFFSET_CLAMPED in codes(clamped)
    unshifted = build_job(qr_label(y=0), RenderOptions(model="allegro-2", offset_along=0.0))
    assert image_records_of(clamped.data) == image_records_of(unshifted.data)


@pytest.mark.os_agnostic
def test_a_qr_pushed_off_the_bottom_by_a_caller_offset_is_refused() -> None:
    result = build_job(qr_label(y=0), RenderOptions(model="allegro-2", offset_along=-1.0))
    assert FindingCode.OFFSET_PAST_LABEL_EDGE in codes(result)
    assert not result.ok


@pytest.mark.os_agnostic
@pytest.mark.parametrize("field", ["module_dots", "quiet_zone"])
def test_module_dots_and_quiet_zone_have_bounds(field: str) -> None:
    with pytest.raises(ValidationError, match="less than or equal to"):
        qr_label(**{field: 11})


@pytest.mark.os_agnostic
def test_an_unknown_error_level_is_refused() -> None:
    with pytest.raises(ValidationError, match="Input should be 'L', 'M', 'Q' or 'H'"):
        qr_label(error="X")


# ======================== every model, placeholders ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize("model", [profile.id for profile in list_profiles()])
def test_every_model_prints_a_qr(model: str) -> None:
    """No model lacks image records, so none lacks the qr element."""
    result = build_job(qr_label(), RenderOptions(model=model))
    assert result.ok, result.findings
    assert len(result.downloads) == 1


@pytest.mark.os_agnostic
def test_a_placeholder_in_qr_data_is_listed_and_filled_from_a_record() -> None:
    label = qr_label(data="SKU {sku}")
    assert label_placeholders(label) == ["sku"]
    filled = fill_label(label, {"sku": "A-17"})
    [element] = filled.elements
    assert isinstance(element, Qr)
    assert element.data == "SKU A-17"


@pytest.mark.os_agnostic
@settings(max_examples=40, deadline=None)
@given(data=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=80))
def test_any_short_ascii_data_renders_one_download(data: str) -> None:
    result = build_job(qr_label(data=data), RenderOptions(model="generic"))
    assert result.ok, result.findings
    assert len(result.downloads) == 1


# ======================== the image record, explain, preview ========================


@pytest.mark.os_agnostic
def test_image_record_writes_the_magnification_as_its_width_and_height_digits() -> None:
    assert image_record(row=0, column=0, name="LOGO") == b"1Y1100000000000LOGO"
    assert image_record(row=12, column=34, name="QR", magnification=(3, 2)) == b"1Y3200000120034QR"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("magnification", [(0, 1), (1, 10), (10, 10)])
def test_image_record_refuses_a_magnification_outside_1_to_9(magnification: tuple[int, int]) -> None:
    with pytest.raises(ValueError, match="image magnification must be 1-9"):
        image_record(row=0, column=0, name="LOGO", magnification=magnification)


@pytest.mark.os_agnostic
def test_explain_shows_the_download_and_the_magnified_placement() -> None:
    result = build_job(qr_label(), RenderOptions(model="allegro-2"))
    explanation = explain(result.data)
    assert [c.kind for c in explanation.commands].count(CommandKind.DOWNLOAD) == 1
    assert any(c.meaning == "image at magnification 3" for c in explanation.commands)
    assert explanation.label is not None
    [element] = explanation.label.elements
    assert isinstance(element, Image)
    assert element.name == result.downloads[0]
    assert element.magnification == 3


@pytest.mark.os_agnostic
def test_the_preview_draws_the_modules_scaled_inside_the_quiet_zone() -> None:
    label = dots_label()
    profile = get_profile("allegro-2")
    bitmap = preview_bitmap(label, profile)
    matrix = qr_bitmap("HELLO", error=QrError.M)
    size = 21 * 3
    left = 4 * 3
    bottom = bitmap.height - 1 - 4 * 3
    top = bottom - size + 1
    drawn = {
        ((x - left) // 3, (y - top) // 3)
        for y in range(top, bottom + 1)
        for x in range(left, left + size)
        if bitmap.get(x, y)
    }
    assert drawn == black_set(matrix)
    assert not bitmap.get(left - 1, top)


# ======================== content-derived image names ========================

DRAWN_NAME = re.compile(r"D[0-9A-F]{7}")


@pytest.mark.os_agnostic
def test_a_drawn_image_name_is_d_and_seven_hex_digits_of_a_stable_hash() -> None:
    """Pinned literal: Python's hash() is salted per process, hashlib is not."""
    name = drawn_image_name(Bitmap.from_rows(["#."]))
    assert DRAWN_NAME.fullmatch(name)
    assert name == "DBBE51D5"


@pytest.mark.os_agnostic
def test_the_same_bits_at_another_width_get_another_name() -> None:
    """``#.`` and ``#...`` pack into the same byte; only the width tells them apart."""
    assert drawn_image_name(Bitmap.from_rows(["#."])) != drawn_image_name(Bitmap.from_rows(["#..."]))


@pytest.mark.os_agnostic
def test_a_batch_downloads_each_records_qr_under_its_own_name() -> None:
    """Printer memory keeps an image across jobs and refuses to reload a name it holds (manual,
    Appendix D, BELL): two records sharing a name would print the first record's symbol twice."""
    label = qr_label(data="{sku}")
    options = RenderOptions(model="allegro-2")
    different = render_batch(label, [{"sku": "A1"}, {"sku": "B2"}], options)
    first, second = (result.downloads for result in different.results)
    assert first != second
    same = render_batch(label, [{"sku": "A1"}, {"sku": "A1"}], options)
    assert same.results[0].downloads == same.results[1].downloads


@pytest.mark.os_agnostic
def test_identical_qrs_in_one_job_are_downloaded_once_and_placed_twice() -> None:
    label = Label.model_validate(
        {
            "elements": [
                {"type": "qr", "x": 5, "y": 5, "data": "SAME"},
                {"type": "qr", "x": 30, "y": 5, "data": "SAME"},
                {"type": "qr", "x": 5, "y": 30, "data": "OTHER"},
            ]
        }
    )
    result = build_job(label, RenderOptions(model="dmx-600"))
    same, other = result.downloads
    assert result.data.count(STX + b"IAAF" + same.encode("ascii") + b"\r") == 1
    assert [record.endswith(same.encode("ascii")) for record in image_records_of(result.data)] == [True, True, False]
    assert other != same


def drawn_name(ctx: RenderContext, bitmap: Bitmap) -> str:
    """Queue ``bitmap`` as a drawn image of element 0 and return the name it is placed under."""
    placed = ctx.download_drawn_image(bitmap, element=0)
    assert placed is not None
    return placed.name


@pytest.mark.os_agnostic
def test_two_different_images_whose_names_collide_in_one_job_get_different_names() -> None:
    """A hash collision is forced by queuing another image under the first candidate name."""
    ctx = RenderContext(
        profile=get_profile("generic"),
        firmware=None,
        options=frozenset(),
        unit=Unit.MM,
        dpl_unit=DplUnit.INCH_HUNDREDTHS,
        findings=FindingCollector(FallbackPolicy.DEGRADE),
    )
    bitmap = Bitmap.from_rows(["#."])
    taken = drawn_image_name(bitmap)
    ctx.download_image(taken, Bitmap.from_rows([".#"]), element=0)
    name = drawn_name(ctx, bitmap)
    assert name not in (taken, "")
    assert name == drawn_image_name(bitmap, salt=1)
    assert drawn_name(ctx, bitmap) == name
    assert [download.name for download in ctx.downloads] == [taken, name]


HELLO_NAME = "D869223B"


def _user_image(*, bitmap: list[str] | None) -> dict[str, object]:
    image: dict[str, object] = {"type": "image", "x": 40, "y": 40, "name": HELLO_NAME}
    if bitmap is not None:
        image["bitmap"] = bitmap
    return image


def _names_and_bits(result: RenderResult) -> dict[str, set[bytes]]:
    """Each name the job downloads, with the set of distinct download bodies sent under it."""
    sent: dict[str, set[bytes]] = {}
    for name in result.downloads:
        records = download_records(result.data, name)
        sent.setdefault(name, set()).add(b"\r".join(records))
    return sent


@pytest.mark.os_agnostic
def test_the_qr_content_name_is_the_one_the_user_image_tests_use() -> None:
    """Premise of the collision tests below: the HELLO symbol hashes to HELLO_NAME."""
    assert drawn_image_name(qr_bitmap("HELLO", error=QrError.M)) == HELLO_NAME


@pytest.mark.os_agnostic
@pytest.mark.parametrize("qr_first", [True, False])
def test_a_drawn_image_never_takes_the_name_of_a_user_image_with_a_bitmap(qr_first: bool) -> None:
    qr = {"type": "qr", "x": 5, "y": 5, "data": "HELLO"}
    image = _user_image(bitmap=["#.#.", ".#.#"])
    label = Label.model_validate({"elements": [qr, image] if qr_first else [image, qr]})
    result = build_job(label, RenderOptions(model="dmx-600"))
    assert result.ok, result.findings
    assert sorted(result.downloads) == sorted(
        [HELLO_NAME, drawn_image_name(qr_bitmap("HELLO", error=QrError.M), salt=1)]
    )
    assert all(len(bits) == 1 for bits in _names_and_bits(result).values())
    qr_index = 0 if qr_first else 1
    placed = image_records_of(result.data)[qr_index]
    assert not placed.endswith(HELLO_NAME.encode("ascii"))


@pytest.mark.os_agnostic
@pytest.mark.parametrize("qr_first", [True, False])
def test_a_drawn_image_never_takes_the_name_of_a_stored_user_image(qr_first: bool) -> None:
    qr = {"type": "qr", "x": 5, "y": 5, "data": "HELLO"}
    image = _user_image(bitmap=None)
    label = Label.model_validate({"elements": [qr, image] if qr_first else [image, qr]})
    result = build_job(label, RenderOptions(model="dmx-600"))
    assert result.downloads == (drawn_image_name(qr_bitmap("HELLO", error=QrError.M), salt=1),)
    names = [record[15:] for record in image_records_of(result.data)]
    assert sorted(names) == sorted([HELLO_NAME.encode("ascii"), result.downloads[0].encode("ascii")])


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("reserved", "taken_by_other", "queued_twice", "salt"),
    [
        pytest.param(False, False, False, 0, id="nothing-in-the-way"),
        pytest.param(False, False, True, 0, id="queued-twice-keeps-its-name"),
        pytest.param(False, True, False, 0, id="salt-1-taken-but-not-needed"),
        pytest.param(False, True, True, 0, id="salt-1-taken-queued-twice"),
        pytest.param(True, False, False, 1, id="reserved-goes-to-salt-1"),
        pytest.param(True, False, True, 1, id="reserved-queued-twice"),
        pytest.param(True, True, False, 2, id="reserved-and-salt-1-taken-goes-to-salt-2"),
        pytest.param(True, True, True, 2, id="reserved-salt-1-taken-queued-twice"),
    ],
)
def test_download_drawn_image_never_names_two_different_images_alike(
    reserved: bool, taken_by_other: bool, queued_twice: bool, salt: int
) -> None:
    """The docstring's claim: whatever already holds or reserves the content name, a job never
    sends two different images under one name, and the same bitmap keeps one name.

    ``reserved``: a user image holds the content name; ``taken_by_other``: a different image is
    already queued under the salt-1 name; ``queued_twice``: the same bitmap is queued again."""
    bitmap = Bitmap.from_rows(["#."])
    first = drawn_image_name(bitmap)
    ctx = RenderContext(
        profile=get_profile("generic"),
        firmware=None,
        options=frozenset(),
        unit=Unit.MM,
        dpl_unit=DplUnit.INCH_HUNDREDTHS,
        findings=FindingCollector(FallbackPolicy.DEGRADE),
        reserved_image_names=frozenset({first}) if reserved else frozenset(),
    )
    if taken_by_other:
        ctx.download_image(drawn_image_name(bitmap, salt=1), Bitmap.from_rows([".#"]), element=0)
    name = drawn_name(ctx, bitmap)
    assert name == drawn_image_name(bitmap, salt=salt)
    if queued_twice:
        assert drawn_name(ctx, bitmap) == name
    assert name not in ctx.reserved_image_names
    commands = [download.command for download in ctx.downloads if download.name == name]
    assert len(commands) == 1
    assert len({download.name for download in ctx.downloads}) == len(ctx.downloads)


# ======================== dot size, edge consistency ========================


@pytest.mark.os_agnostic
def test_a_qr_on_a_label_with_a_dot_size_above_1x1_is_warned_about() -> None:
    def sized(dot_size: dict[str, int] | None) -> Label:
        return qr_label().model_copy(update={"dot_size": dot_size})

    warned = build_job(Label.model_validate(sized({"width": 2, "height": 1})), RenderOptions(model="dmx-600"))
    [finding] = [f for f in warned.findings if f.code is FindingCode.QR_DOT_SIZE_NOT_ONE]
    assert (finding.severity, finding.element) == (Severity.WARNING, 0)
    assert "2x1 times larger" in finding.message
    for plain in (Label.model_validate(sized({"width": 1, "height": 1})), qr_label()):
        assert FindingCode.QR_DOT_SIZE_NOT_ONE not in codes(build_job(plain, RenderOptions(model="dmx-600")))


@pytest.mark.os_agnostic
def test_the_right_edge_follows_the_corrected_column_the_record_writes() -> None:
    """Allegro 2, scale_across 1.02: x = 93.4 mm is written as column 366 (+6 quiet), dot 744, so
    the symbol ends at dot 819 of 832; reading x uncorrected (dot 746 + 87) would refuse it."""
    options = RenderOptions(model="allegro-2", scale_across=1.02)
    fits = build_job(qr_label(x=93.4), options)
    assert FindingCode.QR_PAST_PRINT_WIDTH not in codes(fits)
    assert fits.ok, fits.findings
    past = build_job(qr_label(x=95.2), options)
    assert FindingCode.QR_PAST_PRINT_WIDTH in codes(past)


@pytest.mark.os_agnostic
def test_a_qr_starting_past_the_last_column_gets_one_finding_not_two() -> None:
    result = build_job(qr_label(x=105), RenderOptions(model="allegro-2"))
    assert FindingCode.OUTSIDE_PRINT_WIDTH in codes(result)
    assert FindingCode.QR_PAST_PRINT_WIDTH not in codes(result)


# ======================== the CLI ========================


def run_cli(runner: CliRunner, args: list[str], printer: RecordingPrinter | None = None) -> Result:
    return runner.invoke(cli_mod.cli, args, obj=lambda: build_testing(printer=printer))


def qr_file(tmp_path: Path) -> str:
    path = tmp_path / "qr.json"
    path.write_text(json.dumps(QR_DOCUMENT), encoding="utf-8")
    return str(path)


def envelope(result: Result) -> dict[str, Any]:
    return json.loads(result.stdout)


@pytest.mark.os_agnostic
def test_validate_accepts_a_qr_label(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = run_cli(cli_runner, ["--json", "validate", qr_file(tmp_path), "--model", "allegro-2"])
    assert result.exit_code == 0, result.output
    assert envelope(result)["ok"] is True


@pytest.mark.os_agnostic
def test_render_shows_the_download_and_the_magnified_record(cli_runner: CliRunner, tmp_path: Path) -> None:
    result = run_cli(cli_runner, ["render", qr_file(tmp_path), "--model", "allegro-2"])
    assert result.exit_code == 0, result.output
    name = drawn_image_name(qr_bitmap("HELLO", error=QrError.Q))
    assert f"<STX>IAAF{name}<CR>" in result.stdout
    assert "1Y44000" in result.stdout


@pytest.mark.os_agnostic
def test_print_sends_exactly_the_rendered_qr_job(cli_runner: CliRunner, tmp_path: Path) -> None:
    printer = RecordingPrinter()
    args = ["--json", "print", qr_file(tmp_path), "--model", "allegro-2", "--printer", PRINTER]
    result = run_cli(cli_runner, args, printer)
    assert result.exit_code == 0, result.output
    assert printer.data == render(Label.model_validate(QR_DOCUMENT), RenderOptions(model="allegro-2")).data


@pytest.mark.os_agnostic
def test_the_schema_describes_the_qr_element(cli_runner: CliRunner) -> None:
    schema = envelope(run_cli(cli_runner, ["--json", "schema"]))["data"]["label_schema"]
    qr = schema["$defs"]["Qr"]
    assert set(qr["properties"]) >= {"data", "error", "module_dots", "quiet_zone"}
    assert schema["$defs"]["QrError"]["enum"] == ["L", "M", "Q", "H"]


@pytest.mark.os_agnostic
def test_explain_of_a_qr_job_shows_the_download_and_the_placement(cli_runner: CliRunner, tmp_path: Path) -> None:
    job = tmp_path / "qr.dpl"
    job.write_bytes(render(Label.model_validate(QR_DOCUMENT), RenderOptions(model="allegro-2")).data)
    commands = envelope(run_cli(cli_runner, ["--json", "explain", str(job)]))["data"]["commands"]
    assert [c["meaning"] for c in commands if c["kind"] == "download"] == ["image download"]
    assert "image at magnification 4" in [c["meaning"] for c in commands]
