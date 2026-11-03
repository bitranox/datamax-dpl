"""What a batch with no clear keeps of the names the printer holds: nothing, from one label to the next.

The printer's directory is the one source (user, 2026-11-02): it may have been reset, or another
program may have printed, between two labels, so a list kept by the batch would be wrong exactly
then. On a two-way link each label is read before its downloads; where no printer can be read, a
batch whose labels need that is refused.
"""

from __future__ import annotations

import sys
import tracemalloc

import pytest

from datamax_dpl import ReportLimits, check_batch, iter_batch
from datamax_dpl.domain.enums import FindingCode, ImageModule, Severity
from datamax_dpl.domain.label import Label, Qr, Text
from datamax_dpl.domain.render_context import RenderOptions

#: A plug-in module: no clear may run, so each label's bytes depend on what the printer holds. A
#: model with no format length limit, so that four large codes fit one label.
_NO_CLEAR = RenderOptions(model="dmx-600", image_module=ImageModule.B)
#: The model's own RAM module with both clear keys on: the default route, which needs no names.
_CLEARED = RenderOptions(model="allegro-2")
_FEW = ReportLimits(findings=1, downloads=1)
#: Four large codes per record, every one a picture no other record has.
_LONG = "x" * 200
_FOUR_LARGE = Label(elements=[Qr(x=1, y=1 + 300 * n, data=f"{{a}}-{n}-{_LONG}") for n in range(4)])
_SMALL = 20
_LARGE = 160
#: What the peak may grow by between the two sizes: a few small objects, no name per record. The
#: digest map this replaced grew by about 150 bytes a name, 84 KB over these 560 names.
_FLAT_BYTES = 16 * 1024
#: Why the flat check skips 3.10: the interpreter itself holds up to about 100 KB more over the first
#: ~2000 records of any batch, a text label's as well, then stays flat to 8000 (measured 2026-11-03).
#: These sizes sit inside that ramp; 3.11 to 3.15 grow by at most 7 KB over it.
_RAMP_310 = "3.10 holds up to about 100 KB more over the first ~2000 records of any batch, then stays flat"


def _peak(records: int) -> int:
    rows = [{"a": str(number)} for number in range(records)]
    tracemalloc.start()
    try:
        report = check_batch(_FOUR_LARGE, rows, _NO_CLEAR, read_names_at_send=True, limits=_FEW)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert report.ok
    assert report.downloads_total == 4 * records
    return peak


@pytest.mark.os_agnostic
@pytest.mark.skipif(sys.version_info < (3, 11), reason=_RAMP_310)
def test_a_no_clear_batch_s_peak_is_flat_however_many_distinct_images_it_downloads() -> None:
    _peak(_SMALL)  # warms what is imported and cached once, which is no cost of the batch
    small, large = _peak(_SMALL), _peak(_LARGE)
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
    label = Label(elements=[Qr(x=1, y=1, data="{a}")])
    jobs = list(iter_batch(label, [{"a": "A"}, {"a": "B"}], _NO_CLEAR))
    refused = [f for job in jobs for f in job.findings if f.code is FindingCode.BATCH_IMAGE_NAMES_UNREAD]
    assert len(refused) == 2
    assert all(f.severity is Severity.ERROR for f in refused)
    assert "two-way printer" in refused[0].message
    assert "clear_graphics_before_label" in refused[0].message
    report = check_batch(label, [{"a": "A"}, {"a": "B"}], _NO_CLEAR)
    assert not report.ok


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
