"""Checks a module runs on its own tables at import, so a mistake fails there and not in one rare path.

Each check is a plain call with its branch here, under a unit test, rather than an unexercised
``if ...: raise`` at every call site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel


class TypedDictClass(Protocol):
    """A ``TypedDict`` class, as far as these checks read it."""

    __name__: str
    __required_keys__: frozenset[str]


def require_same_keys(table: Iterable[object], members: Iterable[object], *, what: str) -> None:
    """Require ``table`` to have an entry for exactly ``members``.

    Args:
        table: The lookup table.
        members: What it must be keyed by: an enum, or the classes of a union.
        what: What the table holds, for the message.

    Raises:
        RuntimeError: A member has no entry, or an entry belongs to no member.

    Example:
        >>> require_same_keys({1: "a"}, [1], what="a letter")
        >>> require_same_keys({1: "a"}, [1, 2], what="a letter")
        Traceback (most recent call last):
        ...
        RuntimeError: every member needs a letter: missing [2], unexpected []
    """
    wanted, keys = set(members), set(table)
    if keys != wanted:
        missing, unexpected = sorted(wanted - keys, key=str), sorted(keys - wanted, key=str)
        raise RuntimeError(f"every member needs {what}: missing {missing}, unexpected {unexpected}")


def require_update_fields(update: TypedDictClass, *models: type[BaseModel]) -> None:
    """Require every key of ``update`` to be a field of each of ``models``.

    ``model_copy(update=...)`` takes any key and sets it unchecked, so the ``TypedDict`` that types
    such an update is checked against the models it is applied to.

    Args:
        update: The ``TypedDict`` typing the update.
        models: The models it is applied to.

    Raises:
        RuntimeError: A key is not a field of one of the models.

    Example:
        >>> from typing import TypedDict
        >>> from pydantic import BaseModel
        >>> class Model(BaseModel):
        ...     size: int
        >>> class Update(TypedDict):
        ...     size: int
        >>> require_update_fields(Update, Model)
    """
    for model in models:
        unknown = sorted(update.__required_keys__ - model.model_fields.keys())
        if unknown:
            raise RuntimeError(f"{update.__name__} names {unknown}, which {model.__name__} does not have")


__all__ = ["TypedDictClass", "require_same_keys", "require_update_fields"]
