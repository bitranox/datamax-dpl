"""Data values a label must not be able to smuggle into a job: above Latin-1, control bytes, counter fills."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.barcodes import check_barcode
from datamax_dpl.domain.enums import BarcodeProblemCode, FindingCode, Unit
from datamax_dpl.domain.errors import LabelRejectedError
from datamax_dpl.domain.label import Barcode, Counter, DataMatrixOptions, Label, Pdf417Options, Text
from datamax_dpl.domain.render import build_job, render
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.symbology import Symbology

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner

EURO = chr(0x20AC)
SOH, STX, ESC = chr(0x01), chr(0x02), chr(0x1B)
#: A model every symbology renders on, so no gate hides the data check.
MODEL = "dmx-600"


def _barcode_label(symbology: Symbology, data: str) -> Label:
    return Label(unit=Unit.MM, elements=[Barcode(x=5, y=5, symbology=symbology, data=data, height=10)])


@pytest.mark.os_agnostic
@pytest.mark.parametrize("symbology", list(Symbology))
@pytest.mark.parametrize("data", [EURO, f"AB{EURO}1", chr(0x4E2D), chr(0x1F600), "1234" + EURO])
def test_a_value_above_latin1_is_a_finding_and_never_an_encode_error(symbology: Symbology, data: str) -> None:
    """build_job reports every finding: a character the check refused is never encoded."""
    result = build_job(_barcode_label(symbology, data), RenderOptions(model=MODEL))
    assert not result.ok
    assert data.encode("utf-8") not in result.data


@pytest.mark.os_agnostic
def test_render_refuses_a_value_above_latin1_with_its_own_error() -> None:
    """render raises the package's refusal, not a UnicodeEncodeError from inside the encoder."""
    with pytest.raises(LabelRejectedError):
        render(_barcode_label(Symbology.CODE128, EURO), RenderOptions(model=MODEL))


@pytest.mark.os_agnostic
def test_the_cli_exits_1_with_the_finding_for_a_record_above_latin1(cli_runner: CliRunner, tmp_path: Path) -> None:
    """One record above Latin-1 refuses the batch at exit 1 with the finding; nothing crashes at exit 2."""
    label = tmp_path / "label.json"
    label.write_text(
        json.dumps({"elements": [{"type": "barcode", "x": 5, "y": 5, "symbology": "code128", "data": "{sku}"}]}),
        encoding="utf-8",
    )
    records = tmp_path / "records.csv"
    records.write_text(f"sku\nAB{EURO}1\n", encoding="utf-8")
    result = cli_runner.invoke(
        cli_mod.cli,
        ["--json-bare", "validate", "--model", MODEL, "--data", str(records), str(label)],
        obj=build_testing,
    )
    assert result.exit_code == 1, result.output
    document = json.loads(result.stdout)
    assert document["ok"] is False
    assert FindingCode.BARCODE_BAD_CHARACTER.value in [finding["code"] for finding in document["data"]["findings"]]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("symbology", [s for s in Symbology if s is not Symbology.FIM])
@pytest.mark.parametrize("control", [SOH, STX, ESC])
def test_a_control_byte_is_refused_in_every_symbology(symbology: Symbology, control: str) -> None:
    """SOH, STX and ESC would reach the printer's interpreter as commands, whatever the character set says."""
    check = check_barcode(symbology, f"X{control}qE")
    assert BarcodeProblemCode.CONTROL_CHARACTER in [problem.code for problem in check.problems]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("symbology", [Symbology.PDF417, Symbology.DATAMATRIX, Symbology.TELEPEN, Symbology.MAXICODE])
def test_the_injected_command_never_reaches_the_job(symbology: Symbology) -> None:
    """The record carrying <STX>qE<SOH># stays out of the job, and the finding names the refusal."""
    result = build_job(_barcode_label(symbology, f"X{STX}qE{SOH}#"), RenderOptions(model=MODEL))
    assert FindingCode.BARCODE_CONTROL_CHARACTER in [finding.code for finding in result.errors]
    assert f"X{STX}qE".encode("latin-1") not in result.data


@pytest.mark.os_agnostic
def test_the_control_message_names_only_the_bytes_found() -> None:
    """The message says which byte it found, so the data can be corrected."""
    check = check_barcode(Symbology.PDF417, f"A{ESC}B")
    message = next(p.message for p in check.problems if p.code is BarcodeProblemCode.CONTROL_CHARACTER)
    assert "<ESC>" in message
    assert "<STX>" not in message


@pytest.mark.os_agnostic
@pytest.mark.parametrize("byte", [chr(0x03), chr(0x1A), chr(0x1C), chr(0x7F), chr(0xFF)])
def test_other_bytes_stay_allowed_in_pdf417(byte: str) -> None:
    """Only the three bytes the interpreter acts on are refused; the rest of the 8-bit range still prints."""
    assert check_barcode(Symbology.PDF417, f"A{byte}B").ok


@pytest.mark.os_agnostic
@pytest.mark.parametrize("fill", ["\r", "\n", SOH, STX, ESC, EURO, chr(0x7F), chr(0xE4), "\x00"])
def test_a_counter_fill_takes_printable_ascii_only(fill: str) -> None:
    """A CR would split the record, STX and SOH would go out raw, and the euro sign crashed the encoder."""
    with pytest.raises(ValidationError):
        Counter(fill=fill)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("fill", ["0", " ", "~", "A"])
def test_a_counter_fill_keeps_every_printable_character(fill: str) -> None:
    """The printable range edges and the documented fills still work."""
    assert Counter(fill=fill).fill == fill


# ======================== Documented records through a whole render ========================


def _record_lines(label: Label, model: str = MODEL) -> list[bytes]:
    result = build_job(label, RenderOptions(model=model))
    assert result.ok, [finding.message for finding in result.errors]
    return result.data.split(b"\r")


def _codes(document: dict[str, object], model: str = MODEL) -> set[FindingCode]:
    label = Label.model_validate({"elements": [{"type": "text", "x": 5, "y": 5, "text": "X"}], **document})
    return {finding.code for finding in build_job(label, RenderOptions(model=model)).findings}


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("symbology", "pdf417", "datamatrix", "field"),
    [
        (Symbology.PDF417, None, None, b"F2000000AB12"),
        (Symbology.PDF417, Pdf417Options(truncated=True, security_level=8), None, b"T8000000AB12"),
        (Symbology.DATAMATRIX, None, None, b"2000000000AB12"),
        (Symbology.DATAMATRIX, None, DataMatrixOptions(ecc=200, rows=18, columns=18), b"2000018018AB12"),
    ],
    ids=["pdf417", "pdf417-options", "datamatrix", "datamatrix-options"],
)
def test_a_2d_barcode_reaches_the_job_with_its_data_header(
    symbology: Symbology, pdf417: Pdf417Options | None, datamatrix: DataMatrixOptions | None, field: bytes
) -> None:
    barcode = Barcode(x=5, y=5, symbology=symbology, data="AB12", pdf417=pdf417, datamatrix=datamatrix)
    [record] = [line for line in _record_lines(Label(unit=Unit.MM, elements=[barcode])) if line.endswith(b"AB12")]
    assert record.endswith(field)
    assert not record[: -len(field)].endswith(b"AB12")


@pytest.mark.os_agnostic
def test_the_column_and_row_offsets_reach_the_job() -> None:
    """The job addresses 1/10 mm here (it opens with ``m``), so 10 and 20 mm are 100 and 200."""
    lines = _record_lines(Label(unit=Unit.MM, column_offset=10, row_offset=20, elements=[Text(x=5, y=5, text="X")]))
    assert b"C0100" in lines
    assert b"R0200" in lines


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("document", "code", "model"),
    [
        ({"media": "continuous"}, FindingCode.CONTINUOUS_LENGTH_MISSING, MODEL),
        ({"column_offset": 3000}, FindingCode.DISTANCE_TOO_LARGE, MODEL),
        ({"elements": [{"type": "text", "x": 3000, "y": 5, "text": "X"}]}, FindingCode.DISTANCE_TOO_LARGE, MODEL),
        (
            {"elements": [{"type": "barcode", "x": 5, "y": 5, "symbology": "code128", "data": "A" * 256}]},
            FindingCode.FIELD_TOO_LONG,
            MODEL,
        ),
        (
            {"elements": [{"type": "barcode", "x": 5, "y": 5, "symbology": "code128", "data": "A", "height": 300}]},
            FindingCode.BARCODE_HEIGHT_OUT_OF_RANGE,
            MODEL,
        ),
        (
            {"elements": [{"type": "barcode", "x": 5, "y": 5, "symbology": "code128", "data": "A", "height": 0.01}]},
            FindingCode.BARCODE_HEIGHT_OUT_OF_RANGE,
            MODEL,
        ),
        (
            {"elements": [{"type": "text", "x": 5, "y": 5, "text": "X", "attribute": 5}]},
            FindingCode.ATTRIBUTE_UNSUPPORTED,
            "allegro-2",
        ),
    ],
    ids=["continuous", "offset", "field-x", "field-length", "height-high", "height-low", "inverse"],
)
def test_a_value_no_record_can_hold_is_refused_by_name(
    document: dict[str, object], code: FindingCode, model: str
) -> None:
    label = Label.model_validate({"elements": [{"type": "text", "x": 5, "y": 5, "text": "X"}], **document})
    result = build_job(label, RenderOptions(model=model))
    assert code in {finding.code for finding in result.errors}
    assert not result.ok


@pytest.mark.os_agnostic
def test_a_cut_past_the_quantity_is_said_and_sends_no_cut_count() -> None:
    assert FindingCode.CUT_BY_NOT_BELOW_QUANTITY in _codes({"quantity": 2, "cut": {"every": 5}})
    label = Label.model_validate(
        {"quantity": 2, "cut": {"every": 5}, "elements": [{"type": "text", "x": 5, "y": 5, "text": "X"}]}
    )
    assert b":0005" not in _record_lines(label)
