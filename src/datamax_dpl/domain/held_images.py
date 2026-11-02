"""The image names a printer already holds in a job's image module, as far as the job knows them.

A printer that holds an image name prints nothing for a format downloading that name again (one
Allegro 2, 3 of 3 rounds, 2026-11-01), while a format that only refers to the held name prints
it. So a label whose module is not cleared before its downloads leaves out the download of every
name the printer holds and places the stored image instead. What the printer holds comes from its
module directory (``<STX>W``), which the application reads; the domain only weighs the answer.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from .enums import ImageModule
from .frozen_mapping import FrozenMapping, SharedMapping
from .memory import NO_MODULES_MEANING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .memory import MemoryDirectory

#: Why the names are unknown when nobody asked the printer: an offline render, validate or preview.
NOT_ASKED = "the printer was not asked"
#: Why the names of the default module (:attr:`ImageModule.C`) are unknown: a directory lists real
#: module letters only (one Allegro 2 listed just ``MODULE: A``), so which of them ``C`` is cannot
#: be read from it.
DEFAULT_MODULE_UNREAD = "C is the printer's default module; which letter it is was not read"


def command_digest(command: bytes) -> bytes:
    """What a batch keeps of a download command: enough to tell whether two pictures are the same.

    The whole hash: a name is itself a truncated hash of the picture, and two pictures under one
    name are exactly what this must tell apart, or the second prints as the first.

    Example:
        >>> command_digest(b"one") == command_digest(b"one"), command_digest(b"one") == command_digest(b"two")
        (True, False)
        >>> len(command_digest(b"one"))
        32
    """
    return hashlib.sha256(command).digest()


def _no_digests() -> dict[str, bytes]:
    return {}


@dataclass(frozen=True)
class HeldImages:
    """What a job knows about the image names its printer holds in the job's image module.

    The default knows nothing: ``listed`` is None, so a job with no clear before its downloads
    keeps every download and says the names were not read (``image_names_unread``).

    Attributes:
        listed: Names the module's directory listed, or None when it was not read.
        unread_because: Why ``listed`` is None, quoted in the ``image_names_unread`` finding.
        sent: Names an earlier label of the same batch downloaded into the module with no clear
            since, each with the :func:`command_digest` of the command that sent it; known to be
            held, without the printer having said so. The digest tells a later label whether its
            own image under that name is the same picture.
        placed: Names the printer listed that an earlier label of the batch placed without
            downloading, each with the :func:`command_digest` of the picture that label took the
            listed image for. A later label with a different picture under that name names it
            with the next salt instead, as for a name in ``sent``; the printer's own picture under
            the name stays unknown.
        reset_reported: The directory answer that gave this carried the printer's reset report,
            which it gives once: the read took it, and a sender has nothing left to ask for.

    ``sent`` and ``placed`` are read-only copies of the mappings given, so a value stays what it
    was made with, however the caller's dict changes afterwards.

    Example:
        >>> HeldImages(listed=frozenset({"LOGO"})).holds("LOGO"), HeldImages().holds("LOGO")
        (True, False)
    """

    listed: frozenset[str] | None = None
    unread_because: str = NOT_ASKED
    # Left out of the hash, which a dict has none of; equal values still hash alike.
    sent: Mapping[str, bytes] = field(default_factory=_no_digests, hash=False)
    placed: Mapping[str, bytes] = field(default_factory=_no_digests, hash=False)
    reset_reported: bool = False

    def __post_init__(self) -> None:
        """Hold ``sent`` and ``placed`` as read-only copies."""
        # Frozen stops rebinding a field, not changing the dict it holds; a read-only copy stops
        # both the caller's dict and any reader from changing what a batch carries along.
        # One that is read-only already stays, not copied: see SharedMapping.
        if not isinstance(self.sent, FrozenMapping):
            object.__setattr__(self, "sent", FrozenMapping(self.sent))
        if not isinstance(self.placed, FrozenMapping):
            object.__setattr__(self, "placed", FrozenMapping(self.placed))

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

    def digest_for(self, name: str) -> bytes | None:
        """Return the :func:`command_digest` of the picture the batch knows, or took, the printer to hold under ``name``.

        A name the batch sent gives the digest of the command that sent it, a listed name an earlier
        label placed gives that of the picture that label took it for; any other name gives None.

        Example:
            >>> held = HeldImages(listed=frozenset({"D1"}), sent={"D2": b"two"}, placed={"D1": b"one"})
            >>> held.digest_for("D1"), held.digest_for("D2"), held.digest_for("D3")
            (b'one', b'two', None)
        """
        sent = self.sent.get(name)
        return sent if sent is not None else self.placed.get(name)

    def as_listed(self, listed: frozenset[str]) -> HeldImages:
        """Return what the job knows once a read of the module listed ``listed``.

        The listing is what the module holds now: a name the batch sent or placed stays known,
        with its digest, only where the listing shows it (a download into a full module is lost
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
            sent={name: digest for name, digest in self.sent.items() if name in listed},
            placed={name: digest for name, digest in self.placed.items() if name in listed},
        )


#: The module is known to be empty: a clear just emptied it.
EMPTY_MODULE = HeldImages(listed=frozenset())


@dataclass(frozen=True)
class HeldChange:
    """What one label's job does to what the printer holds.

    Attributes:
        emptied: Nothing from before the label is left, the listing included: a clear ran.
        sent: The names the label downloaded after that, each with its :func:`command_digest`.
        removed: The names the clear after the label takes out again.
        placed: The listed names the label placed as its own picture, each with that digest.
    """

    emptied: bool = False
    sent: Mapping[str, bytes] = field(default_factory=_no_digests)
    removed: frozenset[str] = frozenset()
    placed: Mapping[str, bytes] = field(default_factory=_no_digests)

    def applied_to(self, held: HeldImages) -> HeldImages:
        """Return what the printer holds after the label, ``held`` being what it held before."""
        if self.emptied:
            return HeldImages(listed=frozenset(), sent=self.sent)
        kept = {name: digest for name, digest in {**held.sent, **self.sent}.items() if name not in self.removed}
        return HeldImages(
            listed=held.listed,
            unread_because=held.unread_because,
            sent=kept,
            placed={**held.placed, **self.placed},
        )


class CarriedNames:
    """What the printer holds across the labels of a batch, in one map changed label by label.

    :meth:`HeldChange.applied_to` copies every name for every label: at 100000 names that is 14 ms a
    label, and the batch is quadratic. A caller that needs only the state of the current label takes
    :attr:`held`, which shares the map, and must not keep it past the next :meth:`apply`.
    """

    def __init__(self, start: HeldImages) -> None:
        """Start from what the printer holds before the first label."""
        self._sent = dict(start.sent)
        self._placed = dict(start.placed)
        self.held = replace(start, sent=SharedMapping(self._sent), placed=SharedMapping(self._placed))

    def apply(self, change: HeldChange) -> None:
        """Take in what the label just rendered does to the module."""
        if change.emptied:
            self._sent.clear()
            self._placed.clear()
        self._sent.update(change.sent)
        for name in change.removed:
            self._sent.pop(name, None)
        self._placed.update(change.placed)
        before = self.held
        self.held = replace(
            before,
            listed=frozenset() if change.emptied else before.listed,
            unread_because=NOT_ASKED if change.emptied else before.unread_because,
            reset_reported=False,
        )


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
    "CarriedNames",
    "HeldChange",
    "HeldImages",
    "command_digest",
    "held_images_from",
]
