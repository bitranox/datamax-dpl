"""The read-only mapping that frozen value objects hold: it copies, pickles and ``asdict`` reads it."""

from __future__ import annotations

import copy
import dataclasses
import pickle
from typing import TYPE_CHECKING, cast

import pytest

from datamax_dpl.domain.frozen_mapping import FrozenMapping

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclasses.dataclass(frozen=True)
class _Holder:
    items: Mapping[str, int]


@pytest.mark.os_agnostic
def test_a_frozen_mapping_keeps_what_it_was_made_with_when_the_given_dict_changes() -> None:
    given = {"a": 1}
    frozen = FrozenMapping(given)
    given["b"] = 2
    assert dict(frozen) == {"a": 1}


@pytest.mark.os_agnostic
def test_a_frozen_mapping_refuses_a_write() -> None:
    frozen = FrozenMapping({"a": 1})
    with pytest.raises(TypeError):
        # Typed as a dict only to make the write; the object has no __setitem__.
        cast("dict[str, int]", frozen)["b"] = 2
    assert dict(frozen) == {"a": 1}


@pytest.mark.os_agnostic
def test_a_deep_copy_is_an_equal_frozen_mapping_of_its_own() -> None:
    frozen = FrozenMapping({"a": 1, "b": 2})
    copied = copy.deepcopy(frozen)
    assert type(copied) is FrozenMapping
    assert copied == frozen
    assert copied is not frozen


@pytest.mark.os_agnostic
@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_a_frozen_mapping_pickles_by_every_protocol(protocol: int) -> None:
    """``copy.deepcopy`` above rebuilds through the same ``__reduce__`` the pickled bytes hold."""
    assert pickle.dumps(FrozenMapping({"a": 1}), protocol=protocol)


@pytest.mark.os_agnostic
def test_dataclasses_asdict_reads_a_frozen_mapping_field() -> None:
    assert dict(dataclasses.asdict(_Holder(items=FrozenMapping({"a": 1})))["items"]) == {"a": 1}


@pytest.mark.os_agnostic
def test_a_frozen_mapping_shows_its_items() -> None:
    assert repr(FrozenMapping({"a": 1})) == "FrozenMapping({'a': 1})"
