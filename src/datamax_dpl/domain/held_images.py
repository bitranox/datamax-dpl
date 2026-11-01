"""The image names a printer already holds in a job's image module, as far as the job knows them.

A printer that holds an image name prints nothing for a format downloading that name again (one
Allegro 2, 3 of 3 rounds, 2026-11-01), while a format that only refers to the held name prints
it. So a label whose module is not cleared before its downloads leaves out the download of every
name the printer holds and places the stored image instead. What the printer holds comes from its
module directory (``<STX>W``), which the application reads; the domain only weighs the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .memory import MemoryDirectory

#: Why the names are unknown when nobody asked the printer: an offline render, validate or preview.
NOT_ASKED = "the printer was not asked"


@dataclass(frozen=True)
class HeldImages:
    """What a job knows about the image names its printer holds in the job's image module.

    The default knows nothing: ``listed`` is None, so a job with no clear before its downloads
    keeps every download and says the names were not read (``image_names_unread``).

    Attributes:
        listed: Names the module's directory listed, or None when it was not read.
        unread_because: Why ``listed`` is None, quoted in the ``image_names_unread`` finding.
        sent: Names an earlier label of the same batch downloaded into the module with no clear
            since; known to be held, without the printer having said so.

    Example:
        >>> HeldImages(listed=frozenset({"LOGO"})).holds("LOGO"), HeldImages().holds("LOGO")
        (True, False)
    """

    listed: frozenset[str] | None = None
    unread_because: str = NOT_ASKED
    sent: frozenset[str] = frozenset()

    def holds(self, name: str) -> bool:
        """Return True when the printer is known to hold ``name`` in the module.

        Names compare exactly: whether a printer folds the letter case of a stored name is
        unverified, and a name taken for held by mistake would print another image.
        """
        return name in self.sent or (self.listed is not None and name in self.listed)


#: The module is known to be empty: a clear just emptied it.
EMPTY_MODULE = HeldImages(listed=frozenset())


def held_images_from(directory: MemoryDirectory, *, module: str) -> HeldImages:
    """Return what ``directory`` (a graphic ``<STX>W`` answer) says ``module`` holds.

    A module the answer does not list holds nothing the job could collide with. An answer the
    parser could not read gives no names: every download is then kept, as when nothing was asked.

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
        >>> held_images_from(answer, module="B").listed
        frozenset()
    """
    if not directory.parsed:
        return HeldImages(unread_because="the printer's directory answer could not be read")
    names = (entry.names for entry in directory.modules if entry.module == module)
    return HeldImages(listed=frozenset(name for listing in names for name in listing))


__all__ = ["EMPTY_MODULE", "NOT_ASKED", "HeldImages", "held_images_from"]
