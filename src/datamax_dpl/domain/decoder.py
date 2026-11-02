"""Read a DPL byte stream back: explain each command, and rebuild the label where possible.

``datamax-dpl explain`` uses this to show what an existing ``.dpl`` file does, and the tests use
it to round-trip the encoder. The decoder knows the commands in ``docs/dpl-reference.md``; any
other command is reported as ``unknown`` rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, ValidationError

from ._invariants import require_update_fields
from .enums import CommandKind, CounterSign, FillPattern, Rotation, SpeedCode, Unit
from .label import Barcode, Box, Circle, Counter, Cut, Element, Image, Label, Line, Point, Polygon, ScalableText, Text
from .profiles import OLD_SERIES_POINTS
from .records import MAX_IMAGE_MAGNIFICATION, multiplier_value
from .symbology import SYMBOLOGY_BY_IDENTIFIER

_SOH, _STX, _CR = 0x01, 0x02, 0x0D
_PRINTABLE = range(32, 127)
#: Most commands one result lists. A sanity limit, so no settings key: a command takes about 1 KB of
#: result and can be a single byte of input, so the 16 MiB a file may hold would come to 15 GB. This
#: is a batch of about 1000 labels, more than anyone reads through.
MAX_EXPLAINED_COMMANDS = 100_000
#: A record header is 15 characters: a b c d eee ffff gggg.
_HEADER_LENGTH = 15


#: The first character of a counter line; the encoder's ``counter_lines`` writes the same signs.
_COUNTER_SIGNS = frozenset(sign.value for sign in CounterSign)
#: The first character of a record line: its rotation digit.
_RECORD_DIGITS = frozenset(str(rotation.dpl_digit) for rotation in Rotation)


class _CounterUpdate(TypedDict):
    """The field a counter line sets on the text or barcode before it; a key of both models."""

    counter: Counter


require_update_fields(_CounterUpdate, Text, Barcode)
_SCALABLE_SIZE_PREFIXES = "SsUu"
_FONT_9 = 9
_PRESENT_DISTANCE_DIGITS = 4
#: Parameter length of fixed-width system commands (``<STX>`` + letter + digits, no ``<CR>``).
_SYSTEM_WIDTHS = {"c": 4, "f": 3, "M": 4, "O": 4, "E": 4, "b": 4, "H": 4, "S": 1, "V": 1, "R": 1, "X": 1, "q": 1}
#: ``<STX>R`` with a 1-8 character image name and ``<CR>``: the Prodigy's remove image (p.36).
_REMOVE_IMAGE = re.compile(rb"\x02R[A-Za-z0-9_]{1,8}\r")
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

    kind: CommandKind
    raw: str
    meaning: str


class Explanation(BaseModel):
    """What a DPL stream does.

    Attributes:
        commands: Every command in order.
        label: The rebuilt label when the stream holds exactly one format, else None.
        problems: Records that could not be rebuilt, and why; a stream past
            :data:`MAX_EXPLAINED_COMMANDS` says how far it was explained, and rebuilds no label.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    commands: tuple[DecodedCommand, ...]
    label: Label | None
    problems: tuple[str, ...]


#: Bytes turned into text at a time: ``str.join`` lists its parts first, which at one part per byte
#: is eight times the job.
_READABLE_PIECE_BYTES = 64 * 1024
_READABLE_NAMES = {_SOH: "<SOH>", _STX: "<STX>", _CR: "<CR>", 0x1B: "<ESC>"}
_READABLE_BYTES = tuple(
    _READABLE_NAMES.get(b, chr(b) if b in _PRINTABLE else f"<{b:02X}>") for b in range(256)
)


def readable(data: bytes | bytearray) -> str:
    """Show control bytes by name.

    Example:
        >>> readable(b"\\x02L\\r")
        '<STX>L<CR>'
    """
    pieces = (data[start : start + _READABLE_PIECE_BYTES] for start in range(0, len(data), _READABLE_PIECE_BYTES))
    return "".join("".join(map(_READABLE_BYTES.__getitem__, piece)) for piece in pieces)


@dataclass
class _State:
    commands: list[DecodedCommand] = field(default_factory=list[DecodedCommand])
    elements: list[Element] = field(default_factory=list[Element])
    problems: list[str] = field(default_factory=list[str])
    formats: int = 0
    metric: bool = False
    #: Job settings the format commands set; the rebuilt label carries them. Quantity 1 is what
    #: the printer prints when a format has no ``Q`` command.
    quantity: int = 1
    heat: int | None = None
    print_speed: SpeedCode | None = None
    slew_speed: SpeedCode | None = None
    cut: Cut | None = None
    #: Why the latest ``:`` (cut by) command is not a valid cut, or None. It is reported with the
    #: label's own validation errors, so a stream with a bad cut gets no label, as before the cut
    #: became a model.
    invalid_cut: ValidationError | None = None
    #: Index in ``elements`` of the field the latest record produced, or None when that record
    #: produced none; a counter line counts exactly that field.
    latest_field: int | None = None
    #: The stream held more than :data:`MAX_EXPLAINED_COMMANDS`, so no label is rebuilt from a part.
    stopped: bool = False


def _full(state: _State) -> bool:
    """Whether the result holds all the commands it may; reports the rest as not explained, once."""
    if len(state.commands) < MAX_EXPLAINED_COMMANDS:
        return False
    if not state.stopped:
        state.stopped = True
        state.problems.append(f"{MAX_EXPLAINED_COMMANDS} commands, the rest not explained")
    return True


def _add(state: _State, kind: CommandKind, raw: bytes, meaning: str) -> None:
    state.commands.append(DecodedCommand(kind=kind, raw=readable(raw), meaning=meaning))


def _until_cr(data: bytes, start: int) -> int:
    end = data.find(b"\r", start)
    return len(data) if end < 0 else end + 1


def _remove_image_end(data: bytes, pos: int) -> int | None:
    """Return where a Prodigy's ``<STX>R<name><CR>`` ends, or None for the one-letter ``<STX>R``.

    The plain Prodigy removes one stored image by name with a command ended by ``<CR>`` (manual
    p.36); elsewhere ``<STX>RY``/``<STX>RN`` switch the ribbon saver and end after one letter.
    """
    match = _REMOVE_IMAGE.match(data, pos)
    return None if match is None else match.end()


def _system(data: bytes, pos: int, state: _State) -> int:
    letter = chr(data[pos + 1]) if pos + 1 < len(data) else ""
    if letter == "I":
        end = data.find(b"FFFF\r", pos)
        end = len(data) if end < 0 else end + len(b"FFFF\r")
        _add(state, CommandKind.DOWNLOAD, data[pos : _until_cr(data, pos)], "image download")
        return end
    if letter == "K" and data[pos + 2 : pos + 3] == b"f":
        end = pos + 3 + _PRESENT_DISTANCE_DIGITS
        _add(state, CommandKind.SYSTEM, data[pos:end], "present distance")
        return end
    if letter == "K":
        end = _until_cr(data, pos)
        _add(state, CommandKind.SYSTEM, data[pos:end], "extended command")
        return end
    end = _remove_image_end(data, pos) if letter == "R" else None
    end = end or pos + 2 + _SYSTEM_WIDTHS.get(letter, 0)
    _add(state, CommandKind.SYSTEM, data[pos:end], _SYSTEM_NAMES.get(letter, f"system command {letter}"))
    if letter == "m":
        state.metric = True
    return end


def _scan(data: bytes, state: _State) -> None:
    pos = 0
    while pos < len(data) and not _full(state):
        byte = data[pos]
        if byte == _SOH:
            letter = chr(data[pos + 1]) if pos + 1 < len(data) else ""
            _add(state, CommandKind.IMMEDIATE, data[pos : pos + 2], _IMMEDIATE_NAMES.get(letter, "immediate command"))
            pos += 2
        elif byte == _STX and data[pos + 1 : pos + 2] == b"L":
            _add(state, CommandKind.FORMAT, data[pos : pos + 2], _SYSTEM_NAMES["L"])
            pos = _format(data, _until_cr(data, pos), state)
        elif byte == _STX:
            pos = _system(data, pos, state)
        else:
            end = _until_cr(data, pos)
            _add(state, CommandKind.UNKNOWN, data[pos:end], "outside any command")
            pos = end


def _format(data: bytes, pos: int, state: _State) -> int:
    state.formats += 1
    while pos < len(data) and not _full(state):
        end = _until_cr(data, pos)
        text = data[pos:end].rstrip(b"\r")
        pos = end
        if text in {b"E", b"X"} or text[:1] == b"s":
            _add(
                state,
                CommandKind.END,
                text,
                {b"E": "end and print", b"X": "end without printing"}.get(text, "store format"),
            )
            return pos
        _format_line(text, state)
    return pos


def _format_line(text: bytes, state: _State) -> None:
    first = chr(text[0]) if text else ""
    if first in _RECORD_DIGITS and len(text) >= _HEADER_LENGTH:
        _add(state, CommandKind.RECORD, text, _record(text, state))
    elif first in _COUNTER_SIGNS:
        _add(state, CommandKind.COUNTER, text, _counter(text, state))
    else:
        _add(state, CommandKind.FORMAT, text, _format_setting(first, text, state))


def _format_setting(first: str, text: bytes, state: _State) -> str:
    """Apply one format setting and return its meaning; an unreadable one is a problem, not a crash.

    Reported the way an unreadable record is: the setting is ignored and the rest of the stream
    is still explained, so one bad line does not hide every command around it.
    """
    meaning = _FORMAT_NAMES.get(first, "unknown format command")
    try:
        _setting(first, text.decode("latin-1"), state)
    except ValueError:
        state.problems.append(f"{readable(text)}: setting not understood")
        return f"{meaning} (not understood)"
    return meaning


def _setting(first: str, text: str, state: _State) -> None:
    if first == "m":
        state.metric = True
    elif first == "Q":
        state.quantity = int(text[1:5] or "1")
    elif first == "H":
        state.heat = int(text[1:3])
    elif first == "P" and len(text[1:]) == 1:
        state.print_speed = SpeedCode(text[1])
    elif first == "S" and len(text[1:]) == 1:
        state.slew_speed = SpeedCode(text[1])
    elif first == ":":
        _cut_setting(int(text[1:5]), state)


def _cut_setting(every: int, state: _State) -> None:
    """Keep the cut a ``:`` command asks for, or why it is invalid; a later command replaces both."""
    try:
        state.cut, state.invalid_cut = Cut(every=every), None
    except ValidationError as exc:
        state.cut, state.invalid_cut = None, exc


def _scale(value: int, state: _State) -> float:
    return value / 10 if state.metric else value / 100


def _record(text: bytes, state: _State) -> str:
    state.latest_field = None
    try:
        parsed = _parse_record(text.decode("latin-1"), state)
    # A truncated job holds a record cut short, which reads as running off the end of its text.
    except IndexError:
        state.problems.append(f"{readable(text)}: record cut short")
        return "record (not understood)"
    except (ValueError, KeyError, ValidationError):
        state.problems.append(f"{readable(text)}: record not understood")
        return "record (not understood)"
    if parsed.element is not None:
        state.elements.append(parsed.element)
        state.latest_field = len(state.elements) - 1
    return parsed.meaning


def _counter(text: bytes, state: _State) -> str:
    """Attach the counter line ``text`` (sign, fill, two-digit step) to the field before it."""
    index = state.latest_field
    target = state.elements[index] if index is not None else None
    if index is None or not isinstance(target, Text | Barcode):
        state.problems.append(f"{readable(text)}: no text or barcode field right before it to count")
        return "counter (nothing to count)"
    decoded = text.decode("latin-1")
    sign = CounterSign(decoded[0])
    try:
        counter = Counter(
            step=int(decoded[2:4]),
            decrement=sign.decrement,
            alphanumeric=sign.alphanumeric,
            fill=decoded[1:2],
        )
    except (ValueError, ValidationError):
        state.problems.append(f"{readable(text)}: counter not understood")
        return "counter (not understood)"
    state.elements[index] = target.model_copy(update=_CounterUpdate(counter=counter))
    letters = " (letters too)" if sign.alphanumeric else ""
    return f"count the previous field {'down' if sign.decrement else 'up'} by {counter.step}{letters}"


@dataclass(frozen=True)
class _ParsedRecord:
    """The element a record rebuilds, or None where it rebuilds none, and what the record means."""

    element: Element | None
    meaning: str


def _parse_record(text: str, state: _State) -> _ParsedRecord:
    kind = text[1]
    if kind == "X":
        return _graphic(text, state)
    if kind == "Y":
        return _image(text, state)
    if kind == "W":
        return _ParsedRecord(_barcode(text, state, identifier="W1c", offset=2), "barcode datamatrix")
    if kind.isdigit():
        return _ParsedRecord(_text(text, state), f"text font {kind}")
    return _ParsedRecord(
        _barcode(text, state, identifier=kind, offset=0), f"barcode {SYMBOLOGY_BY_IDENTIFIER[kind].value}"
    )


def _image(text: str, state: _State) -> _ParsedRecord:
    """An image placement, its square magnification in the rebuilt Image.

    A dot width and height that differ, or one outside the 1-9 an Image holds, is named in the meaning
    instead (``image, magnified 2x3``) and the rebuilt Image keeps magnification 1.
    """
    head = _head(text)
    square = head.width == head.height and 1 <= head.width <= MAX_IMAGE_MAGNIFICATION
    image = Image(
        name=text[_HEADER_LENGTH:],
        x=_scale(head.column, state),
        y=_scale(head.row, state),
        magnification=head.width if square else 1,
    )
    if square and head.width == 1:
        return _ParsedRecord(image, "image")
    if square:
        return _ParsedRecord(image, f"image at magnification {head.width}")
    return _ParsedRecord(image, f"image, magnified {head.width}x{head.height}")


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
    size = _font9_size(head.size) if font == _FONT_9 else _NO_FONT9_SIZE
    return Text(
        text=data,
        font=font,
        width_multiplier=head.width,
        height_multiplier=head.height,
        points=size.points,
        font_id=size.font_id,
        x=x,
        y=y,
        rotation=head.rotation,
    )


@dataclass(frozen=True)
class _Font9Size:
    """A font 9 size code read back: its point size, or the font id it names instead."""

    points: int | None = None
    font_id: str | None = None


#: The size of a text field in a font other than 9, which names neither.
_NO_FONT9_SIZE = _Font9Size()


def _font9_size(code: str) -> _Font9Size:
    """Return the point size or the font id a font 9 size code names."""
    if code[0] == "A" and code[1:].isdigit():
        return _Font9Size(points=int(code[1:]))
    if code.isdigit() and int(code) < len(OLD_SERIES_POINTS):
        return _Font9Size(points=OLD_SERIES_POINTS[int(code)])
    return _Font9Size(font_id=code)


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


def _graphic(text: str, state: _State) -> _ParsedRecord:
    fill = FillPattern(int(text[4:7]))
    row, column = int(text[7:11]), int(text[11:15])
    shape, rest = text[15], text[16:]
    x, y = _scale(column, state), _scale(row, state)
    if shape in "Ll":
        width = 3 if shape == "L" else 4
        h, v = (_scale(int(rest[i : i + width]), state) for i in (0, width))
        horizontal = h >= v
        x2, y2 = (x + h, y) if horizontal else (x, y + v)
        return _ParsedRecord(Line(x=x, y=y, x2=x2, y2=y2, thickness=v if horizontal else h), "line")
    if shape in "Bb":
        width = 3 if shape == "B" else 4
        w, h, t, s = (_scale(int(rest[i * width : (i + 1) * width]), state) for i in range(4))
        return _ParsedRecord(Box(x=x, y=y, width=w, height=h, thickness=t, side_thickness=s), "box")
    if shape == "C":
        return _ParsedRecord(Circle(x=x, y=y, radius=_scale(int(rest[7:11]), state), fill=fill), "circle")
    coords = rest[7:]
    points = [Point(x=x, y=y)] + [
        Point(x=_scale(int(coords[i + 4 : i + 8]), state), y=_scale(int(coords[i : i + 4]), state))
        for i in range(0, len(coords) - 7, 8)
    ]
    return _ParsedRecord(Polygon(points=points, fill=fill), "polygon")


def _rebuild(state: _State) -> Label | None:
    if state.formats > 1:
        state.problems.append(f"{state.formats} label formats: only a stream with exactly one is rebuilt as a label")
        return None
    if state.stopped or state.formats != 1 or not state.elements:
        return None
    cut_errors = state.invalid_cut.error_count() if state.invalid_cut is not None else 0
    try:
        label = Label(
            unit=Unit.MM if state.metric else Unit.INCH,
            elements=list(state.elements),
            quantity=state.quantity,
            heat=state.heat,
            print_speed=state.print_speed,
            slew_speed=state.slew_speed,
            cut=state.cut,
        )
    except ValidationError as exc:
        errors = exc.error_count() + cut_errors
    else:
        if not cut_errors:
            return label
        errors = cut_errors
    state.problems.append(f"label: {errors} validation errors")
    return None


def explain(data: bytes) -> Explanation:
    """Explain every command in ``data`` and rebuild the label when there is exactly one format.

    A record cut short or a value it cannot read becomes a problem in the result; nothing raises.

    Args:
        data: The DPL bytes, whole or in part.

    Returns:
        The commands in order, the rebuilt label or None, and the problems found.

    Example:
        >>> result = explain(b"\\x02L\\r121100000500025Hi\\rE\\r")
        >>> [c.kind.value for c in result.commands]
        ['format', 'record', 'end']
        >>> result.label.elements[0].text
        'Hi'
    """
    state = _State()
    _scan(data, state)
    return Explanation(commands=tuple(state.commands), label=_rebuild(state), problems=tuple(state.problems))


__all__ = ["MAX_EXPLAINED_COMMANDS", "DecodedCommand", "Explanation", "explain", "readable"]
