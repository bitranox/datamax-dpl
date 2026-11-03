"""Every package type a caller meets in an exported signature or field can be imported from the root.

The test walks the type hints of each exported function, of each exported class (its fields), and of
each public method and property, and names every type of this package found there that the root does
not export. A type left out on purpose is listed below with its reason.
"""

from __future__ import annotations

import collections.abc
import importlib
import inspect
import pkgutil
import typing

import pytest

import datamax_dpl

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
        yield from _package_types(argument)
    origin = typing.get_origin(hint) or hint
    name = getattr(origin, "__name__", None)
    if name and (getattr(origin, "__module__", "") or "").startswith("datamax_dpl"):
        yield name


def _hinted(item: object, names: dict[str, object]) -> typing.Iterator[tuple[str, object]]:
    """Each hinted parameter, return or field of ``item`` with its hint."""
    scope = {**names, **vars(inspect.getmodule(item))}
    yield from typing.get_type_hints(item, globalns=scope).items()


def _members(name: str, item: object) -> typing.Iterator[tuple[str, object]]:
    """``item`` and, for a class, each public method and property's getter, named for the message."""
    yield name, item
    if not inspect.isclass(item):
        return
    for attribute, member in vars(item).items():
        function = member.fget if isinstance(member, property) else member
        if inspect.isfunction(function) and (not attribute.startswith("_") or attribute == "__init__"):
            yield f"{name}.{attribute}", function


def _unexported() -> dict[str, set[str]]:
    names = _namespace()
    exported = set(datamax_dpl.__all__)
    found: dict[str, set[str]] = {}
    for name in sorted(exported):
        item = getattr(datamax_dpl, name)
        if not (inspect.isfunction(item) or inspect.isclass(item)):
            continue
        for where, member in _members(name, item):
            for parameter, hint in _hinted(member, names):
                for type_name in _package_types(hint):
                    if type_name not in exported:
                        found.setdefault(type_name, set()).add(f"{where}:{parameter}")
    return found


@pytest.mark.os_agnostic
def test_every_type_an_exported_signature_names_is_exported_or_kept_out_with_a_reason() -> None:
    found = _unexported()
    missing = {name: sorted(places)[:3] for name, places in found.items() if name not in _KEPT_OUT}
    assert not missing, f"export these from datamax_dpl, or keep them out with a reason: {missing}"


@pytest.mark.os_agnostic
def test_each_type_kept_out_is_still_named_somewhere() -> None:
    """A reason for a type no signature names any more is a stale entry."""
    assert set(_KEPT_OUT) <= set(_unexported())
