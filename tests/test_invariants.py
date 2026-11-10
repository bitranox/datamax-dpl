"""The import-time checks of the lookup tables and of the typed ``model_copy`` updates."""

from __future__ import annotations

import ast
from enum import Enum
from pathlib import Path
from typing import TypedDict, get_args

import pytest
from pydantic import BaseModel

from datamax_dpl.domain._invariants import require_same_keys, require_update_fields


class _Colour(Enum):
    RED = 1
    BLUE = 2


class _Size(BaseModel):
    size: int


class _Other(BaseModel):
    other: int


class _SizeUpdate(TypedDict):
    size: int


@pytest.mark.os_agnostic
def test_a_table_with_every_member_passes() -> None:
    require_same_keys({_Colour.RED: 1, _Colour.BLUE: 2}, _Colour, what="a rank")


@pytest.mark.os_agnostic
def test_a_member_without_an_entry_is_named() -> None:
    with pytest.raises(RuntimeError, match=r"needs a rank: missing \[<_Colour.BLUE: 2>\]"):
        require_same_keys({_Colour.RED: 1}, _Colour, what="a rank")


@pytest.mark.os_agnostic
def test_an_entry_of_no_member_is_named() -> None:
    with pytest.raises(RuntimeError, match=r"unexpected \['stray'\]"):
        require_same_keys({_Colour.RED: 1, _Colour.BLUE: 2, "stray": 3}, _Colour, what="a rank")


@pytest.mark.os_agnostic
def test_the_classes_of_a_union_can_key_a_table() -> None:
    require_same_keys({_Size: 1, _Other: 2}, get_args(_Size | _Other), what="a code")


@pytest.mark.os_agnostic
def test_an_update_whose_keys_are_fields_passes() -> None:
    require_update_fields(_SizeUpdate, _Size)


@pytest.mark.os_agnostic
def test_an_update_naming_a_field_of_no_model_is_refused() -> None:
    with pytest.raises(RuntimeError, match=r"_SizeUpdate names \['size'\], which _Other does not have"):
        require_update_fields(_SizeUpdate, _Size, _Other)


class _LooseUpdate(TypedDict, total=False):
    colour: str


@pytest.mark.os_agnostic
def test_an_optional_key_naming_no_field_is_refused_too() -> None:
    """A total=False TypedDict lists its keys as optional: a check of the required ones alone saw none."""
    with pytest.raises(RuntimeError, match=r"_LooseUpdate names \['colour'\], which _Size does not have"):
        require_update_fields(_LooseUpdate, _Size)


_SRC = Path(__file__).resolve().parents[1] / "src" / "datamax_dpl"


def _guarded_names(tree: ast.Module) -> set[str]:
    """The names a module passes to ``require_update_fields`` as the update type."""
    return {
        node.args[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "require_update_fields"
        and node.args
        and isinstance(node.args[0], ast.Name)
    }


def _unguarded_updates(source: str) -> list[int]:
    """The line of each ``model_copy(update=...)`` whose update is not a call of a checked ``TypedDict``."""
    tree = ast.parse(source)
    guarded = _guarded_names(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "model_copy"):
            continue
        for keyword in node.keywords:
            value = keyword.value
            checked = isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in guarded
            if keyword.arg == "update" and not checked:
                lines.append(node.lineno)
    return lines


@pytest.mark.os_agnostic
def test_every_model_copy_update_is_a_typed_dict_the_module_checks() -> None:
    """model_copy(update=...) sets a misspelt key without a word, so every update goes through a checked TypedDict."""
    sources = {
        path.relative_to(_SRC).as_posix(): path.read_text(encoding="utf-8") for path in sorted(_SRC.rglob("*.py"))
    }
    updates = sum(source.count("model_copy(update=") + source.count("model_copy(\n") for source in sources.values())
    assert updates >= 20, f"the scan found {updates} model_copy updates, too few to mean anything"
    offenders = [f"{name}:{line}" for name, source in sources.items() for line in _unguarded_updates(source)]
    assert offenders == []


@pytest.mark.os_agnostic
def test_the_update_scan_names_a_dict_literal_and_an_unchecked_typed_dict() -> None:
    source = (
        "require_update_fields(_Checked, Model)\n"
        "a.model_copy(update={'x': 1})\n"
        "b.model_copy(update=_Loose(x=1))\n"
        "c.model_copy(update=_Checked(x=1))\n"
        "d.model_copy()\n"
    )
    assert _unguarded_updates(source) == [2, 3]
