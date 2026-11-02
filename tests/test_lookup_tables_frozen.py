"""The module-level tables every render reads: no caller can change them for the whole process."""

from __future__ import annotations

import copy
import operator
from typing import TYPE_CHECKING, Any

import pytest

from datamax_dpl.adapters.transport import uri
from datamax_dpl.domain import charset, profiles
from datamax_dpl.domain.frozen_mapping import FrozenMapping

if TYPE_CHECKING:
    from collections.abc import Mapping

TABLES: list[tuple[Any, str, Any]] = [
    (charset, "FONT_BYTES", 1),
    (charset, "SYMBOL_SET_CODECS", "PC"),
    (profiles, "MODEL_PROFILES", "dmx-600"),
    (profiles, "PROFILES", "generic"),
    (uri, "_BYTESIZES", "8"),
    (uri, "_STOPBITS", "1"),
]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("module", "name", "key"), TABLES, ids=[row[1] for row in TABLES])
def test_a_lookup_table_refuses_a_write_and_keeps_its_contents(module: Any, name: str, key: Any) -> None:
    table = getattr(module, name)
    before = table[key]
    with pytest.raises(TypeError):
        operator.setitem(table, key, "changed")
    with pytest.raises(TypeError):
        operator.delitem(table, key)
    assert table[key] is before


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("module", "name", "key"), TABLES, ids=[row[1] for row in TABLES])
def test_a_lookup_table_uses_the_read_only_mapping_that_copies(module: Any, name: str, key: Any) -> None:
    """A MappingProxyType would refuse the write too and then fail any copy of a value holding it."""
    table: Mapping[Any, Any] = getattr(module, name)
    assert isinstance(table, FrozenMapping)
    assert key in copy.deepcopy(table)
