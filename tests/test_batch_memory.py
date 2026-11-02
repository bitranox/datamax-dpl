"""What a batch holds in memory while it is sent: each record's data, not its filled label."""

from __future__ import annotations

import gc
import tracemalloc

import pytest
from pydantic import ValidationError

from datamax_dpl.application.use_cases import record_renders, render_batch
from datamax_dpl.domain.held_images import HeldImages
from datamax_dpl.domain.label import Label, Text
from datamax_dpl.domain.placeholders import MissingFieldError
from datamax_dpl.domain.render_context import RenderOptions

_DMX = RenderOptions(model="dmx-600")
_FIELDS = 399
#: A filled label of 399 fields held about 360 KB, so this many records held about 72 MB.
_RECORDS = 200
#: Well below one filled label per record, well above a record's own dict and its render.
_MOST_KEPT_PER_RECORD = 20_000


def _wide_label() -> Label:
    return Label(elements=[Text(x=0, y=i, text="{a}") for i in range(_FIELDS)])


def _kept_while_rendering(records: list[dict[str, str]]) -> int:
    """Bytes ``record_renders`` keeps once it has returned, the renders still held."""
    label = _wide_label()
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        renders = record_renders(label, records, _DMX)
        gc.collect()
        kept = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    assert len(renders) == len(records)
    return kept


@pytest.mark.os_agnostic
def test_a_batch_keeps_no_filled_label_for_the_send() -> None:
    """The bound sits under one filled label per record: 200 x 360 KB is what used to stay."""
    records = [{"a": str(i)} for i in range(_RECORDS)]
    kept = _kept_while_rendering(records)
    assert kept < _RECORDS * _MOST_KEPT_PER_RECORD


@pytest.mark.os_agnostic
def test_a_render_fills_its_label_when_it_runs_and_gets_the_same_job() -> None:
    label = Label(elements=[Text(x=10, y=10, text="{a}")])
    records = [{"a": "one"}, {"a": "two"}]
    renders = record_renders(label, records, _DMX)
    batch = render_batch(label, records, _DMX)
    assert [r(HeldImages(), halve=False).data for r in renders] == [r.data for r in batch.results]
    assert b"two" in renders[1](HeldImages(), halve=False).data
    assert b"CAL" in renders[1].calibration("CAL").data


@pytest.mark.os_agnostic
def test_every_record_is_checked_before_any_render_runs() -> None:
    """A library caller gets the documented raises from ``record_renders``, not half way through a send."""
    label = Label(elements=[Text(x=10, y=10, text="{a}")])
    with pytest.raises(MissingFieldError):
        record_renders(label, [{"a": "one"}, {"b": "two"}], _DMX)
    with pytest.raises(ValidationError):
        record_renders(label, [{"a": "one"}, {"a": "x" * 300}], _DMX)
