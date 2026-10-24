"""Barcode data checks: character sets, lengths, check digits and the 2-D data headers."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from datamax_dpl.domain.barcodes import (
    BarcodeCheck,
    check_barcode,
    datamatrix_data_field,
    gs1_mod10_check_digit,
    pdf417_data_field,
)
from datamax_dpl.domain.enums import BarcodeProblemCode
from datamax_dpl.domain.label import DataMatrixOptions, Pdf417Options
from datamax_dpl.domain.symbology import Symbology

ARABIC_INDIC_TWO = chr(0x0662)
NUL = chr(0)


def _codes(check: BarcodeCheck) -> list[BarcodeProblemCode]:
    return [problem.code for problem in check.problems]


# ======================== positive cases from the spec ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("symbology", "data"),
    [
        (Symbology.CODE39, "ABC-123 $/+%.*"),
        (Symbology.UPC_A, "03600029145"),
        (Symbology.UPC_A, "036000291452"),
        (Symbology.UPC_E, "425261"),
        (Symbology.UPC_E, "4252614"),
        (Symbology.I2OF5, "123"),
        (Symbology.CODE128, "BHello World 123"),
        (Symbology.CODE128, "&D1234&G5678"),
        (Symbology.EAN13, "012345678901"),
        (Symbology.EAN13, "0123456789012"),
        (Symbology.EAN8, "9638507"),
        (Symbology.EAN8, "96385074"),
        (Symbology.HIBC, "+A123BJC5D6E71"),
        (Symbology.CODABAR, "A12345-$:/.+B"),
        (Symbology.I2OF5_MOD10, "1234567"),
        (Symbology.PLESSEY, "1"),
        (Symbology.PLESSEY, "12345678901234"),
        (Symbology.I2OF5_BEARER, "1234567890123"),
        (Symbology.ADDON2, "12"),
        (Symbology.ADDON5, "12345"),
        (Symbology.CODE93, "CODE 93-./$+%"),
        (Symbology.POSTNET, "32118"),
        (Symbology.POSTNET, "321181234"),
        (Symbology.POSTNET, "32118123456"),
        (Symbology.UCC_EAN128, "0" * 19),
        (Symbology.UCC_EAN128_KMART, "1" * 18),
        (Symbology.UCC_EAN128_RANDOM_WEIGHT, "2" * 34),
        (Symbology.UCC_EAN128_RANDOM_WEIGHT, "2" * 60),
        (Symbology.TELEPEN, "abc" + chr(1) + chr(127)),
        (Symbology.MAXICODE, "x" * 84),
        (Symbology.FIM, "C"),
        (Symbology.PDF417, "PDF417 data " + chr(0xE4)),
        (Symbology.DATAMATRIX, "".join(chr(n) for n in range(1, 13)) + chr(0xFF)),
    ],
)
def test_accepts_valid_data(symbology: Symbology, data: str) -> None:
    """Data within the documented set and length passes, unchanged."""
    check = check_barcode(symbology, data)
    assert check.ok, check.problems
    assert check.data == data


@pytest.mark.os_agnostic
def test_upc_a_ten_digits_is_bad_length() -> None:
    """The manual's 10-digit UPC-A sample is one digit short of the 11 the symbology needs."""
    check = check_barcode(Symbology.UPC_A, "1234567890")
    assert _codes(check) == [BarcodeProblemCode.BAD_LENGTH]
    assert check.computed_check_digit is None


@pytest.mark.os_agnostic
def test_ean13_twelve_digits_reports_the_digit_the_printer_adds() -> None:
    """Without a check digit the data stays as given and the computed digit is reported."""
    check = check_barcode(Symbology.EAN13, "012345678901")
    assert check.ok
    assert check.data == "012345678901"
    assert check.computed_check_digit == "2"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("symbology", "data", "expected"),
    [
        (Symbology.UPC_A, "036000291453", "2"),
        (Symbology.EAN13, "0123456789019", "2"),
        (Symbology.EAN8, "96385070", "4"),
        (Symbology.UPC_E, "4252619", "4"),
    ],
)
def test_wrong_included_check_digit_names_the_expected_one(symbology: Symbology, data: str, expected: str) -> None:
    """A wrong included check digit is a problem that names the right digit."""
    check = check_barcode(symbology, data)
    assert _codes(check) == [BarcodeProblemCode.BAD_CHECK_DIGIT]
    assert f"expected {expected}" in check.problems[0].message
    assert check.computed_check_digit == expected


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("body", "upc_a_equivalent"),
    [
        ("425261", "04210000526"),
        ("123453", "01230000045"),
        ("123414", "01234000001"),
        ("123457", "01234500007"),
    ],
)
def test_upc_e_check_digit_comes_from_the_expanded_upc_a(body: str, upc_a_equivalent: str) -> None:
    """UPC-E's check digit is the one of the UPC-A it expands to (number system 0)."""
    check = check_barcode(Symbology.UPC_E, body)
    assert check.computed_check_digit == gs1_mod10_check_digit(upc_a_equivalent)


# ======================== price marker ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("symbology", "data"),
    [
        (Symbology.UPC_A, "12345V78901"),
        (Symbology.UPC_A, "123456V8901"),
        (Symbology.UPC_A, "123456V89012"),
        (Symbology.EAN13, "123456V89012"),
        (Symbology.EAN13, "1234567V9012"),
    ],
)
def test_price_marker_in_documented_position_is_accepted(symbology: Symbology, data: str) -> None:
    """A V at the documented price position passes; the overall check digit is left to the printer."""
    check = check_barcode(symbology, data)
    assert check.ok, check.problems
    assert check.computed_check_digit is None


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("symbology", "data", "code"),
    [
        (Symbology.UPC_A, "1234V678901", BarcodeProblemCode.BAD_PRICE_MARKER),
        (Symbology.UPC_A, "12345VV8901", BarcodeProblemCode.BAD_PRICE_MARKER),
        (Symbology.EAN13, "12345V789012", BarcodeProblemCode.BAD_PRICE_MARKER),
        (Symbology.EAN8, "123V567", BarcodeProblemCode.BAD_CHARACTER),
        (Symbology.UPC_E, "12V456", BarcodeProblemCode.BAD_CHARACTER),
    ],
)
def test_price_marker_elsewhere_is_a_problem(symbology: Symbology, data: str, code: BarcodeProblemCode) -> None:
    """A V outside the price position, twice, or in a symbology without one is refused."""
    assert code in _codes(check_barcode(symbology, data))


# ======================== symbology-specific rules ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("symbology", "data", "code"),
    [
        (Symbology.CODE39, "abc", BarcodeProblemCode.BAD_CHARACTER),
        (Symbology.CODE93, "*", BarcodeProblemCode.BAD_CHARACTER),
        (Symbology.CODE128, "A&", BarcodeProblemCode.BAD_ESCAPE),
        (Symbology.CODE128, "A&H", BarcodeProblemCode.BAD_ESCAPE),
        (Symbology.CODE128, "&&A", BarcodeProblemCode.BAD_ESCAPE),
        (Symbology.CODE128, "tab\there", BarcodeProblemCode.BAD_CHARACTER),
        (Symbology.CODABAR, "12345", BarcodeProblemCode.BAD_START_STOP),
        (Symbology.CODABAR, "A123", BarcodeProblemCode.BAD_START_STOP),
        (Symbology.CODABAR, "A12C34B", BarcodeProblemCode.BAD_CHARACTER),
        (Symbology.CODABAR, "A", BarcodeProblemCode.BAD_LENGTH),
        (Symbology.HIBC, "A123", BarcodeProblemCode.MISSING_PREFIX),
        (Symbology.HIBC, "+a1", BarcodeProblemCode.BAD_CHARACTER),
        (Symbology.POSTNET, "123456", BarcodeProblemCode.BAD_LENGTH),
        (Symbology.PLESSEY, "1" * 15, BarcodeProblemCode.BAD_LENGTH),
        (Symbology.I2OF5_BEARER, "123456789012", BarcodeProblemCode.BAD_LENGTH),
        (Symbology.ADDON2, "123", BarcodeProblemCode.BAD_LENGTH),
        (Symbology.ADDON5, "1234", BarcodeProblemCode.BAD_LENGTH),
        (Symbology.UCC_EAN128, "1" * 18, BarcodeProblemCode.BAD_LENGTH),
        (Symbology.UCC_EAN128_KMART, "1" * 19, BarcodeProblemCode.BAD_LENGTH),
        (Symbology.UCC_EAN128_RANDOM_WEIGHT, "1" * 33, BarcodeProblemCode.BAD_LENGTH),
        (Symbology.TELEPEN, "x" + chr(128), BarcodeProblemCode.BAD_CHARACTER),
        (Symbology.FIM, "E", BarcodeProblemCode.BAD_CHARACTER),
        (Symbology.FIM, "AB", BarcodeProblemCode.BAD_LENGTH),
        (Symbology.MAXICODE, "x" * 85, BarcodeProblemCode.BAD_LENGTH),
        (Symbology.MAXICODE, chr(0xE9), BarcodeProblemCode.BAD_CHARACTER),
        (Symbology.I2OF5, "12a", BarcodeProblemCode.BAD_CHARACTER),
        (Symbology.I2OF5_MOD10, "1 2", BarcodeProblemCode.BAD_CHARACTER),
        (Symbology.DATAMATRIX, "x" + chr(0x100), BarcodeProblemCode.BAD_CHARACTER),
        (Symbology.PDF417, chr(0x20AC), BarcodeProblemCode.BAD_CHARACTER),
    ],
)
def test_rule_violations_are_reported(symbology: Symbology, data: str, code: BarcodeProblemCode) -> None:
    """Each documented rule produces its own problem code."""
    assert code in _codes(check_barcode(symbology, data))


@pytest.mark.os_agnostic
def test_code128_escapes_are_consumed_in_pairs() -> None:
    """``&&G`` is a lone ``&`` followed by the ``&G`` escape: only position 1 is reported."""
    check = check_barcode(Symbology.CODE128, "&&G")
    assert _codes(check) == [BarcodeProblemCode.BAD_ESCAPE]
    assert "position 1 " in check.problems[0].message


@pytest.mark.os_agnostic
def test_plain_interleaved_two_of_five_accepts_an_odd_digit_count() -> None:
    """The spec gives no even-count rule for plain I 2 of 5, so none is enforced."""
    assert check_barcode(Symbology.I2OF5, "12345").ok


# ======================== record framing ========================


@pytest.mark.os_agnostic
def test_empty_data_is_one_problem() -> None:
    """Empty data is reported once, with no other findings."""
    check = check_barcode(Symbology.CODE128, "")
    assert _codes(check) == [BarcodeProblemCode.EMPTY]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("symbology", [Symbology.UPC_A, Symbology.POSTNET, Symbology.CODE39])
def test_whitespace_only_is_not_digits(symbology: Symbology) -> None:
    """Data that is empty after stripping is still checked as given, not silently stripped."""
    check = check_barcode(symbology, "   ")
    assert check.data == "   "
    if symbology is Symbology.CODE39:
        assert check.ok
    else:
        assert BarcodeProblemCode.BAD_CHARACTER in _codes(check)


@pytest.mark.os_agnostic
def test_carriage_return_ends_the_record_early() -> None:
    """A CR inside the data would end the DPL record, even where the character set allows it."""
    assert BarcodeProblemCode.RECORD_TERMINATOR in _codes(check_barcode(Symbology.TELEPEN, "AB\rCD"))


@pytest.mark.os_agnostic
def test_record_longer_than_255_characters_is_too_long() -> None:
    """One-dimensional data is bounded by the record limit."""
    assert _codes(check_barcode(Symbology.CODE128, "A" * 256)) == [BarcodeProblemCode.TOO_LONG]
    assert check_barcode(Symbology.CODE128, "A" * 255).ok


@pytest.mark.os_agnostic
def test_pdf417_limit_counts_the_parameter_header() -> None:
    """PDF417 takes 3000 characters including its 8-character parameter header."""
    assert check_barcode(Symbology.PDF417, "x" * 2992).ok
    assert _codes(check_barcode(Symbology.PDF417, "x" * 2993)) == [BarcodeProblemCode.TOO_LONG]


@pytest.mark.os_agnostic
def test_datamatrix_limit_counts_the_parameter_header() -> None:
    """DataMatrix stays within the 255-character record including its 10-character header."""
    options = DataMatrixOptions(ecc=140)
    assert check_barcode(Symbology.DATAMATRIX, "x" * 245, datamatrix=options).ok
    assert _codes(check_barcode(Symbology.DATAMATRIX, "x" * 246, datamatrix=options)) == [BarcodeProblemCode.TOO_LONG]


# ======================== adversarial inputs ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "symbology",
    [Symbology.UPC_A, Symbology.EAN13, Symbology.EAN8, Symbology.UPC_E, Symbology.POSTNET, Symbology.PLESSEY],
)
def test_non_ascii_digits_are_refused(symbology: Symbology) -> None:
    """Unicode digits such as U+0662 are digits to str.isdigit but not to the printer."""
    data = "1" * 5 + ARABIC_INDIC_TWO
    check = check_barcode(symbology, data)
    assert BarcodeProblemCode.BAD_CHARACTER in _codes(check)
    assert "U+0662" in " ".join(p.message for p in check.problems)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("symbology", [Symbology.CODE39, Symbology.CODE128, Symbology.EAN13])
def test_nul_is_refused_and_rendered_as_a_code_point(symbology: Symbology) -> None:
    """NUL never reaches a message raw; it is named by code point."""
    check = check_barcode(symbology, "12" + NUL + "3")
    assert BarcodeProblemCode.BAD_CHARACTER in _codes(check)
    messages = " ".join(p.message for p in check.problems)
    assert "U+0000" in messages
    assert NUL not in messages


@pytest.mark.os_agnostic
def test_very_long_input_keeps_messages_short() -> None:
    """A huge input with many distinct bad characters yields a bounded message."""
    data = "".join(chr(0x4E00 + n) for n in range(5000)) * 20
    check = check_barcode(Symbology.CODE39, data)
    assert set(_codes(check)) == {BarcodeProblemCode.TOO_LONG, BarcodeProblemCode.BAD_CHARACTER}
    assert all(len(p.message) < 300 for p in check.problems)
    assert "and 4990 more" in " ".join(p.message for p in check.problems)


@pytest.mark.os_agnostic
@given(symbology=st.sampled_from(list(Symbology)), data=st.text(max_size=400))
def test_check_barcode_never_raises(symbology: Symbology, data: str) -> None:
    """Every symbology answers every string with a result, never an exception."""
    check = check_barcode(symbology, data)
    assert check.data == data
    assert check.ok == (not check.problems)


# ======================== GS1 modulo 10 ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("digits", "expected"),
    [("03600029145", "2"), ("012345678901", "2"), ("9638507", "4"), ("0", "0"), ("1", "7")],
)
def test_gs1_mod10_known_values(digits: str, expected: str) -> None:
    """Published UPC/EAN samples give their printed check digit."""
    assert gs1_mod10_check_digit(digits) == expected


@pytest.mark.os_agnostic
@pytest.mark.parametrize("digits", ["", "12a", "1" + ARABIC_INDIC_TWO, " 1"])
def test_gs1_mod10_refuses_non_digits(digits: str) -> None:
    """Only ASCII digits can carry a check digit."""
    with pytest.raises(ValueError, match="ASCII digits"):
        gs1_mod10_check_digit(digits)


_BODIES = st.text(alphabet="0123456789", min_size=12, max_size=12)


@pytest.mark.os_agnostic
@given(body=_BODIES)
def test_appending_the_computed_digit_always_validates(body: str) -> None:
    """EAN-13 data ending in its computed check digit always passes."""
    check = check_barcode(Symbology.EAN13, body + gs1_mod10_check_digit(body))
    assert check.ok


@pytest.mark.os_agnostic
@given(body=_BODIES, position=st.integers(min_value=0, max_value=12), shift=st.integers(min_value=1, max_value=9))
def test_changing_one_digit_always_fails(body: str, position: int, shift: int) -> None:
    """Mod 10 with weights 1 and 3 catches every single-digit substitution."""
    full = body + gs1_mod10_check_digit(body)
    changed_digit = str((int(full[position]) + shift) % 10)
    tampered = full[:position] + changed_digit + full[position + 1 :]
    assert _codes(check_barcode(Symbology.EAN13, tampered)) == [BarcodeProblemCode.BAD_CHECK_DIGIT]


# ======================== 2-D data headers ========================


@pytest.mark.os_agnostic
def test_pdf417_data_field_layout() -> None:
    """``T``/``F``, one security digit, then aspect, rows and columns as two digits each."""
    options = Pdf417Options(truncated=True, security_level=8, aspect_ratio=5, rows=90, columns=30)
    assert pdf417_data_field("DATA", options) == "T8059030DATA"
    assert pdf417_data_field("", Pdf417Options()) == "F2000000"


@pytest.mark.os_agnostic
def test_datamatrix_data_field_layout() -> None:
    """ECC as three digits, format id one digit, rows and columns three digits each."""
    options = DataMatrixOptions(ecc=50, format_id=6, rows=144, columns=12)
    assert datamatrix_data_field("X", options) == "0506144012X"
