"""A read-only mapping that frozen value objects can hold and still copy, pickle and ``asdict``.

A frozen dataclass stops rebinding a field, not changing the dict the field holds, so a value
object holding a mapping keeps a read-only copy of it. ``types.MappingProxyType`` is read-only
too, but it cannot be deep-copied or pickled, so neither could a value holding one, nor could
``dataclasses.asdict`` read it (``asdict`` deep-copies a mapping that is not a dict).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TypeVar

_K = TypeVar("_K")
_V = TypeVar("_V")


class FrozenMapping(Mapping[_K, _V]):
    """A read-only copy of a mapping that deep-copies and pickles by every pickle protocol.

    It is rebuilt from a plain dict for copy and pickle, since protocols 0 and 1 refuse an object
    with ``__slots__`` otherwise.

    Example:
        >>> frozen = FrozenMapping({"D1": b"one"})
        >>> frozen["D1"], dict(frozen) == {"D1": b"one"}
        (b'one', True)
    """

    __slots__ = ("_items",)

    def __init__(self, items: Mapping[_K, _V]) -> None:
        """Hold a copy of ``items``, so a later change to them changes nothing here."""
        self._items = dict(items)

    def __getitem__(self, key: _K) -> _V:
        """Return the value of ``key``."""
        return self._items[key]

    def __iter__(self) -> Iterator[_K]:
        """Iterate the keys."""
        return iter(self._items)

    def __len__(self) -> int:
        """Return how many keys there are."""
        return len(self._items)

    def __repr__(self) -> str:
        """Show the items."""
        return f"{type(self).__name__}({self._items!r})"

    def __reduce__(self) -> tuple[type[FrozenMapping[_K, _V]], tuple[dict[_K, _V]]]:
        """Rebuild from a plain dict of the items, for copy and every pickle protocol."""
        return (type(self), (dict(self._items),))


__all__ = ["FrozenMapping"]
