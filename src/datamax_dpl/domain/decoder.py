"""Read a DPL byte stream back: explain each command, and rebuild the label where possible.

``datamax-dpl explain`` uses this to show what an existing ``.dpl`` file does, and the tests use
it to round-trip the encoder. The decoder knows the commands in ``docs/dpl-reference.md``; any
other command is reported as ``unknown`` rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .enums import FillPattern, Rotation, SpeedCode, Unit
from .label import Barcode, Box, Circle, Element, Image, Label, Line, Point, Polygon, ScalableText, Text
from .profiles import OLD_SERIES_POINTS
from .records import multiplier_value
from .symbology import SYMBOLOGY_BY_IDENTIFIER

Kind = Literal["immediate", "system", "download", "format", "record", "counter", "end", "unknown"]

_SOH, _STX, _CR = 0x01, 0x02, 0x0D
_PRINTABLE = range(32, 127)
#: A record header is 15 characters: a b c d eee ffff gggg.
_HEADER_LENGTH = 15
_SCALABLE_SIZE_PREFIXES = "SsUu"
_FONT_9 = 9
_PRESENT_DISTANCE_DIGITS = 4
#: Parameter length of fixed-width system commands (``<STX>`` + letter + digits, no ``<CR>``).
_SYSTEM_WIDTHS = {"c": 4, "f": 3, "M": 4, "O": 4, "E": 4, "b": 4, "H": 4, "S": 1, "V": 1, "R": 1, "X": 1, "q": 1}
_SYSTEM_NAMES = {
    "L": "enter label formatting",
    "n": "inch mode",
    "m": "metric mode",
    "e": "gap sensor",
    "r": "reflective sensor",
    "o": "cut now",
    "J": "pause after each label",
    "F": "form feed",
    "c": "continuous label length",
    "f": "stop position (backfeed)",
    "M": "maximum label length",
    "O": "start of print position",
    "E": "quantity for stored label",
    "G": "print last format",
    "v": "firmware version request",
    "k": "serial port test",
}
_IMMEDIATE_NAMES = {
    "#": "reset",
    "A": "status request",
    "B": "toggle pause",
    "C": "cancel",
    "E": "batch request",
    "F": "status byte request",
}
_FORMAT_NAMES = {
    "D": "dot size",
    "H": "heat",
    "P": "print speed",
    "S": "slew speed",
    "p": "backup speed",
    "C": "column offset",
    "R": "row offset",
    "A": "attribute",
    "M": "mirror",
    "m": "metric",
    "n": "inch",
    "Q": "quantity",
    "z": "plain zero",
    "^": "count by",
    ":": "cut by",
    "c": "cut by",
    "y": "symbol set",
    "T": "field terminator",
    "U": "replaceable field",
    "G": "to global register",
}


class DecodedCommand(BaseModel):
    """One command of the stream, with its bytes shown readably (``<STX>``, ``<CR>``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Kind
    raw: str
    meaning: str


class Explanation(BaseModel):
    """What a DPL stream does.

    Attributes:
        commands: Every command in order.
        label: The rebuilt label when the stream holds exactly one format, else None.
        problems: Records that could not be rebuilt, and why.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    commands: tuple[DecodedCommand, ...]
    label: Label | None
    problems: tuple[str, ...]


def readable(data: bytes) -> str:
    """Show control bytes by name.

    Example:
        >>> readable(b"\\x02L\\r")
        '<STX>L<CR>'
    """
    names = {_SOH: "<SOH>", _STX: "<STX>", _CR: "<CR>", 0x1B: "<ESC>"}
    return "".join(names.get(b, chr(b) if b in _PRINTABLE else f"<{b:02X}>") for b in data)


@dataclass
class _State:
    commands: list[DecodedCommand] = field(default_factory=list[DecodedCommand])
    elements: list[Element] = field(default_factory=list[Element])
    settings: dict[str, object] = field(default_factory=dict[str, object])
    problems: list[str] = field(default_factory=list[str])
    formats: int = 0
    metric: bool = False


def _add(state: _State, kind: Kind, raw: bytes, meaning: str) -> None:
    state.commands.append(DecodedCommand(kind=kind, raw=readable(raw), meaning=meaning))


def _until_cr(data: bytes, start: int) -> int:
    end = data.find(b"\r", start)
    return len(data) if end < 0 else end + 1


def _system(data: bytes, pos: int, state: _State) -> int:
    letter = chr(data[pos + 1]) if pos + 1 < len(data) else ""
    if letter == "I":
        end = data.find(b"FFFF\r", pos)
        end = len(data) if end < 0 else end + len(b"FFFF\r")
        _add(state, "download", data[pos : _until_cr(data, pos)], "image download")
        return end
    if letter == "K" and data[pos + 2 : pos + 3] == b"f":
        end = pos + 3 + _PRESENT_DISTANCE_DIGITS
        _add(state, "system", data[pos:end], "present distance")
        return end
    if letter == "K":
        end = _until_cr(data, pos)
        _add(state, "system", data[pos:end], "extended command")
        return end
    end = pos + 2 + _SYSTEM_WIDTHS.get(letter, 0)
    _add(state, "system", data[pos:end], _SYSTEM_NAMES.get(letter, f"system command {letter}"))
    if letter == "m":
        state.metric = True
    return end


def _scan(data: bytes, state: _State) -> None:
    pos = 0
    while pos < len(data):
        byte = data[pos]
        if byte == _SOH:
            letter = chr(data[pos + 1]) if pos + 1 < len(data) else ""
            _add(state, "immediate", data[pos : pos + 2], _IMMEDIATE_NAMES.get(letter, "immediate command"))
            pos += 2
        elif byte == _STX and data[pos + 1 : pos + 2] == b"L":
            _add(state, "format", data[pos : pos + 2], _SYSTEM_NAMES["L"])
            pos = _format(data, _until_cr(data, pos), state)
        elif byte == _STX:
            pos = _system(data, pos, state)
        else:
            end = _until_cr(data, pos)
            _add(state, "unknown", data[pos:end], "outside any command")
            pos = end


def _format(data: bytes, pos: int, state: _State) -> int:
    state.formats += 1
    while pos < len(data):
        end = _until_cr(data, pos)
        text = data[pos:end].rstrip(b"\r")
        pos = end
        if text in {b"E", b"X"} or text[:1] == b"s":
            _add(state, "end", text, {b"E": "end and print", b"X": "end without printing"}.get(text, "store format"))
            return pos
        _format_line(text, state)
    return pos


def _format_line(text: bytes, state: _State) -> None:
    first = chr(text[0]) if text else ""
    if first in "1234" and len(text) >= _HEADER_LENGTH:
        _add(state, "record", text, _record(text, state))
    elif first in "+-<>":
        _add(state, "counter", text, "count the previous field")
    else:
        _add(state, "format", text, _FORMAT_NAMES.get(first, "unknown format command"))
        _setting(first, text.decode("latin-1"), state)


def _setting(first: str, text: str, state: _State) -> None:
    if first == "m":
        state.metric = True
    elif first == "Q":
        state.settings["quantity"] = int(text[1:5] or "1")
    elif first == "H":
        state.settings["heat"] = int(text[1:3])
    elif first in "PS" and len(text[1:]) == 1:
        state.settings["print_speed" if first == "P" else "slew_speed"] = SpeedCode(text[1])
    elif first == ":":
        state.settings["cut"] = {"every": int(text[1:5])}


def _scale(value: int, state: _State) -> float:
    return value / 10 if state.metric else value / 100


def _record(text: bytes, state: _State) -> str:
    try:
        element, meaning = _parse_record(text.decode("latin-1"), state)
    except (ValueError, KeyError, ValidationError) as exc:
        state.problems.append(f"{readable(text)}: {exc.__class__.__name__}")
        return "record (not understood)"
    if element is not None:
        state.elements.append(element)
    return meaning


def _parse_record(text: str, state: _State) -> tuple[Element | None, str]:
    kind = text[1]
    if kind == "X":
        return _graphic(text, state)
    if kind == "Y":
        y, x = _scale(int(text[7:11]), state), _scale(int(text[11:15]), state)
        return Image(name=text[15:], x=x, y=y), "image"
    if kind == "W":
        return _barcode(text, state, identifier="W1c", offset=2), "barcode datamatrix"
    if kind.isdigit():
        return _text(text, state), f"text font {kind}"
    return _barcode(text, state, identifier=kind, offset=0), f"barcode {SYMBOLOGY_BY_IDENTIFIER[kind].value}"


@dataclass(frozen=True)
class _Head:
    rotation: Rotation
    width: int
    height: int
    size: str
    row: int
    column: int


def _head(text: str, offset: int = 0) -> _Head:
    c, d = text[2 + offset], text[3 + offset]
    return _Head(
        rotation=Rotation.from_dpl_digit(int(text[0])),
        width=multiplier_value(c),
        height=multiplier_value(d),
        size=text[4 + offset : 7 + offset],
        row=int(text[7 + offset : 11 + offset]),
        column=int(text[11 + offset : 15 + offset]),
    )


def _text(text: str, state: _State) -> Element:
    head = _head(text)
    font = int(text[1])
    x, y = _scale(head.column, state), _scale(head.row, state)
    if font == _FONT_9 and head.size[0] in _SCALABLE_SIZE_PREFIXES:
        points = int(text[16:19])
        return ScalableText(text=text[23:], font_id=head.size, height_points=points, x=x, y=y, rotation=head.rotation)
    data = text[15:].encode("latin-1").decode("cp437")
    points, font_id = _font9_size(head.size) if font == _FONT_9 else (None, None)
    return Text(
        text=data,
        font=font,
        width_multiplier=head.width,
        height_multiplier=head.height,
        points=points,
        font_id=font_id,
        x=x,
        y=y,
        rotation=head.rotation,
    )


def _font9_size(code: str) -> tuple[int | None, str | None]:
    """Return (points, font_id) for a font 9 size code."""
    if code[0] == "A" and code[1:].isdigit():
        return int(code[1:]), None
    if code.isdigit() and int(code) < len(OLD_SERIES_POINTS):
        return OLD_SERIES_POINTS[int(code)], None
    return None, code


def _barcode(text: str, state: _State, *, identifier: str, offset: int) -> Element:
    head = _head(text, offset)
    symbology = SYMBOLOGY_BY_IDENTIFIER[identifier]
    height = None if head.size == "000" else _scale(int(head.size), state)
    return Barcode(
        symbology=symbology,
        data=text[15 + offset :],
        x=_scale(head.column, state),
        y=_scale(head.row, state),
        rotation=head.rotation,
        height=height,
        narrow_dots=head.height,
        wide_dots=head.width,
        human_readable=identifier.isupper(),
    )


def _graphic(text: str, state: _State) -> tuple[Element, str]:
    fill = FillPattern(int(text[4:7]))
    row, column = int(text[7:11]), int(text[11:15])
    shape, rest = text[15], text[16:]
    x, y = _scale(column, state), _scale(row, state)
    if shape in "Ll":
        width = 3 if shape == "L" else 4
        h, v = (_scale(int(rest[i : i + width]), state) for i in (0, width))
        horizontal = h >= v
        x2, y2 = (x + h, y) if horizontal else (x, y + v)
        return Line(x=x, y=y, x2=x2, y2=y2, thickness=v if horizontal else h), "line"
    if shape in "Bb":
        width = 3 if shape == "B" else 4
        w, h, t, s = (_scale(int(rest[i * width : (i + 1) * width]), state) for i in range(4))
        return Box(x=x, y=y, width=w, height=h, thickness=t, side_thickness=s), "box"
    if shape == "C":
        return Circle(x=x, y=y, radius=_scale(int(rest[7:11]), state), fill=fill), "circle"
    coords = rest[7:]
    points = [Point(x=x, y=y)] + [
        Point(x=_scale(int(coords[i + 4 : i + 8]), state), y=_scale(int(coords[i : i + 4]), state))
        for i in range(0, len(coords) - 7, 8)
    ]
    return Polygon(points=points, fill=fill), "polygon"


def _rebuild(state: _State) -> Label | None:
    if state.formats != 1 or not state.elements:
        return None
    try:
        document: dict[str, object] = {
            "unit": Unit.MM if state.metric else Unit.INCH,
            "elements": [element.model_dump() for element in state.elements],
            **state.settings,
        }
        return Label.model_validate(document)
    except ValidationError as exc:
        state.problems.append(f"label: {exc.error_count()} validation errors")
        return None


def explain(data: bytes) -> Explanation:
    """Explain every command in ``data`` and rebuild the label when there is exactly one format.

    Example:
        >>> result = explain(b"\\x02L\\r121100000500025Hi\\rE\\r")
        >>> [c.kind for c in result.commands]
        ['format', 'record', 'end']
        >>> result.label.elements[0].text
        'Hi'
    """
    state = _State()
    _scan(data, state)
    return Explanation(commands=tuple(state.commands), label=_rebuild(state), problems=tuple(state.problems))


__all__ = ["DecodedCommand", "Explanation", "explain", "readable"]
