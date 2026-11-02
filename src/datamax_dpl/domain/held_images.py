"""The image names a printer holds in a job's image module, as one read of its directory says.

A printer that holds an image name prints nothing for a format downloading that name again (one
Allegro 2, 3 of 3 rounds, 2026-11-01), while a format that only refers to the held name prints
it. So a label whose module is not cleared before its downloads leaves out the download of every
name the printer holds and places the stored image instead. What the printer holds comes from its
module directory (``<STX>W``), which the application reads before each such label; the domain only
weighs the answer. Nothing is remembered from one label to the next: the printer may have been
reset, or another program may have printed, in between, so its directory is the one source.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .enums import ImageModule
from .memory import NO_MODULES_MEANING

if TYPE_CHECKING:
    from .memory import MemoryDirectory

#: Why the names are unknown when nobody asked the printer: an offline render, validate or preview.
NOT_ASKED = "the printer was not asked"
#: Why the names of the default module (:attr:`ImageModule.C`) are unknown: a directory lists real
#: module letters only (one Allegro 2 listed just ``MODULE: A``), so which of them ``C`` is cannot
#: be read from it.
DEFAULT_MODULE_UNREAD = "C is the printer's default module; which letter it is was not read"


@dataclass(frozen=True)
class HeldImages:
    """What one read of the printer's directory says it holds in the job's image module.

    The default knows nothing: ``listed`` is None, so a job with no clear before its downloads
    keeps every download and says the names were not read (``image_names_unread``).

    Attributes:
        listed: Names the module's directory listed, or None when it was not read.
        unread_because: Why ``listed`` is None, quoted in the ``image_names_unread`` finding.
        read_at_send: The names are not known yet and will be read right before the label's
            downloads go out, by a sender on a two-way link: a render with this reports nothing
            about them, and the sender renders the label again with what that read lists.
        reset_reported: The directory answer that gave this carried the printer's reset report,
            which it gives once: the read took it, and a sender has nothing left to ask for.
        uploaded: Names this label's own upload check sent in an earlier round. Listed, each holds
            the picture the label itself sent, so it is left out with no finding: it is not a name
            the printer held without the label. Never passed from one label to the next.

    Example:
        >>> HeldImages(listed=frozenset({"LOGO"})).holds("LOGO"), HeldImages().holds("LOGO")
        (True, False)
    """

    listed: frozenset[str] | None = None
    unread_because: str = NOT_ASKED
    read_at_send: bool = False
    reset_reported: bool = False
    uploaded: frozenset[str] = frozenset()

    def holds(self, name: str) -> bool:
        """Return True when the printer's directory listed ``name`` in the module.

        Names compare exactly, letter case included: one Allegro 2 listed a name as it was sent
        and printed no image for a format naming it in another case (2026-11-01), so a name
        listed only in another case is another name.

        Example:
            >>> HeldImages(listed=frozenset({"LOGO"})).holds("logo")
            False
        """
        return self.listed is not None and name in self.listed


#: The module is known to be empty: a clear just emptied it.
EMPTY_MODULE = HeldImages(listed=frozenset())
#: The names are read right before each label's downloads, on a two-way link (see ``read_at_send``).
READ_AT_SEND = HeldImages(unread_because="they are read right before the label's downloads", read_at_send=True)


def held_images_from(directory: MemoryDirectory, *, module: ImageModule) -> HeldImages:
    """Return what ``directory`` (a graphic ``<STX>W`` answer) says ``module`` holds, and any reset it reported.

    A reset report says nothing about the module: what it holds is what the listing says.

    A module the answer lists with no names holds none. A module the answer does not list gives
    no names: the answer may have ended before it, and a module read as empty that holds a name
    would get that name downloaded again, which prints nothing. An answer the parser could not
    read gives no names either, nor does "No Modules Available" (which the manual reads as a
    corrupt or worn-out module, not as an empty one), nor the default module ``C``, which the
    answer never lists by that letter: every download is then kept, as when nothing was asked.

    Args:
        directory: The printer's graphic directory.
        module: The job's image module.

    Returns:
        The names listed for ``module``, or unknown names with the reason.

    Example:
        >>> from datamax_dpl.domain.memory import parse_module_directory
        >>> raw = b"MODULE: A\\rLOGO\\rAVAILABLE BYTES IN MODULE: 00049083\\r"
        >>> from datamax_dpl.domain.enums import MemoryKind
        >>> answer = parse_module_directory(raw, kind=MemoryKind.GRAPHIC)
        >>> held_images_from(answer, module=ImageModule.A).listed
        frozenset({'LOGO'})
        >>> held_images_from(answer, module=ImageModule.B).unread_because
        "the printer's directory answer did not list module B"
        >>> held_images_from(answer, module=ImageModule.C).listed is None
        True
    """
    return replace(_listed_in(directory, module), reset_reported=directory.reset_reported)


def _listed_in(directory: MemoryDirectory, module: ImageModule) -> HeldImages:
    """The names ``directory`` lists for ``module``, or unknown names with the reason."""
    if module == ImageModule.C:
        return HeldImages(unread_because=DEFAULT_MODULE_UNREAD)
    if not directory.parsed:
        return HeldImages(unread_because="the printer's directory answer could not be read")
    if directory.no_modules_available:
        return HeldImages(unread_because=f'the printer answered "No Modules Available" ({NO_MODULES_MEANING})')
    # A letter outside A-E names no module, so it matches none; a parsed directory has none anyway.
    listings = [entry.names for entry in directory.modules if entry.module == module]
    if not listings:
        return HeldImages(unread_because=f"the printer's directory answer did not list module {module.value}")
    return HeldImages(listed=frozenset(name for listing in listings for name in listing))


__all__ = [
    "DEFAULT_MODULE_UNREAD",
    "EMPTY_MODULE",
    "NOT_ASKED",
    "READ_AT_SEND",
    "HeldImages",
    "held_images_from",
]
