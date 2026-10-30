"""Memory module directories: the answer to ``<STX>W`` (Request Memory Module Information).

The printer lists, for one kind of stored data (fonts, graphics or label formats), each memory
module with the names it holds and its free bytes (``docs/dpl-reference.md`` section 8). The answer
has no terminator and no fixed length, so a transport reads it until the printer falls silent;
this module only turns the bytes into a :class:`MemoryDirectory`.

The parser is lenient on purpose: the manual's own sample is ambiguous about a line break, and a
directory is diagnostic output, so an answer it cannot read is returned unparsed with its raw text
instead of raised.
"""

from __future__ import annotations

import re
from typing import Literal, TypeAlias

from pydantic import BaseModel

#: The kinds of stored data a directory lists, each asked with its own ``<STX>W`` letter.
MemoryKind: TypeAlias = Literal["font", "graphic", "format"]

_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_MODULE = re.compile(r"MODULE:\s*(\S+)", re.IGNORECASE)
# The manual prints the number on the line after the header but marks no <CR> there, so the
# number may follow on the same line or on the next one. An Allegro 2 puts a space before the
# colon ("MODULE : 00048631"), which the manual's sample does not show, so blanks are allowed
# around every word and the colon.
_AVAILABLE = re.compile(r"AVAILABLE\s+BYTES\s+IN\s+MODULE\s*:\s*(\S*)", re.IGNORECASE)
# A line that starts like the free-bytes header is never a stored name, even when the rest of it
# cannot be read: taking it as a name would report a module with a made-up item and no free bytes.
_AVAILABLE_START = re.compile(r"AVAILABLE\s+BYTES\b", re.IGNORECASE)
_NO_MODULES = re.compile(r"NO MODULES AVAILABLE", re.IGNORECASE)
_NUMBER = re.compile(r"[0-9]{1,9}")
# XON/XOFF never belong to a text answer; they are flow control the printer may interleave.
_FLOW_CONTROL = "\x11\x13"


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
        parsed: False when the answer was neither a module list nor "No Modules Available", or
            when a module's free bytes could not be read (missing, or a header line that is not
            the manual's).
        modules: The modules found; empty for "No Modules Available" and for an unparsed answer.
        raw: The whole answer as text, for a reader to judge what the printer sent.
    """

    kind: MemoryKind
    parsed: bool
    modules: tuple[MemoryModule, ...]
    raw: str


def parse_module_directory(raw: bytes, *, kind: MemoryKind) -> MemoryDirectory:
    """Parse the answer to ``<STX>W`` for ``kind``.

    Lines end in CR, LF or CRLF. Each module starts with ``MODULE: x``; the lines after it are the
    names, up to ``AVAILABLE BYTES IN MODULE:`` and its number (on the same line or the next;
    blanks around the colon and any letter case are accepted). Lines before the first module and
    after a module's number are ignored.

    A line that starts like the free-bytes header ends the names even when it cannot be read; the
    module then has no free-bytes figure, and a module without one makes the directory unparsed.

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
        >>> parse_module_directory(b"No Modules Available\\r", kind="font").modules
        ()
    """
    text = raw.decode("ascii", errors="backslashreplace")
    lines = _lines(text)
    modules = tuple(_module(block) for block in _blocks(lines))
    found = bool(modules) or any(_NO_MODULES.fullmatch(line) for line in lines)
    parsed = found and all(module.available_bytes is not None for module in modules)
    return MemoryDirectory(kind=kind, parsed=parsed, modules=modules, raw=text)


def _lines(text: str) -> list[str]:
    """Split ``text`` into its non-empty lines, flow-control bytes and outer blanks removed."""
    stripped = (line.translate({ord(c): None for c in _FLOW_CONTROL}).strip() for line in _LINE_BREAK.split(text))
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


def _module(block: list[str]) -> MemoryModule:
    """Read one module block: its letter, the names before the free-bytes header, the free bytes."""
    header = _MODULE.fullmatch(block[0])
    letter = header.group(1) if header else block[0]
    rest = block[1:]
    for index, line in enumerate(rest):
        if _AVAILABLE_START.match(line):
            following = rest[index + 1] if index + 1 < len(rest) else ""
            return MemoryModule(module=letter, names=tuple(rest[:index]), available_bytes=_free_bytes(line, following))
    return MemoryModule(module=letter, names=tuple(rest), available_bytes=None)


def _free_bytes(line: str, following: str) -> int | None:
    """The number of a free-bytes header ``line`` (or of the ``following`` line), or None if unreadable."""
    available = _AVAILABLE.fullmatch(line)
    if available is None:
        return None
    return _number(available.group(1) or following)


def _number(text: str) -> int | None:
    """``text`` as a number of bytes, or None when it is not one."""
    return int(text) if _NUMBER.fullmatch(text) else None


__all__ = ["MemoryDirectory", "MemoryKind", "MemoryModule", "parse_module_directory"]
