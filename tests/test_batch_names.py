"""What a batch with no clear keeps of the names the printer holds: a digest per name, in one map."""

from __future__ import annotations

import time
import tracemalloc
from itertools import product

import pytest

from datamax_dpl import HeldImages, ReportLimits, check_batch, iter_batch
from datamax_dpl.domain.enums import ImageModule
from datamax_dpl.domain.held_images import command_digest
from datamax_dpl.domain.label import Label, Qr, Text
from datamax_dpl.domain.render import RenderResult, build_record
from datamax_dpl.domain.render_context import RenderOptions

#: A plug-in module: no clear may run, so every name stays known for the rest of the batch.
_NO_CLEAR = RenderOptions(model="allegro-2", image_module=ImageModule.B)
_FEW = ReportLimits(findings=1, downloads=1)
#: Four large codes per record, the product a flat figure per name hid.
_LONG = "x" * 200
_FOUR_LARGE = Label(elements=[Qr(x=1, y=1 + 300 * n, data=f"{{a}}-{n}-{_LONG}") for n in range(4)])
#: What a name may cost with its digest: a whole command of such a code is more than ten times this.
_MOST_BYTES_PER_NAME = 400
_SMALL = 20
_LARGE = 80
_TIMED_RECORDS = 20


def _peak(records: int) -> int:
    rows = [{"a": str(number)} for number in range(records)]
    tracemalloc.start()
    try:
        report = check_batch(_FOUR_LARGE, rows, _NO_CLEAR, limits=_FEW)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert report.downloads_total == 4 * records
    return peak


@pytest.mark.os_agnostic
def test_a_name_costs_a_small_constant_however_large_its_image() -> None:
    _peak(_SMALL)  # warms what is imported and cached once, which is no cost of a name
    small, large = _peak(_SMALL), _peak(_LARGE)
    per_name = (large - small) / (4 * (_LARGE - _SMALL))
    assert per_name < _MOST_BYTES_PER_NAME, f"{per_name:.0f} bytes a name; {small} at {_SMALL}, {large} at {_LARGE}"


def _seconds_per_record(names: int) -> float:
    """The best of a few short runs: what the copying costs, not what the machine was doing."""
    held = HeldImages(sent={f"N{number}": b"d" * 32 for number in range(names)})
    label = Label(elements=[Text(x=1, y=1, text="{a}")])
    jobs = iter_batch(label, [{"a": "v"}] * (_TIMED_RECORDS + 1), _NO_CLEAR, held=held)
    next(jobs)  # the one copy of the names given, which is per batch
    started = time.perf_counter()
    for _ in jobs:
        pass
    return (time.perf_counter() - started) / _TIMED_RECORDS


@pytest.mark.os_agnostic
def test_a_record_takes_as_long_with_many_names_held() -> None:
    """Copying the map per record made a whole batch quadratic: 14 ms a record at 100000 names, 0.2 ms at 1000."""
    few: list[float] = []
    many: list[float] = []
    for _ in range(4):
        few.append(_seconds_per_record(1000))
        many.append(_seconds_per_record(100_000))
    assert min(many) < 2 * min(few), f"{min(few) * 1000:.2f} ms with few names, {min(many) * 1000:.2f} ms with many"


@pytest.mark.os_agnostic
def test_the_map_carried_holds_digests_and_no_command() -> None:
    record = build_record(Label(elements=[Qr(x=1, y=1, data="v" + _LONG)]), _NO_CLEAR)
    [name] = record.result.downloads
    digest = record.held_after.digest_for(name)
    assert digest is not None
    assert len(digest) == 32, "a full digest: a truncated one could take another picture for this one"
    assert digest not in record.result.data


@pytest.mark.os_agnostic
def test_another_picture_under_a_held_name_takes_the_next_name() -> None:
    """The one use of what is kept: a name is free again only for the same picture."""
    label = Label(elements=[Qr(x=1, y=1, data="SAME")])
    first = build_record(label, _NO_CLEAR)
    [name] = first.result.downloads
    other = HeldImages(sent={name: command_digest(b"another picture")})
    same = build_record(label, _NO_CLEAR, held=first.held_after)
    assert same.result.downloads == ()
    salted = build_record(label, _NO_CLEAR, held=other)
    assert salted.result.downloads != (name,)
    assert len(salted.result.downloads) == 1


@pytest.mark.os_agnostic
def test_iter_batch_carries_what_build_record_says_record_by_record() -> None:
    """The one map must say what the pure step says, label after label, clears and removes included."""
    label = Label(elements=[Qr(x=1, y=1, data="{a}")])
    rows = [{"a": value} for value in ("A", "B", "A", "C", "B")]
    arms = (
        _NO_CLEAR,
        RenderOptions(model="allegro-2"),
        RenderOptions(model="prodigy"),
        RenderOptions(model="allegro-2", clear_graphics_before_label=False),
        RenderOptions(model="allegro-2", clear_graphics_after_label=False),
        RenderOptions(model="allegro-2", clear_graphics_before_label=False, clear_graphics_after_label=False),
    )
    # With the first code's name listed, so that a label places it (``placed``) rather than sends it.
    [listed] = build_record(Label(elements=[Qr(x=1, y=1, data="A")]), _NO_CLEAR).result.downloads
    for options, start in product(arms, (HeldImages(), HeldImages(listed=frozenset({listed})))):
        held = start
        stepwise: list[RenderResult] = []
        for row in rows:
            filled = Label(elements=[Qr(x=1, y=1, data=row["a"])])
            record = build_record(filled, options, held=held)
            stepwise.append(record.result)
            held = record.held_after
        assert list(iter_batch(label, rows, options, held=start)) == stepwise, options.model
