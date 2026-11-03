"""Every package type a caller meets in an exported signature or field can be imported from the root.

The test walks the type hints of each exported function, of each exported class (its fields), and of
each public method, property, class and static method, ``__call__`` and overload, on the class and on
each of its bases from this package; an exported object that is neither (a callable instance) is
walked through its class's members. The arguments of a ``Callable`` count as hints. It names every
type of this package found there that the root does not export; one left out on purpose is listed
below with its reason.
"""

from __future__ import annotations

import collections.abc
import importlib
import inspect
import pkgutil
import sys
import typing

import pytest

import datamax_dpl

if typing.TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from datamax_dpl.application.use_cases import BatchJob

#: Types an exported signature names that a caller never builds, passes or annotates, and why.
_KEPT_OUT = {
    "BatchJob": "check_batch builds it and the report reads it; a caller reaches it through the report's methods",
    "CutPlan": "check_batch builds it from the options; a caller reads the cuts through BatchReport.cuts()",
}


def _namespace() -> dict[str, object]:
    """Every name of the package's modules, and of typing and collections.abc, to resolve string hints in."""
    names: dict[str, object] = {}
    for info in pkgutil.walk_packages(datamax_dpl.__path__, "datamax_dpl."):
        if info.name.endswith("__main__"):
            continue
        module = importlib.import_module(info.name)
        for key, value in vars(module).items():
            names.setdefault(key, value)
    for source in (collections.abc, typing):
        for key in dir(source):
            names.setdefault(key, getattr(source, key))
    return names


def _package_types(hint: object) -> typing.Iterator[str]:
    """The names of this package's types in ``hint``, its arguments included; a type variable stands for none."""
    if isinstance(hint, typing.TypeVar):
        return
    for argument in typing.get_args(hint):
        # A Callable's parameters come as one list: Callable[[X], Y] gives ([X], Y).
        parts = typing.cast("list[object]", argument) if isinstance(argument, list) else [argument]
        for part in parts:
            yield from _package_types(part)
    origin = typing.get_origin(hint) or hint
    name = getattr(origin, "__name__", None)
    if name and (getattr(origin, "__module__", "") or "").startswith("datamax_dpl"):
        yield name


def _hinted(item: object, names: dict[str, object]) -> typing.Iterator[tuple[str, object]]:
    """Each hinted parameter, return or field of ``item`` with its hint."""
    scope = {**names, **vars(inspect.getmodule(item))}
    yield from typing.get_type_hints(item, globalns=scope).items()


#: The dunder methods a caller calls or implements, walked beside the public ones.
_CALLED_DUNDERS = frozenset({"__init__", "__call__"})


def _functions(member: object) -> typing.Iterator[object]:
    """The functions a class attribute holds: a method, a getter, a class or static method's function, overloads."""
    if isinstance(member, property):
        member = member.fget
    elif isinstance(member, classmethod):
        member = typing.cast("classmethod[object, ..., object]", member).__func__
    elif isinstance(member, staticmethod):
        member = typing.cast("staticmethod[..., object]", member).__func__
    if not inspect.isfunction(member):
        return
    yield member
    # get_overloads is 3.11+; on 3.10 the implementation's own hints, which cover each overload, stay.
    if sys.version_info >= (3, 11):
        yield from typing.get_overloads(member)


def _package_bases(cls: type) -> list[type]:
    """``cls`` and each base of it from this package (or from its own module): their members are the class's too."""
    own = (cls.__module__, "datamax_dpl")
    return [base for base in cls.__mro__ if (base.__module__ or "").startswith(own)]


def _members(name: str, item: object) -> typing.Iterator[tuple[str, object]]:
    """``item`` and each member a caller meets through it, named for the message.

    A class gives its methods and its bases' from this package; any other callable (an instance
    with ``__call__``) gives its class's, but not the class itself, which the caller never names.
    """
    if inspect.isfunction(item) or inspect.isclass(item):
        yield name, item
    owner = item if inspect.isclass(item) else type(item)
    if not inspect.isclass(item) and not callable(item):
        return
    for cls in _package_bases(owner):
        for attribute, member in vars(cls).items():
            if attribute.startswith("_") and attribute not in _CALLED_DUNDERS:
                continue
            for function in _functions(member):
                yield f"{name}.{attribute}", function


def _unexported_in(items: Mapping[str, object], exported: set[str]) -> dict[str, set[str]]:
    """Each package type ``items`` name that ``exported`` lacks, with where."""
    names = _namespace()
    found: dict[str, set[str]] = {}
    for name in sorted(items):
        for where, member in _members(name, items[name]):
            for parameter, hint in _hinted(member, names):
                for type_name in _package_types(hint):
                    if type_name not in exported:
                        found.setdefault(type_name, set()).add(f"{where}:{parameter}")
    return found


def _unexported() -> dict[str, set[str]]:
    exported = set(datamax_dpl.__all__)
    return _unexported_in({name: getattr(datamax_dpl, name) for name in exported}, exported)


@pytest.mark.os_agnostic
def test_every_type_an_exported_signature_names_is_exported_or_kept_out_with_a_reason() -> None:
    found = _unexported()
    missing = {name: sorted(places)[:3] for name, places in found.items() if name not in _KEPT_OUT}
    assert not missing, f"export these from datamax_dpl, or keep them out with a reason: {missing}"


@pytest.mark.os_agnostic
def test_each_type_kept_out_is_still_named_somewhere() -> None:
    """A reason for a type no signature names any more is a stale entry."""
    assert set(_KEPT_OUT) <= set(_unexported())


def _takes_a_job(callback: Callable[[BatchJob], None]) -> None:
    del callback


class _Planted:
    """A class whose every blind spot of a walk names BatchJob."""

    @classmethod
    def from_job(cls, job: BatchJob) -> _Planted:
        del job
        return cls()

    @staticmethod
    def check(job: BatchJob) -> None:
        del job

    def __call__(self, job: BatchJob) -> None:
        del job


class _PlantedChild(_Planted):
    """Inherits every member above: a walk of its own ``vars`` alone finds none."""


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("item", "where"),
    [
        pytest.param(_takes_a_job, "planted:callback", id="callable-parameter"),
        pytest.param(_Planted, "planted.from_job:job", id="classmethod"),
        pytest.param(_Planted, "planted.check:job", id="staticmethod"),
        pytest.param(_Planted, "planted.__call__:job", id="call"),
        pytest.param(_PlantedChild, "planted.from_job:job", id="inherited"),
        pytest.param(_Planted(), "planted.__call__:job", id="callable-instance"),
    ],
)
def test_the_walk_finds_a_type_in_each_place_a_signature_can_name_it(item: object, where: str) -> None:
    """A planted unexported type in each place: a walk blind to one of them would miss a real export."""
    found = _unexported_in({"planted": item}, set(datamax_dpl.__all__))
    assert where in found.get("BatchJob", set()), found
