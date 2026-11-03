"""What a batch with no clear keeps of the names the printer holds: nothing, from one label to the next.

The printer's directory is the one source (user, 2026-11-02): it may have been reset, or another
program may have printed, between two labels, so a list kept by the batch would be wrong exactly
then. On a two-way link each label is read before its downloads; where no printer can be read, a
batch whose labels need that is refused.
"""

from __future__ import annotations

import gc
import sys
import tracemalloc
from typing import TYPE_CHECKING

import pytest

from datamax_dpl import ReportLimits, check_batch, iter_batch
from datamax_dpl.domain.enums import FindingCode, ImageModule, Severity
from datamax_dpl.domain.label import Label, Qr, Text
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    from datamax_dpl.application.use_cases import BatchEntry

#: A plug-in module: no clear may run, so each label's bytes depend on what the printer holds. A
#: model with no format length limit, so that four codes fit one label.
_NO_CLEAR = RenderOptions(model="dmx-600", image_module=ImageModule.B)
#: The model's own RAM module with both clear keys on: the default route, which needs no names.
_CLEARED = RenderOptions(model="allegro-2")
_FEW = ReportLimits(findings=1, downloads=1)
#: Four codes per record, every one a picture no other record has. Short data keeps each a version
#: 1 symbol (21 modules square): what a name holds does not grow with its picture, while the time
#: to draw it does. segno scores every mask in pure Python, and on CPython 3.12 a trace function
#: (coverage's, in CI) together with tracemalloc makes that loop about ten times slower than either
#: alone (3.13 is not affected), and with version 10 symbols this test runs past the 300 s
#: per-test timeout on a 3.12 CI runner.
_FOUR = Label(elements=[Qr(x=1, y=1 + 300 * n, data=f"{{a}}-{n}") for n in range(4)])
_SMALL = 20
_LARGE = 160
#: What the memory held may grow by between the two sizes: a few small objects, no name per record.
#: The digest map this replaced grew by about 150 bytes a name, 84 KB over these 560 names.
_FLAT_BYTES = 16 * 1024
#: Why the check skips 3.10: there what is held at the last record, after a full collection, grew by
#: 11 to 14 KB between these sizes (3.10.22, two runs, 2026-11-03), within 5 KB of the bound, so a
#: hold of a name per label would show only at about twice these sizes. 3.11 to 3.15 grew by under
#: 5 KB (two runs each).
_RAMP_310 = "3.10 holds 11 to 14 KB more at 160 records than at 20 with nothing of the batch kept, near the bound"


def _held_at_the_last(records: int) -> int:
    """The traced memory the batch holds while its last record is checked, its garbage collected first.

    Read there rather than as the run's peak: whatever the batch kept of each label is all still held
    at its last record, while the peak on 3.11 to 3.13 also counts what the interpreter keeps for
    reuse (free lists) and garbage not yet collected, which grew by about 125 KB between these sizes
    with nothing of the batch held (a full collection drops it to under 5 KB).
    """
    rows = [{"a": str(number)} for number in range(records)]
    seen = 0
    held = 0

    def measure(_entry: BatchEntry) -> None:
        nonlocal seen, held
        seen += 1
        if seen == records:
            gc.collect()
            held = tracemalloc.get_traced_memory()[0]

    tracemalloc.start()
    try:
        report = check_batch(_FOUR, rows, _NO_CLEAR, read_names_at_send=True, limits=_FEW, each=measure)
    finally:
        tracemalloc.stop()
    assert report.ok
    assert report.downloads_total == 4 * records
    assert held > 0, "the last record was never measured"
    return held


@pytest.mark.os_agnostic
@pytest.mark.skipif(sys.version_info < (3, 11), reason=_RAMP_310)
def test_a_no_clear_batch_holds_no_more_however_many_distinct_images_it_downloads() -> None:
    _held_at_the_last(_SMALL)  # warms what is imported and cached once, which is no cost of the batch
    small, large = _held_at_the_last(_SMALL), _held_at_the_last(_LARGE)
    assert large - small < _FLAT_BYTES, f"{small} at {_SMALL} records, {large} at {_LARGE}"


@pytest.mark.os_agnostic
def test_a_later_label_downloads_the_image_an_earlier_one_downloaded_as_nothing_is_kept() -> None:
    """Whether the printer still holds it is for the read before that label to say, not for the batch."""
    label = Label(elements=[Qr(x=1, y=1, data="SAME")])
    rows: list[dict[str, str]] = [{}] * 3
    jobs = list(iter_batch(label, rows, _NO_CLEAR, read_names_at_send=True))
    assert [len(job.downloads) for job in jobs] == [1, 1, 1]
    assert all(job.names_unread for job in jobs), "each label's names are read before its downloads"
    assert all(job.ok for job in jobs)
    assert not [f for job in jobs for f in job.findings if f.code is FindingCode.IMAGE_NAMES_UNREAD]


@pytest.mark.os_agnostic
def test_a_no_clear_batch_with_images_is_refused_where_no_printer_is_read_before_each_label() -> None:
    """From the second label on: a label before it in the batch may have left any of its names."""
    label = Label(elements=[Qr(x=1, y=1, data="{a}")])
    jobs = list(iter_batch(label, [{"a": "A"}, {"a": "B"}, {"a": "C"}], _NO_CLEAR))
    refused = [[f for f in job.findings if f.code is FindingCode.BATCH_IMAGE_NAMES_UNREAD] for job in jobs]
    assert [len(found) for found in refused] == [0, 1, 1]
    message = refused[1][0]
    assert message.severity is Severity.ERROR
    assert "two-way printer" in message.message
    assert "clear_graphics_before_label" in message.message
    report = check_batch(label, [{"a": "A"}, {"a": "B"}], _NO_CLEAR)
    assert not report.ok


@pytest.mark.os_agnostic
def test_the_first_record_of_a_no_clear_batch_is_judged_as_a_single_label_is() -> None:
    """No label of the batch precedes it, so the refusal's reason cannot hold: it says its names were not read."""
    label = Label(elements=[Qr(x=1, y=1, data="{a}")])
    [first] = list(iter_batch(label, [{"a": "A"}], _NO_CLEAR))
    codes = [finding.code for finding in first.findings]
    assert FindingCode.BATCH_IMAGE_NAMES_UNREAD not in codes
    assert FindingCode.IMAGE_NAMES_UNREAD in codes
    assert len(first.downloads) == 1, "it keeps its download, as a single label does"
    report = check_batch(label, [{"a": "A"}], _NO_CLEAR)
    assert report.ok


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("options", "label"),
    [
        (_CLEARED, Label(elements=[Qr(x=1, y=1, data="{a}")])),
        (_NO_CLEAR, Label(elements=[Text(x=1, y=1, text="{a}")])),
    ],
    ids=["cleared-before-each-label", "no-image"],
)
def test_a_batch_that_needs_no_names_is_not_refused(options: RenderOptions, label: Label) -> None:
    """The control: a clear before each label, or no image at all, makes the names matter to nothing."""
    report = check_batch(label, [{"a": "A"}, {"a": "B"}], options)
    assert report.ok
    assert FindingCode.BATCH_IMAGE_NAMES_UNREAD not in report.finding_counts
