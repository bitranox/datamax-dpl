"""Text to font bytes: the bitmapped font subsets of code page 437 and the scalable symbol sets."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from datamax_dpl.domain.charset import SYMBOL_SET_CODECS, encode_for_bitmapped_font, encode_for_symbol_set

A_UMLAUT = chr(0x00E4)
O_UMLAUT = chr(0x00F6)
U_UMLAUT = chr(0x00FC)
CAPITAL_A_UMLAUT = chr(0x00C4)
CAPITAL_O_UMLAUT = chr(0x00D6)
CAPITAL_U_UMLAUT = chr(0x00DC)
SHARP_S = chr(0x00DF)
EURO = chr(0x20AC)
E_ACUTE = chr(0x00E9)
CAPITAL_E_ACUTE = chr(0x00C9)

GERMAN_BYTES = {
    A_UMLAUT: 132,
    O_UMLAUT: 148,
    U_UMLAUT: 129,
    CAPITAL_A_UMLAUT: 142,
    CAPITAL_O_UMLAUT: 153,
    CAPITAL_U_UMLAUT: 154,
    SHARP_S: 225,
}

ALL_FONTS = list(range(10))


@pytest.mark.os_agnostic
@pytest.mark.parametrize("font", [1, 2, 9])
@pytest.mark.parametrize(("character", "expected"), list(GERMAN_BYTES.items()))
def test_german_letters_map_to_code_page_437(font: int, character: str, expected: int) -> None:
    """Fonts 1, 2 and 9 print umlauts and sharp s at their documented code page 437 bytes."""
    encoded = encode_for_bitmapped_font(character, font=font)
    assert encoded.data == bytes([expected])
    assert encoded.unmappable == ()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("font", ALL_FONTS)
def test_euro_sign_is_unmappable_in_every_bitmapped_font(font: int) -> None:
    """Code page 437 has no euro sign, so no bitmapped font prints it."""
    encoded = encode_for_bitmapped_font("1" + EURO, font=font)
    assert encoded.unmappable == (EURO,)
    assert encoded.data.endswith(b"?")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("font", [3, 4, 5, 6])
def test_capital_fonts_print_lower_case_as_capitals(font: int) -> None:
    """Fonts 3-6 have capitals only; lower case maps to the capital, sharp s stays itself."""
    text = "abc " + A_UMLAUT + O_UMLAUT + U_UMLAUT + SHARP_S + E_ACUTE
    encoded = encode_for_bitmapped_font(text, font=font)
    assert encoded.data == b"ABC " + bytes([142, 153, 154, 225, 144])
    assert encoded.unmappable == ()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("font", [3, 4, 5, 6])
def test_capital_fonts_lack_some_punctuation(font: int) -> None:
    """The capitals set has no ``!``, ``"`` or ``'``, and y-diaeresis has no capital in it."""
    y_diaeresis = chr(0x00FF)
    encoded = encode_for_bitmapped_font("A!\"'" + y_diaeresis, font=font)
    assert encoded.data == b"A????"
    assert encoded.unmappable == ("!", '"', "'", y_diaeresis)


@pytest.mark.os_agnostic
def test_font_zero_and_seven_differ_only_in_del() -> None:
    """Font 0 covers ASCII 32-127, font 7 (OCR-A) stops at 126."""
    assert encode_for_bitmapped_font(chr(127), font=0).unmappable == ()
    assert encode_for_bitmapped_font(chr(127), font=7).unmappable == (chr(127),)
    assert encode_for_bitmapped_font("~ abc", font=7).data == b"~ abc"


@pytest.mark.os_agnostic
def test_font_eight_is_digits_and_a_few_symbols() -> None:
    """OCR-B font 8 prints digits, space and ``< > C E N S T X Z`` only."""
    encoded = encode_for_bitmapped_font("0123456789 <>CENSTXZ", font=8)
    assert encoded.unmappable == ()
    rejected = encode_for_bitmapped_font("ABc", font=8)
    assert rejected.data == b"???"
    assert rejected.unmappable == ("A", "B", "c")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("font", [1, 2])
def test_fonts_one_and_two_stop_at_168_plus_three_extras(font: int) -> None:
    """Byte 169 is outside fonts 1 and 2, bytes 171, 172 and 225 are inside."""
    reversed_not = bytes([169]).decode("cp437")
    one_half = bytes([171]).decode("cp437")
    assert encode_for_bitmapped_font(reversed_not, font=font).unmappable == (reversed_not,)
    assert encode_for_bitmapped_font(one_half, font=font).data == bytes([171])


@pytest.mark.os_agnostic
def test_font_nine_has_its_documented_gaps() -> None:
    """Font 9 prints byte 169 but not 170 or 180."""
    printable = bytes([169]).decode("cp437")
    gaps = bytes([170, 180]).decode("cp437")
    assert encode_for_bitmapped_font(printable, font=9).unmappable == ()
    assert encode_for_bitmapped_font(gaps, font=9).unmappable == tuple(gaps)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("font", ALL_FONTS)
@pytest.mark.parametrize("control", [chr(0), chr(2), "\t", "\r", "\n", chr(27)])
def test_control_characters_are_never_printed(font: int, control: str) -> None:
    """Control characters would be read as protocol framing, so they never pass through."""
    encoded = encode_for_bitmapped_font(control, font=font)
    assert encoded.data == b"?"
    assert encoded.unmappable == (control,)


@pytest.mark.os_agnostic
def test_unmappable_characters_are_listed_once_in_first_seen_order() -> None:
    """Repeats collapse; order follows the text."""
    text = EURO + "a" + chr(0x0662) + EURO + EURO
    encoded = encode_for_bitmapped_font(text, font=1)
    assert encoded.unmappable == (EURO, chr(0x0662))
    assert encoded.data == b"?a???"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("font", [-1, 10, 99])
def test_unknown_font_is_refused(font: int) -> None:
    """Only fonts 0-9 are bitmapped fonts."""
    with pytest.raises(ValueError, match="0-9"):
        encode_for_bitmapped_font("A", font=font)


@pytest.mark.os_agnostic
@given(text=st.text(max_size=200), font=st.sampled_from(ALL_FONTS))
def test_one_byte_per_character_for_any_text(text: str, font: int) -> None:
    """Every input character becomes exactly one byte."""
    encoded = encode_for_bitmapped_font(text, font=font)
    assert len(encoded.data) == len(text)
    assert set(encoded.unmappable) <= set(text)


# ======================== symbol sets ========================


@pytest.mark.os_agnostic
def test_no_symbol_set_means_code_page_437() -> None:
    """A scalable font without ``ySxx`` prints through PC (code page 437)."""
    text = "".join(GERMAN_BYTES)
    assert encode_for_symbol_set(text, symbol_set=None).data == bytes(GERMAN_BYTES.values())
    assert encode_for_symbol_set(text, symbol_set="PC").data == bytes(GERMAN_BYTES.values())


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("symbol_set", "expected"),
    [("W1", 0xE4), ("E1", 0xE4), ("PM", 0x84), ("PE", 0x84), ("E2", 0xE4), ("WE", 0xE4)],
)
def test_a_umlaut_per_symbol_set(symbol_set: str, expected: int) -> None:
    """The same letter lands on the byte its symbol set assigns."""
    assert encode_for_symbol_set(A_UMLAUT, symbol_set=symbol_set).data == bytes([expected])


@pytest.mark.os_agnostic
def test_euro_is_mappable_only_where_the_codec_has_it() -> None:
    """Windows Latin 1 has the euro at 0x80; ISO 8859-1 has none."""
    assert encode_for_symbol_set(EURO, symbol_set="W1").data == b"\x80"
    assert encode_for_symbol_set(EURO, symbol_set="E1").unmappable == (EURO,)


@pytest.mark.os_agnostic
def test_e5_is_latin_5_turkish() -> None:
    """E5 is ISO 8859-9 (Latin 5): capital I with dot above is 0xDD there."""
    assert encode_for_symbol_set(chr(0x0130), symbol_set="E5").data == b"\xdd"


@pytest.mark.os_agnostic
def test_ascii_symbol_set_refuses_accents() -> None:
    """US (ISO 6) is plain ASCII."""
    encoded = encode_for_symbol_set("e" + CAPITAL_E_ACUTE, symbol_set="US")
    assert encoded.data == b"e?"
    assert encoded.unmappable == (CAPITAL_E_ACUTE,)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("symbol_set", sorted(SYMBOL_SET_CODECS))
def test_control_characters_are_unmappable_in_every_symbol_set(symbol_set: str) -> None:
    """A CR or STX in text would break the record, whichever symbol set is chosen."""
    assert encode_for_symbol_set("\r" + chr(2), symbol_set=symbol_set).unmappable == ("\r", chr(2))


@pytest.mark.os_agnostic
@pytest.mark.parametrize("symbol_set", ["XX", "", "pc", "Z" * 1000])
def test_unknown_symbol_set_is_refused(symbol_set: str) -> None:
    """An id without an exact codec is refused, and a long id is not echoed in full."""
    with pytest.raises(ValueError, match="no exact codec") as caught:
        encode_for_symbol_set("A", symbol_set=symbol_set)
    assert len(str(caught.value)) < 200


@pytest.mark.os_agnostic
@given(text=st.text(max_size=200), symbol_set=st.sampled_from(sorted(SYMBOL_SET_CODECS)))
def test_symbol_set_round_trips_what_it_maps(text: str, symbol_set: str) -> None:
    """Every byte that is not a replacement decodes back to its source character."""
    encoded = encode_for_symbol_set(text, symbol_set=symbol_set)
    codec = SYMBOL_SET_CODECS[symbol_set]
    assert len(encoded.data) == len(text)
    for ch, byte in zip(text, encoded.data, strict=True):
        if ch not in encoded.unmappable:
            assert bytes([byte]).decode(codec) == ch
