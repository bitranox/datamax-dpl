"""Lettering masks: refused before any is built, and cut to the canvas before they are allocated."""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from datamax_dpl.adapters import preview as preview_adapter
from datamax_dpl.adapters.preview import render_preview_png
from datamax_dpl.domain.enums import Rotation, Unit
from datamax_dpl.domain.errors import PreviewTooLongError
from datamax_dpl.domain.label import Label, ScalableText, Text
from datamax_dpl.domain.preview import canvas_for, printed_text
from datamax_dpl.domain.profiles import Firmware, get_profile
from datamax_dpl.domain.render_context import RenderOptions

_MODEL = "dmx-600"
#: The widest field a label states: 255 characters at 24 times the size, tens of thousands of dots.
_HUGE = Text(x=1, y=500, text="W" * 255, font=8, width_multiplier=24, height_multiplier=24)


def _huge_fields(count: int) -> Label:
    return Label(unit=Unit.DOTS, width=800, height=800, elements=[_HUGE] * count)


#: A grey level below this reads as dark: the preview PNG is mode "1", so its pixels are 0 or 255.
_DARK_BELOW = 128
#: The pixels darker than :data:`_DARK_BELOW` in a mode "L" row of bytes.
_DARK_BYTE = re.compile(rb"[\x00-\x7f]")


def _dark_pixels(label: Label, *, model: str = _MODEL) -> set[tuple[int, int]]:
    """Dark pixels of the lettered preview PNG, its one-dot frame left out, read a whole row at a time."""
    image_module = pytest.importorskip("PIL.Image")
    with image_module.open(io.BytesIO(render_preview_png(label, get_profile(model)))) as image:
        grey = image.convert("L")
    width, height = grey.size
    data = grey.tobytes()
    return {
        (found.start() - y * width, y)
        for y in range(1, height - 1)
        for found in _DARK_BYTE.finditer(data, y * width + 1, (y + 1) * width - 1)
    }


def _dark_pixels_pixel_by_pixel(label: Label) -> set[tuple[int, int]]:
    image_module = pytest.importorskip("PIL.Image")
    with image_module.open(io.BytesIO(render_preview_png(label, get_profile(_MODEL)))) as image:
        image.load()
        width, height = image.size
        return {
            (x, y) for y in range(1, height - 1) for x in range(1, width - 1) if image.getpixel((x, y)) < _DARK_BELOW
        }


@pytest.mark.os_agnostic
@pytest.mark.parametrize("rotation", list(Rotation))
def test_dark_pixels_reads_the_pixels_a_pixel_by_pixel_reading_reads(rotation: Rotation) -> None:
    label = Label(
        unit=Unit.DOTS, width=120, height=90, elements=[Text(x=3, y=40, text="iIlW gq", font=2, rotation=rotation)]
    )
    dark = _dark_pixels(label)
    assert dark, "the fixture letters something"
    assert dark == _dark_pixels_pixel_by_pixel(label)


class _PillowCalls:
    """What the preview asked of Pillow, seen at the third-party edge."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.letters: list[int] = []
        self.pictures: list[tuple[int, int]] = []
        image_module = pytest.importorskip("PIL.Image")
        draw_module = pytest.importorskip("PIL.ImageDraw")
        real_new, real_text = image_module.new, draw_module.ImageDraw.text

        def new(mode: str, size: tuple[int, int], *args: Any, **kwargs: Any) -> Any:
            self.pictures.append(size)
            return real_new(mode, size, *args, **kwargs)

        def text(draw: Any, xy: Any, text: str, *args: Any, **kwargs: Any) -> Any:
            self.letters.append(len(text))
            return real_text(draw, xy, text, *args, **kwargs)

        monkeypatch.setattr(image_module, "new", new)
        monkeypatch.setattr(draw_module.ImageDraw, "text", text)


@pytest.mark.os_agnostic
def test_a_preview_of_too_many_fields_is_refused_with_no_field_lettered(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _PillowCalls(monkeypatch)
    profile = get_profile(_MODEL)
    with pytest.raises(PreviewTooLongError, match=f"takes at most {profile.max_fields}"):
        render_preview_png(_huge_fields(profile.max_fields + 1), profile)
    assert calls.letters == []
    assert calls.pictures == []


@pytest.mark.os_agnostic
def test_a_field_far_past_the_canvas_allocates_no_more_than_the_canvas(monkeypatch: pytest.MonkeyPatch) -> None:
    label = _huge_fields(3)
    profile = get_profile(_MODEL)
    canvas = canvas_for(label, profile)
    calls = _PillowCalls(monkeypatch)
    render_preview_png(label, profile)
    assert len(calls.letters) == 3
    assert max(calls.letters) < 10
    assert all(width <= canvas.width and height <= canvas.height for width, height in calls.pictures)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("rotation", list(Rotation))
def test_a_field_cut_by_the_canvas_keeps_every_dot_of_it_on_the_canvas(rotation: Rotation) -> None:
    """The cut picture holds exactly what the whole field shows there, on each side a turned field leaves by.

    The same field on a canvas large enough for all of it is the reference; narrow letters prove the
    letters drawn reach the end of the part on the canvas.
    """
    # A shift of whole DPL units (0.01 in, 3 dots here): another would round the field's place differently.
    size, shift, x, y = 300, 600, 150, 150
    text = "iIlW" * 60
    small = Label(
        unit=Unit.DOTS, width=size, height=size, elements=[Text(x=x, y=y, text=text, font=2, rotation=rotation)]
    )
    large = Label(
        unit=Unit.DOTS,
        width=size + 2 * shift,
        height=size + 2 * shift,
        elements=[Text(x=x + shift, y=y + shift, text=text, font=2, rotation=rotation)],
    )
    profile = get_profile(_MODEL)
    edge = canvas_for(small, profile).width
    assert canvas_for(large, profile).width == edge + 2 * shift
    moved = {(px - shift, py - shift) for px, py in _dark_pixels(large)}
    inside = {(px, py) for px, py in moved if 1 <= px < edge - 1 and 1 <= py < edge - 1}
    drawn = _dark_pixels(small)
    assert drawn
    assert moved - inside
    assert drawn == inside


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="the resource module, which reads peak memory, is POSIX-only")
def test_the_most_fields_a_model_takes_stay_under_a_memory_bound(tmp_path: Path) -> None:
    """399 huge fields took 6.7 GB and more than 281 s; drawn in their own process to read the peak."""
    pytest.importorskip("PIL.Image")
    script = textwrap.dedent(
        """\
        import resource
        import sys

        from datamax_dpl.adapters.preview import render_preview_png
        from datamax_dpl.domain.enums import Unit
        from datamax_dpl.domain.label import Label, Text
        from datamax_dpl.domain.profiles import get_profile

        field = Text(x=1, y=500, text="W" * 255, font=8, width_multiplier=24, height_multiplier=24)
        label = Label(unit=Unit.DOTS, width=800, height=800, elements=[field] * 399)
        render_preview_png(label, get_profile("dmx-600"))
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(peak // (1024 * 1024) if sys.platform == "darwin" else peak // 1024)
        """
    )
    done = subprocess.run(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8", check=True, cwd=tmp_path
    )
    assert int(done.stdout.strip()) < 600


def _one_letter(char: str, font: int) -> Label:
    points = 24 if font == 9 else None
    return Label(
        unit=Unit.DOTS, width=200, height=160, elements=[Text(x=10, y=20, text=char, font=font, points=points)]
    )


@pytest.mark.os_agnostic
@pytest.mark.parametrize("pair", ["üö", "äß", "ÄÖ", "éè", "ÜÇ"])
@pytest.mark.parametrize("font", [2, 9])
def test_accented_letters_are_lettered_as_themselves_not_as_one_shared_box(pair: str, font: int) -> None:
    """Two different accented letters draw differently; a font lacking them draws both as the same box."""
    first, second = (_dark_pixels(_one_letter(char, font)) for char in pair)
    assert first, f"{pair[0]!r} drew nothing"
    assert first != second, f"{pair!r} drew the same pixels for both letters"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("typed", "printed", "font"),
    [("abc", "ABC", 3), ("a\N{EURO SIGN}b", "a?b", 2), ("a\N{EURO SIGN}b", "a?b", 9)],
)
def test_a_text_field_is_lettered_as_the_printer_prints_it_not_as_typed(typed: str, printed: str, font: int) -> None:
    """Font 3 prints capitals only and fonts 2 and 9 have no euro sign (printed '?'): the preview shows the paper."""
    assert _dark_pixels(_one_letter(typed, font)) == _dark_pixels(_one_letter(printed, font))


#: Code page 437 (PC) has neither letter, 850 (font 9) has the A grave, 1252 (W1) has both: each path
#: the encoder can take letters it differently.
_A_GRAVE_EURO = "a\N{LATIN CAPITAL LETTER A WITH GRAVE}\N{EURO SIGN}"
_FONT9_OF_IT = "a\N{LATIN CAPITAL LETTER A WITH GRAVE}?"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("model", "symbol_set", "printed"),
    [
        ("dmx-800", None, "a??"),
        ("dmx-800", "W1", _A_GRAVE_EURO),
        ("allegro-2", "W1", _FONT9_OF_IT),
    ],
)
def test_scalable_text_is_lettered_in_the_encoding_its_record_is_sent_in(
    model: str, symbol_set: str | None, printed: str
) -> None:
    """Scalable text goes out in the label's symbol set (PC when none); a model without scalable fonts sends font 9."""
    element = ScalableText(x=0, y=0, text=_A_GRAVE_EURO, height_points=12)
    label = Label(symbol_set=symbol_set, elements=[element])
    assert printed_text(element, canvas_for(label, get_profile(model))) == printed


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("firmware", "printed"),
    [
        (Firmware(major=2, minor=1), _A_GRAVE_EURO),
        (Firmware(major=1, minor=0), _FONT9_OF_IT),
        (None, _FONT9_OF_IT),
    ],
)
def test_scalable_text_is_lettered_by_the_firmware_the_job_is_rendered_for(
    firmware: Firmware | None, printed: str
) -> None:
    """A DMX 600 has scalable fonts from firmware 2.01 on; before that (or unstated) the field goes out in font 9."""
    element = ScalableText(x=0, y=0, text=_A_GRAVE_EURO, height_points=12)
    label = Label(symbol_set="W1", elements=[element])
    canvas = canvas_for(label, get_profile("dmx-600"), options=RenderOptions(model="dmx-600", firmware=firmware))
    assert printed_text(element, canvas) == printed


@pytest.mark.os_agnostic
def test_a_symbol_set_without_a_known_codec_is_lettered_as_typed_and_the_preview_still_draws() -> None:
    """No codec is known for set XX, so what prints is unknown: the preview letters the text as typed, as 0.12.0 did.

    The encoder refuses such a label; the preview picture of it is drawn all the same, with or without Pillow.
    """
    element = ScalableText(x=1, y=1, text="a\N{EURO SIGN}", height_points=12)
    label = Label(symbol_set="XX", elements=[element])
    profile = get_profile("dmx-800")
    assert printed_text(element, canvas_for(label, profile)) == "a\N{EURO SIGN}"
    assert render_preview_png(label, profile)[1:4] == b"PNG"


def _font9_text(text: str) -> Label:
    """One font 9 line at 30 points: the printer's narrow '1' is much narrower than the lettering font's."""
    return Label(unit=Unit.DOTS, width=400, height=200, elements=[Text(x=10, y=20, text=text, font=9, points=30)])


def _row_runs(pixels: set[tuple[int, int]], y: int) -> list[int]:
    """The lengths of the runs of dark columns in row ``y``, left to right."""
    columns = sorted(x for x, row in pixels if row == y)
    runs: list[int] = []
    for index, x in enumerate(columns):
        if index and x == columns[index - 1] + 1:
            runs[-1] += 1
        else:
            runs.append(1)
    return runs


@pytest.mark.os_agnostic
def test_a_one_is_lettered_as_heavy_as_a_zero() -> None:
    """The stem of a font 9 '1' is as thick as the strokes of a '0' beside it, as on paper.

    The printer's '1' is a bare stem, the width of its ink box. A lettering glyph with a foot is wider than that
    box and is narrowed into it, which thinned its stem to about half the '0' strokes ("BAT001" read with a
    small '1'); a footless glyph fits the box as it is.
    """
    label = Label(unit=Unit.DOTS, width=200, height=120, elements=[Text(x=10, y=20, text="10", font=9, points=14)])
    pixels = _dark_pixels(label, model="allegro-2")
    rows = [y for _, y in pixels]
    one, zero_left, zero_right = _row_runs(pixels, (min(rows) + max(rows)) // 2)
    assert abs(one - zero_left) <= 1, (one, zero_left)
    assert abs(one - zero_right) <= 1, (one, zero_right)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("font", [2, 9])
def test_the_accent_of_a_capital_is_lettered_inside_its_field(font: int) -> None:
    """An A umlaut shows its dots above the A: the baseline sits low enough in the field for a capital's accent."""
    plain = _dark_pixels(_one_letter("A", font))
    accented = _dark_pixels(_one_letter("\N{LATIN CAPITAL LETTER A WITH DIAERESIS}", font))
    assert min(y for _, y in accented) < min(y for _, y in plain) - 1


#: Every character code page 437 or 850 (font 9) prints, as Python decodes bytes 32-255; DEL (127) is no glyph.
_PRINTABLE_CODE_PAGE_CHARACTERS = sorted(
    {bytes([byte]).decode(code_page) for byte in range(0x20, 0x100) if byte != 0x7F for code_page in ("cp437", "cp850")}
)
#: Characters that draw no ink in a font by design: the spaces, and the soft hyphen (shown only at a line break).
_INKLESS = frozenset({" ", "\N{NO-BREAK SPACE}", "\N{SOFT HYPHEN}"})


@pytest.mark.os_agnostic
def test_the_lettering_font_draws_every_code_page_character() -> None:
    """Each character of code pages 437 and 850 has a glyph of its own, not the font's missing-glyph shape.

    Pillow draws a character the font lacks as the font's ``.notdef`` glyph, an empty box or nothing at all.
    """
    font_module = pytest.importorskip("PIL.ImageFont")
    shipped = Path(preview_adapter.__file__).resolve().parent / "fonts" / "NimbusSans-Bold.otf"
    # Pillow looks a missing path up among the system's fonts by its name, so check the shipped file itself.
    assert shipped.is_file()
    font = font_module.truetype(str(shipped), 40)
    missing = font.getmask(chr(0xE000))
    missing_shape = (missing.size, bytes(missing))
    drawn = {char: font.getmask(char) for char in _PRINTABLE_CODE_PAGE_CHARACTERS if char not in _INKLESS}
    lacking = [
        f"U+{ord(char):04X}"
        for char, mask in drawn.items()
        if mask.getbbox() is None or (mask.size, bytes(mask)) == missing_shape
    ]
    assert len(drawn) > 200, "the fixture checks the code pages, not a handful of characters"
    assert lacking == []


@pytest.mark.os_agnostic
def test_a_missing_lettering_font_is_an_error_not_a_system_font(tmp_path: Path) -> None:
    """A package whose font file is gone fails to letter rather than drawing with a system font of that name.

    Given a path that does not exist, Pillow looks the file name up among the installed fonts, so a
    machine with URW's fonts installed would draw a different file without any error.
    """
    pytest.importorskip("PIL.ImageFont")
    copy = tmp_path / "pkg" / "datamax_dpl"
    shutil.copytree(Path(preview_adapter.__file__).resolve().parents[2], copy)
    (copy / "adapters" / "preview" / "fonts" / "NimbusSans-Bold.otf").unlink()
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(copy.parent)!r})
        import datamax_dpl
        from datamax_dpl.adapters.preview import render_preview_png
        from datamax_dpl.domain.enums import Unit
        from datamax_dpl.domain.label import Label, Text
        from datamax_dpl.domain.profiles import get_profile
        print(datamax_dpl.__file__)
        label = Label(unit=Unit.DOTS, width=200, height=80, elements=[Text(x=5, y=5, text="1", font=9, points=24)])
        try:
            render_preview_png(label, get_profile("allegro-2"))
        except FileNotFoundError:
            print("FileNotFoundError")
        else:
            print("drawn")
        """
    )
    done = subprocess.run(
        [sys.executable, "-"], input=script, capture_output=True, text=True, encoding="utf-8", check=True, cwd=tmp_path
    )
    loaded, outcome = done.stdout.split()
    assert Path(loaded).is_relative_to(copy), "the copy, not the installed package, must be imported"
    assert outcome == "FileNotFoundError"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("text", ["1", "BOX 1", "W", "11"])
def test_a_trailing_space_changes_no_letter(text: str) -> None:
    """A space prints nothing, so a line with one more at its end letters exactly the same dots.

    It did not when a letter wider than the printer's advance was drawn at its own width: the space's room let
    the overhang show, while without it the field's end cut it off.
    """
    assert _dark_pixels(_font9_text(text)) == _dark_pixels(_font9_text(text + " "))
