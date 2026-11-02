"""The image names a printer already holds in a job's image module, as far as the job knows them.

A printer that holds an image name prints nothing for a format downloading that name again (one
Allegro 2, 3 of 3 rounds, 2026-11-01), while a format that only refers to the held name prints
it. So a label whose module is not cleared before its downloads leaves out the download of every
name the printer holds and places the stored image instead. What the printer holds comes from its
module directory (``<STX>W``), which the application reads; the domain only weighs the answer.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .memory import MemoryDirectory

#: Why the names are unknown when nobody asked the printer: an offline render, validate or preview.
NOT_ASKED = "the printer was not asked"
#: The module letter that stands for the printer's default module on every model (manual, ``<STX>X``).
DEFAULT_MODULE = "C"
#: Why the names of the default module are unknown: a directory lists real module letters only (one
#: Allegro 2 listed just ``MODULE: A``), so which of them ``C`` is cannot be read from it.
DEFAULT_MODULE_UNREAD = "C is the printer's default module; which letter it is was not read"


def _no_commands() -> dict[str, bytes]:
    return {}


class _ReadOnlyCommands(Mapping[str, bytes]):
    """A read-only copy of image names, each with its download command, that copies and pickles.

    A ``types.MappingProxyType`` would be read-only too, but it cannot be deep-copied or pickled,
    so neither could a :class:`HeldImages` holding one, nor could ``dataclasses.asdict`` read it.
    This one copies and pickles by every pickle protocol: it is rebuilt from a plain dict, since
    protocols 0 and 1 refuse an object with ``__slots__`` otherwise.

    Example:
        >>> commands = _ReadOnlyCommands({"D1": b"one"})
        >>> commands["D1"], dict(commands) == {"D1": b"one"}
        (b'one', True)
    """

    __slots__ = ("_commands",)

    def __init__(self, commands: Mapping[str, bytes]) -> None:
        """Hold a copy of ``commands``, so a later change to them changes nothing here."""
        self._commands = dict(commands)

    def __getitem__(self, name: str) -> bytes:
        """Return the download command of ``name``."""
        return self._commands[name]

    def __iter__(self) -> Iterator[str]:
        """Iterate the names."""
        return iter(self._commands)

    def __len__(self) -> int:
        """Return how many names there are."""
        return len(self._commands)

    def __repr__(self) -> str:
        """Show the names and commands."""
        return f"{type(self).__name__}({self._commands!r})"

    def __reduce__(self) -> tuple[type[_ReadOnlyCommands], tuple[dict[str, bytes]]]:
        """Rebuild from a plain dict of the names and commands, for copy and every pickle protocol."""
        return (type(self), (dict(self._commands),))


@dataclass(frozen=True)
class HeldImages:
    """What a job knows about the image names its printer holds in the job's image module.

    The default knows nothing: ``listed`` is None, so a job with no clear before its downloads
    keeps every download and says the names were not read (``image_names_unread``).

    Attributes:
        listed: Names the module's directory listed, or None when it was not read.
        unread_because: Why ``listed`` is None, quoted in the ``image_names_unread`` finding.
        sent: Names an earlier label of the same batch downloaded into the module with no clear
            since, each with the download command that sent it; known to be held, without the
            printer having said so. The command tells a later label whether its own image under
            that name is the same picture.
        placed: Names the printer listed that an earlier label of the batch placed without
            downloading, each with the download command of the picture that label took the
            listed image for. A later label with a different picture under that name names it
            with the next salt instead, as for a name in ``sent``; the printer's own picture under
            the name stays unknown.

    ``sent`` and ``placed`` are read-only copies of the mappings given, so a value stays what it
    was made with, however the caller's dict changes afterwards.

    Example:
        >>> HeldImages(listed=frozenset({"LOGO"})).holds("LOGO"), HeldImages().holds("LOGO")
        (True, False)
    """

    listed: frozenset[str] | None = None
    unread_because: str = NOT_ASKED
    # Left out of the hash, which a dict has none of; equal values still hash alike.
    sent: Mapping[str, bytes] = field(default_factory=_no_commands, hash=False)
    placed: Mapping[str, bytes] = field(default_factory=_no_commands, hash=False)

    def __post_init__(self) -> None:
        """Hold ``sent`` and ``placed`` as read-only copies."""
        # Frozen stops rebinding a field, not changing the dict it holds; a read-only copy stops
        # both the caller's dict and any reader from changing what a batch carries along.
        object.__setattr__(self, "sent", _ReadOnlyCommands(self.sent))
        object.__setattr__(self, "placed", _ReadOnlyCommands(self.placed))

    def holds(self, name: str) -> bool:
        """Return True when the printer is known to hold ``name`` in the module.

        Names compare exactly, letter case included: one Allegro 2 listed a name as it was sent
        and printed no image for a format naming it in another case (2026-11-01), so a name
        listed only in another case is another name.

        Example:
            >>> HeldImages(listed=frozenset({"LOGO"})).holds("logo")
            False
        """
        return name in self.sent or (self.listed is not None and name in self.listed)

    def command_for(self, name: str) -> bytes | None:
        """Return the download command of the picture the batch knows, or took, the printer to hold under ``name``.

        A name the batch sent gives the command that sent it, a listed name an earlier label
        placed gives the command of the picture that label took it for; any other name gives None.

        Example:
            >>> held = HeldImages(listed=frozenset({"D1"}), sent={"D2": b"two"}, placed={"D1": b"one"})
            >>> held.command_for("D1"), held.command_for("D2"), held.command_for("D3")
            (b'one', b'two', None)
        """
        sent = self.sent.get(name)
        return sent if sent is not None else self.placed.get(name)

    def as_listed(self, listed: frozenset[str]) -> HeldImages:
        """Return what the job knows once a read of the module listed ``listed``.

        The listing is what the module holds now: a name the batch sent or placed stays known,
        with its command, only where the listing shows it (a download into a full module is lost
        with no error), and every name it shows is held, so a label rendered with the result
        leaves out its download.

        Example:
            >>> held = HeldImages(sent={"D1": b"one", "D2": b"two"})
            >>> now = held.as_listed(frozenset({"D1", "LOGO"}))
            >>> dict(now.sent), now.holds("LOGO"), now.holds("D2")
            ({'D1': b'one'}, True, False)
        """
        return HeldImages(
            listed=listed,
            sent={name: command for name, command in self.sent.items() if name in listed},
            placed={name: command for name, command in self.placed.items() if name in listed},
        )


#: The module is known to be empty: a clear just emptied it.
EMPTY_MODULE = HeldImages(listed=frozenset())


def held_images_from(directory: MemoryDirectory, *, module: str) -> HeldImages:
    """Return what ``directory`` (a graphic ``<STX>W`` answer) says ``module`` holds.

    A module the answer lists with no names holds none. A module the answer does not list gives
    no names: the answer may have ended before it, and a module read as empty that holds a name
    would get that name downloaded again, which prints nothing. An answer the parser could not
    read gives no names either, and so does the default module ``C``, which the answer never lists
    by that letter: every download is then kept, as when nothing was asked.

    Args:
        directory: The printer's graphic directory.
        module: The letter of the job's image module.

    Returns:
        The names listed for ``module``, or unknown names with the reason.

    Example:
        >>> from datamax_dpl.domain.memory import parse_module_directory
        >>> raw = b"MODULE: A\\rLOGO\\rAVAILABLE BYTES IN MODULE: 00049083\\r"
        >>> answer = parse_module_directory(raw, kind="graphic")
        >>> held_images_from(answer, module="A").listed
        frozenset({'LOGO'})
        >>> held_images_from(answer, module="B").unread_because
        "the printer's directory answer did not list module B"
        >>> held_images_from(answer, module="C").listed is None
        True
    """
    if module == DEFAULT_MODULE:
        return HeldImages(unread_because=DEFAULT_MODULE_UNREAD)
    if not directory.parsed:
        return HeldImages(unread_because="the printer's directory answer could not be read")
    listings = [entry.names for entry in directory.modules if entry.module == module]
    if not listings:
        return HeldImages(unread_because=f"the printer's directory answer did not list module {module}")
    return HeldImages(listed=frozenset(name for listing in listings for name in listing))


__all__ = ["DEFAULT_MODULE", "DEFAULT_MODULE_UNREAD", "EMPTY_MODULE", "NOT_ASKED", "HeldImages", "held_images_from"]
