"""The lookup tables keyed by a closed enum: each holds every member it must, read-only.

Each table is private to its module, so it is checked through what reads it: a member the table
lacks shows up as a KeyError, a wrong value or a fallback where the public function is asked about
that member.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from datamax_dpl.adapters.camera import capture_image
from datamax_dpl.adapters.camera.file import MEDIA_TYPE_BY_SUFFIX
from datamax_dpl.adapters.camera.http import EXTENSION_BY_MEDIA_TYPE, MEDIA_TYPE_BY_CONTENT_TYPE
from datamax_dpl.adapters.cli.payloads import FindingOut, findings_text
from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
from datamax_dpl.application.ports import PrinterEndpoint
from datamax_dpl.application.use_cases import PrinterCommand, send_command
from datamax_dpl.domain.barcodes import check_barcode
from datamax_dpl.domain.camera import CameraKind, CameraSettings
from datamax_dpl.domain.enums import (
    SPEED_IPS,
    Attribute,
    BarcodeProblemCode,
    CutterPresence,
    FillPattern,
    FindingCode,
    MediaType,
    Severity,
    SpeedCode,
    Unit,
)
from datamax_dpl.domain.errors import CameraError
from datamax_dpl.domain.label import Box, Circle, Label, Line, Point, Polygon, Text
from datamax_dpl.domain.preview import preview_bitmap
from datamax_dpl.domain.profiles import get_profile
from datamax_dpl.domain.raster import TILE_SIZE, Bitmap
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.status import CANCEL, CUT_NOW, FORM_FEED, RESET, TOGGLE_PAUSE
from datamax_dpl.domain.symbology import SYMBOLOGY_BY_IDENTIFIER, SYMBOLOGY_CODES, Symbology

if TYPE_CHECKING:
    from pathlib import Path

_COMMAND_BYTES = {
    PrinterCommand.RESET: RESET,
    PrinterCommand.CANCEL: CANCEL,
    PrinterCommand.PAUSE: TOGGLE_PAUSE,
    PrinterCommand.FEED: FORM_FEED,
    PrinterCommand.CUT: CUT_NOW,
}


@pytest.mark.os_agnostic
def test_the_command_table_covers_every_printer_command() -> None:
    assert set(_COMMAND_BYTES) == set(PrinterCommand)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", list(PrinterCommand), ids=lambda command: command.value)
def test_every_printer_command_sends_its_own_bytes(command: PrinterCommand) -> None:
    printer = RecordingPrinter()
    send_command(
        command,
        endpoint=PrinterEndpoint(
            uri="tcp://192.0.2.60:9100", timeout=1.0, open_printer=open_recording_printer(printer)
        ),
    )
    assert printer.data == _COMMAND_BYTES[command]


#: The black share of each shade's 8x8 tile: the nearest multiple of 1/64 to its nominal percent.
_SHADE_BLACK_CELLS = {
    FillPattern.SHADE_6: 4,
    FillPattern.SHADE_12: 8,
    FillPattern.SHADE_25: 16,
    FillPattern.SHADE_38: 24,
    FillPattern.SHADE_50: 32,
}


def _black_cells(pattern: FillPattern) -> int:
    bitmap = Bitmap(TILE_SIZE, TILE_SIZE)
    bitmap.fill_rect(0, 0, TILE_SIZE, TILE_SIZE, pattern=pattern)
    return bitmap.black_pixels()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("pattern", list(FillPattern), ids=lambda pattern: pattern.name)
def test_every_fill_pattern_has_a_tile(pattern: FillPattern) -> None:
    black = _black_cells(pattern)
    assert 0 <= black <= TILE_SIZE * TILE_SIZE
    if pattern in _SHADE_BLACK_CELLS:
        assert black == _SHADE_BLACK_CELLS[pattern]


@pytest.mark.os_agnostic
def test_the_shade_patterns_are_the_ones_drawn_by_dither() -> None:
    shades = {pattern for pattern in FillPattern if pattern.name.startswith("SHADE_")}
    assert shades == set(_SHADE_BLACK_CELLS)


@pytest.mark.os_agnostic
def test_every_speed_letter_has_a_read_only_speed() -> None:
    assert set(SPEED_IPS) == set(SpeedCode)
    assert (SPEED_IPS[SpeedCode.IPS_1_0], SPEED_IPS[SpeedCode.IPS_10_0]) == (1.0, 10.0)
    with pytest.raises(TypeError):
        cast("dict[SpeedCode, float]", SPEED_IPS)[SpeedCode.IPS_1_0] = 2.0


@pytest.mark.os_agnostic
@pytest.mark.parametrize("problem", list(BarcodeProblemCode), ids=lambda problem: problem.value)
def test_every_barcode_problem_has_its_own_finding_code(problem: BarcodeProblemCode) -> None:
    assert problem.finding_code is FindingCode(f"barcode_{problem.value}")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("symbology", list(Symbology), ids=lambda symbology: symbology.value)
def test_every_symbology_has_a_data_check(symbology: Symbology) -> None:
    assert check_barcode(symbology, "1").data == "1"


@pytest.mark.os_agnostic
def test_every_symbology_has_a_code_and_every_identifier_reads_back_as_it() -> None:
    assert set(SYMBOLOGY_CODES) == set(Symbology)
    for symbology, code in SYMBOLOGY_CODES.items():
        for identifier in (code.with_text, code.bars_only):
            if identifier is not None:
                assert SYMBOLOGY_BY_IDENTIFIER[identifier] is symbology
    with pytest.raises(TypeError):
        cast("dict[str, Symbology]", SYMBOLOGY_BY_IDENTIFIER)["e"] = Symbology.CODE39


@pytest.mark.os_agnostic
@pytest.mark.parametrize("attribute", list(Attribute), ids=lambda attribute: attribute.name)
def test_every_attribute_has_a_preview_combine_mode(attribute: Attribute) -> None:
    label = Label(
        unit=Unit.DOTS,
        width=200,
        height=100,
        attribute=attribute,
        elements=[Text(x=10, y=10, text="AB"), Text(x=12, y=12, text="AB")],
    )
    assert preview_bitmap(label, get_profile("dmx-800")).width > 0


@pytest.mark.os_agnostic
def test_an_inverse_field_shows_in_the_preview_as_xor() -> None:
    def drawn(attribute: Attribute) -> list[bytes]:
        label = Label(
            unit=Unit.DOTS,
            width=200,
            height=100,
            attribute=attribute,
            elements=[Text(x=10, y=10, text="AB"), Text(x=12, y=12, text="AB")],
        )
        return list(preview_bitmap(label, get_profile("dmx-800")).rows())

    assert drawn(Attribute.INVERSE) == drawn(Attribute.XOR)
    assert drawn(Attribute.INVERSE) != drawn(Attribute.TRANSPARENT)


@pytest.mark.os_agnostic
def test_every_media_type_has_a_read_only_extension_it_is_read_back_as() -> None:
    assert set(EXTENSION_BY_MEDIA_TYPE) == set(MediaType)
    for media_type, extension in EXTENSION_BY_MEDIA_TYPE.items():
        assert MEDIA_TYPE_BY_SUFFIX[extension] is media_type
    with pytest.raises(TypeError):
        cast("dict[MediaType, str]", EXTENSION_BY_MEDIA_TYPE)[MediaType.PNG] = ".gif"


@pytest.mark.os_agnostic
def test_the_camera_content_types_name_every_media_type_and_image_jpg_as_an_alias() -> None:
    assert dict(MEDIA_TYPE_BY_CONTENT_TYPE) == {
        "image/jpeg": MediaType.JPEG,
        "image/jpg": MediaType.JPEG,
        "image/png": MediaType.PNG,
    }
    assert set(MEDIA_TYPE_BY_CONTENT_TYPE.values()) == set(MediaType)
    with pytest.raises(TypeError):
        cast("dict[str, MediaType]", MEDIA_TYPE_BY_CONTENT_TYPE)["image/gif"] = MediaType.PNG


@pytest.mark.os_agnostic
def test_the_picked_up_suffixes_are_read_only_and_name_every_media_type() -> None:
    assert dict(MEDIA_TYPE_BY_SUFFIX) == {".jpg": MediaType.JPEG, ".jpeg": MediaType.JPEG, ".png": MediaType.PNG}
    assert set(MEDIA_TYPE_BY_SUFFIX.values()) == set(MediaType)
    with pytest.raises(TypeError):
        cast("dict[str, MediaType]", MEDIA_TYPE_BY_SUFFIX)[".gif"] = MediaType.PNG


@pytest.mark.os_agnostic
@pytest.mark.parametrize("kind", list(CameraKind), ids=lambda kind: kind.value)
def test_every_camera_kind_but_none_has_a_capture(kind: CameraKind, tmp_path: Path) -> None:
    with pytest.raises(CameraError) as caught:
        capture_image(CameraSettings(kind=kind, timeout=0.5), output_dir=tmp_path)
    unconfigured = str(caught.value) == "no camera configured: set [camera] kind"
    assert unconfigured is (kind is CameraKind.NONE)


@pytest.mark.os_agnostic
def test_findings_text_ranks_every_severity_most_severe_first() -> None:
    def finding(severity: Severity) -> FindingOut:
        return FindingOut(
            code=FindingCode.MODEL_DEFAULTS_APPLIED, severity=severity, message="m", element=None, fallback=None
        )

    text = findings_text([finding(Severity.INFO), finding(Severity.WARNING), finding(Severity.ERROR)])
    assert [line.split()[0] for line in text.splitlines()] == ["ERROR", "WARNING", "INFO"]
    assert {Severity.INFO, Severity.WARNING, Severity.ERROR} == set(Severity)


@pytest.mark.os_agnostic
def test_the_cutter_cases_are_every_cutter_presence() -> None:
    assert {CutterPresence.YES, CutterPresence.NO, CutterPresence.UNKNOWN} == set(CutterPresence)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("element", "code"),
    [
        (Line(x=1.0, y=1.0, x2=20.0, y2=15.0, thickness=0.5), FindingCode.LINE_RASTERIZED),
        (
            Box(x=1.0, y=1.0, width=10.0, height=5.0, thickness=0.5, fill=FillPattern.SHADE_25),
            FindingCode.BOX_RASTERIZED,
        ),
        (Circle(x=10.0, y=10.0, radius=4.0), FindingCode.CIRCLE_RASTERIZED),
        (
            Polygon(points=[Point(x=1.0, y=1.0), Point(x=9.0, y=1.0), Point(x=5.0, y=8.0)]),
            FindingCode.POLYGON_RASTERIZED,
        ),
    ],
    ids=["line", "box", "circle", "polygon"],
)
def test_every_graphic_a_model_cannot_draw_is_reported_as_rasterized_by_its_own_code(
    element: Line | Box | Circle | Polygon, code: FindingCode
) -> None:
    result = build_job(Label(width=40.0, height=30.0, elements=[element]), RenderOptions(model="allegro"))
    assert code in {finding.code for finding in result.findings}
