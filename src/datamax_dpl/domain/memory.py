"""Memory module directories: the answer to ``<STX>W`` (Request Memory Module Information).

The printer lists, for one kind of stored data (fonts, graphics or label formats), each memory
module with the names it holds and its free bytes (``docs/dpl-reference.md`` section 8). The answer
has no terminator and no fixed length, so a transport reads it until the printer falls silent;
this module only turns the bytes into a :class:`MemoryDirectory`.

The parser is lenient about layout, because the manual's sample and a real Allegro 2 already
differ in line breaks and blanks, and strict about content: a module letter, name or free-bytes
figure outside the manual's limits makes the directory unparsed, so a garbled or cut-short answer
is never reported as a valid listing. A directory is diagnostic output, so an answer it cannot
read is returned unparsed with its raw text instead of raised.
"""

from __future__ import annotations

import re
from typing import Literal, TypeAlias

from pydantic import BaseModel

#: The kinds of stored data a directory lists, each asked with its own ``<STX>W`` letter.
MemoryKind: TypeAlias = Literal["font", "graphic", "format"]

_LINE_BREAK = re.compile(r"\r\n|\r|\n")
#: Line ends, then nothing but flow control and blanks: the answer's last line was finished.
_ENDED = re.compile(r"[\r\n][\x11\x13 \t]*\Z")
# Blanks around the colon are allowed here as in the free-bytes header below.
_MODULE = re.compile(r"MODULE\s*:\s*(\S+)", re.IGNORECASE)
# The manual prints the number on the line after the header but marks no <CR> there, so the
# number may follow on the same line or on the next one. An Allegro 2 puts a space before the
# colon ("MODULE : 00048631"), which the manual's sample does not show, so blanks are allowed
# around every word and the colon.
_AVAILABLE = re.compile(r"AVAILABLE\s+BYTES\s+IN\s+MODULE\s*:\s*(.*)", re.IGNORECASE)
# A line that starts like the free-bytes header is never a stored name, even when the rest of it
# cannot be read: taking it as a name would report a module with a made-up item and no free bytes.
_AVAILABLE_START = re.compile(r"AVAILABLE\s+BYTES\b", re.IGNORECASE)
_NO_MODULES = re.compile(r"NO MODULES AVAILABLE", re.IGNORECASE)
#: The free-bytes figure: eight digits in the manual's sample and in what an Allegro 2 sends, so a
#: shorter one is an answer cut short, not a smaller number.
_FIGURE = re.compile(r"[0-9]{8}")
#: Module letters the manual knows (Appendix L).
MODULE_LETTERS = frozenset("ABCDE")
#: Longest stored name the manual allows (fonts, graphics and formats alike).
MAX_NAME_LENGTH = 16
#: A stored name is printable ASCII.
_NAME = re.compile(rf"[\x20-\x7e]{{1,{MAX_NAME_LENGTH}}}")
# XON/XOFF never belong to a text answer; they are flow control the printer may interleave.
_FLOW_CONTROL = {0x11: None, 0x13: None}


class MemoryModule(BaseModel, frozen=True, extra="forbid"):
    """One module of a directory.

    Attributes:
        module: The module letter (``A`` to ``E``, see the manual's Appendix L).
        names: The names of the stored items of the asked kind, in the printer's order.
        available_bytes: Free bytes in the module, or None when the answer did not say.
    """

    module: str
    names: tuple[str, ...]
    available_bytes: int | None


class MemoryDirectory(BaseModel, frozen=True, extra="forbid"):
    """What the printer answered to one ``<STX>W`` request.

    Attributes:
        kind: The kind of data that was asked for.
        parsed: True when the answer is a module list within the manual's limits, or "No Modules
            Available". False otherwise: no module found, a module letter outside A-E, a module
            listed twice, a name longer than 16 characters or holding a byte that is not
            printable ASCII, or a module whose free bytes are missing, cut short or not the
            manual's eight digits ending their line.
        no_modules_available: The printer answered "No Modules Available", which the manual
            (``<STX>q`` Clear Module, page 35) gives as the sign of a corrupt or worn-out module.
        modules: The modules found, also when they make the answer unparsed.
        raw: The whole answer as text, for a reader to judge what the printer sent.
    """

    kind: MemoryKind
    parsed: bool
    no_modules_available: bool
    modules: tuple[MemoryModule, ...]
    raw: str


def parse_module_directory(raw: bytes, *, kind: MemoryKind) -> MemoryDirectory:
    """Parse the answer to ``<STX>W`` for ``kind``.

    Lines end in CR, LF or CRLF. Each module starts with ``MODULE: x``; the lines after it are the
    names, up to ``AVAILABLE BYTES IN MODULE:`` and its eight-digit number (on the same line or
    the next; blanks around both colons and any letter case are accepted). Lines before the first
    module and after a module's number are ignored.

    A line that starts like the free-bytes header ends the names even when it cannot be read; the
    module then has no free-bytes figure. Whatever breaks the manual's limits (see
    :attr:`MemoryDirectory.parsed`) makes the directory unparsed; the modules and the raw text are
    kept either way.

    Args:
        raw: The bytes the printer sent.
        kind: The kind that was asked for.

    Returns:
        The directory; ``parsed`` is False when no module and no "No Modules Available" was found.

    Example:
        >>> answer = b"MODULE: B\\rLOGO\\rAVAILABLE BYTES IN MODULE:00257919\\r"
        >>> directory = parse_module_directory(answer, kind="graphic")
        >>> directory.parsed, directory.modules[0].names, directory.modules[0].available_bytes
        (True, ('LOGO',), 257919)
        >>> parse_module_directory(b"MODULE: A\\rAVAILABLE BYTES IN MODULE : 00048631\\r", kind="font").parsed
        True
        >>> parse_module_directory(b"MODULE: A\\rLOGO\\r", kind="graphic").parsed
        False
        >>> parse_module_directory(b"No Modules Available\\r", kind="font").no_modules_available
        True
    """
    # latin-1 keeps one character per byte, so a non-ASCII byte stays one unprintable character
    # in a name instead of becoming a printable escape sequence.
    text = raw.decode("latin-1")
    lines = _lines(text)
    blocks = _blocks(lines)
    ended = _ENDED.search(text) is not None
    modules = tuple(_module(block, ended=ended or i < len(blocks) - 1) for i, block in enumerate(blocks))
    no_modules = any(_NO_MODULES.fullmatch(line) for line in lines)
    letters = [module.module for module in modules]
    parsed = (
        (bool(modules) != no_modules)
        and len(set(letters)) == len(letters)
        and all(_within_limits(module) for module in modules)
    )
    return MemoryDirectory(
        kind=kind,
        parsed=parsed,
        no_modules_available=no_modules,
        modules=modules,
        raw=raw.decode("ascii", errors="backslashreplace"),
    )


def _within_limits(module: MemoryModule) -> bool:
    """Whether ``module`` keeps to the manual: letter A-E, free bytes read, names it allows."""
    return (
        module.module in MODULE_LETTERS
        and module.available_bytes is not None
        and all(_NAME.fullmatch(name) for name in module.names)
    )


def _lines(text: str) -> list[str]:
    """Split ``text`` into its non-empty lines, flow-control bytes and outer blanks removed."""
    stripped = (line.translate(_FLOW_CONTROL).strip(" \t") for line in _LINE_BREAK.split(text))
    return [line for line in stripped if line]


def _blocks(lines: list[str]) -> list[list[str]]:
    """Group ``lines`` into one list per module, each starting with its ``MODULE:`` line."""
    blocks: list[list[str]] = []
    for line in lines:
        if _MODULE.fullmatch(line):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    return blocks


def _module(block: list[str], *, ended: bool) -> MemoryModule:
    """Read one module block: its letter, the names before the free-bytes header, the free bytes.

    ``ended`` is False when the block's last line is the answer's last and no line end follows it.
    """
    header = _MODULE.fullmatch(block[0])
    letter = (header.group(1) if header else block[0]).upper()
    rest = block[1:]
    for index, line in enumerate(rest):
        if _AVAILABLE_START.match(line):
            free = _free_bytes(rest, index, ended=ended)
            return MemoryModule(module=letter, names=tuple(rest[:index]), available_bytes=free)
    return MemoryModule(module=letter, names=tuple(rest), available_bytes=None)


def _free_bytes(rest: list[str], index: int, *, ended: bool) -> int | None:
    """The figure of the free-bytes header ``rest[index]``, on its line or the next, or None.

    None when the header is not the manual's, the figure is missing or not eight digits, or the
    figure's line is the answer's unfinished last line (no line end after it: possibly cut short).
    """
    available = _AVAILABLE.fullmatch(rest[index])
    if available is None:
        return None
    at = index if available.group(1) else index + 1
    if at >= len(rest) or (at == len(rest) - 1 and not ended):
        return None
    figure = rest[at] if at > index else available.group(1)
    return int(figure) if _FIGURE.fullmatch(figure) else None


__all__ = [
    "MAX_NAME_LENGTH",
    "MODULE_LETTERS",
    "MemoryDirectory",
    "MemoryKind",
    "MemoryModule",
    "parse_module_directory",
]
