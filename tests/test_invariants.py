"""The import-time checks of the lookup tables and of the typed ``model_copy`` updates."""

from __future__ import annotations

from enum import Enum
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
