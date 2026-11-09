"""Cutting every N labels when each label of a batch is its own format.

DPL's cut-by commands (``:nnnn``, ``cnn``) count copies of ONE format, and the manual requires N
to be below the quantity. A data-driven batch renders one format per record with quantity 1, so
those commands cannot express "cut after every 5 of these 40 different labels".

Where the job sets the cutter itself with ``<STX>V``, the batch turns it on only for every N-th
record and the last one. Measured 2026-11-09 on one Allegro 2: with the cutter on it cuts every
format's label by itself, whatever ``<STX>o`` follows, and ``<STX>o`` right after ``<STX>V1`` cuts
twice, so the cutter bit is the cut and ``<STX>o`` stays out.

Where the job does not set it, the batch sends ``<STX>o`` (cut now) after every N-th record and
after the last one. Whether the printer then also cuts each label on its own depends on its cutter
settings, which the job does not control; the finding this module adds says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .enums import Attribute, FindingCode, Unit
from .findings import Finding, FindingCollector
from .job import sends_cutter_bit
from .profiles import Feature, get_profile
from .render_context import (
    RenderContext,
    RenderOptions,
    resolved_image_module,
    resolved_label_state,
    resolved_printer_state,
)
from .units import DplUnit

if TYPE_CHECKING:
    from .label import Cut

CUT_NOW = b"\x02o"
#: Default of ``[printer] max_reported_findings``: a whole batch's findings can pass any memory
#: (399 warnings x 100000 records), and past this many nobody reads them one by one.
MAX_REPORTED_FINDINGS = 1000
#: Default of ``[printer] max_reported_downloads``: one image name per record grows the same way.
MAX_REPORTED_DOWNLOADS = 1000


def _context(options: RenderOptions, findings: FindingCollector) -> RenderContext:
    """The context a batch asks about the model's features; its findings go to ``findings``."""
    profile = get_profile(options.model)
    return RenderContext(
        profile=profile,
        firmware=options.firmware,
        options=options.options,
        unit=Unit.MM,
        dpl_unit=DplUnit.INCH_HUNDREDTHS,
        findings=findings,
        image_module=resolved_image_module(options, profile),
        # A batch has no label of its own, so no label media wins over the options' here.
        printer_state=resolved_printer_state(options, profile, label_media=None),
        configured=resolved_label_state(options),
        cutter_installed=options.cutter_installed,
        default_attribute=Attribute.XOR,
    )


@dataclass(frozen=True)
class CutPlan:
    """Where a batch cuts across its records, decided record by record, so nothing grows with their number.

    Attributes:
        every: A cut follows every this many records; None when the batch sends no cut.
        at_end: A cut also follows the last record.
        findings: The batch-level findings.
        by_cutter_bit: Each record turns the cutter on or off itself, so no ``<STX>o`` goes out.
    """

    every: int | None
    at_end: bool
    findings: tuple[Finding, ...]
    by_cutter_bit: bool = False

    def follows(self, number: int, *, last: bool) -> bool:
        """Whether a cut follows record ``number``, counted from 1; ``last`` when it is the last one."""
        return self.every is not None and (number % self.every == 0 or (last and self.at_end))

    def cut_now_follows(self, number: int, *, last: bool) -> bool:
        """Whether ``<STX>o`` follows record ``number``, counted from 1; ``last`` when it is the last one."""
        return not self.by_cutter_bit and self.follows(number, last=last)

    def leaves_uncut(self, number: int, *, last: bool) -> bool:
        """Whether record ``number``, counted from 1, must print with the cutter off."""
        return self.by_cutter_bit and not self.follows(number, last=last)


#: The plan of a batch that cuts across no records.
NO_CUTS = CutPlan(every=None, at_end=False, findings=())


@dataclass(frozen=True)
class BatchCuts:
    """Where a batch cuts across its records, and the batch-level findings.

    Attributes:
        cut_after: For each record, whether a cut follows it.
        findings: The batch-level findings.
    """

    cut_after: tuple[bool, ...]
    findings: tuple[Finding, ...]


def batch_cuts(count: int, *, cut: Cut, options: RenderOptions) -> BatchCuts:
    """Return, for each of ``count`` records, whether a cut follows it, and the batch-level findings.

    Example:
        >>> from datamax_dpl.domain.label import Cut
        >>> batch_cuts(3, cut=Cut(every=2), options=RenderOptions(model="dmx-600")).cut_after
        (False, True, True)
    """
    plan = cut_plan(cut, options=options)
    flags = tuple(plan.follows(number, last=number == count) for number in range(1, count + 1))
    return BatchCuts(cut_after=flags, findings=plan.findings)


def cut_plan(cut: Cut, *, options: RenderOptions) -> CutPlan:
    """Return where a batch of ``cut`` cuts, whatever its length, and the batch-level findings.

    A cut follows every ``cut.every``-th record, and the last one when ``cut.at_end``. A job that
    sets the cutter itself makes each cut with the record's cutter bit; any other job needs the
    cut-now command, and without one no cut follows any record, which the findings then report.
    """
    findings = FindingCollector(options.policy)
    ctx = _context(options, findings)
    if sends_cutter_bit(ctx):
        return CutPlan(every=cut.every, at_end=cut.at_end, findings=(), by_cutter_bit=True)
    if not ctx.supports(Feature.CUT_NOW):
        findings.deviate(
            FindingCode.BATCH_CUT_UNSUPPORTED,
            f"cutting every {cut.every} labels of a batch needs the cut-now command: "
            f"{ctx.requirement(Feature.CUT_NOW)}",
            fallback="the printer cuts as its own cutter setting says",
        )
        return CutPlan(every=None, at_end=False, findings=findings.findings())
    findings.warn(
        FindingCode.BATCH_CUT_UNVERIFIED,
        f"a cut command follows every {cut.every}th label; this job does not set the cutter itself, so the "
        "printer must not also cut each label by itself (unverified on hardware)",
    )
    return CutPlan(every=cut.every, at_end=cut.at_end, findings=findings.findings())


def with_cut(job: bytes, closing: bytes, *, cut: bool) -> bytes:
    """Return a record's job as the batch sends it: with a cut before its closing clears when ``cut``.

    The cut goes before the clears, so they stay the last of its bytes as in a single label's job:
    the next record, or the next program, starts from an empty module.

    Example:
        >>> with_cut(b"Aq", b"q", cut=True), with_cut(b"Aq", b"q", cut=False)
        (b'A\\x02oq', b'Aq')
    """
    if not cut:
        return job
    return b"".join([job[: len(job) - len(closing)], CUT_NOW, closing])


__all__ = [
    "CUT_NOW",
    "MAX_REPORTED_DOWNLOADS",
    "MAX_REPORTED_FINDINGS",
    "NO_CUTS",
    "BatchCuts",
    "CutPlan",
    "batch_cuts",
    "cut_plan",
    "with_cut",
]
