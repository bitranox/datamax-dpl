"""Cutting every N labels when each label of a batch is its own format.

DPL's cut-by commands (``:nnnn``, ``cnn``) count copies of ONE format, and the manual requires N
to be below the quantity. A data-driven batch renders one format per record with quantity 1, so
those commands cannot express "cut after every 5 of these 40 different labels". The batch
therefore sends ``<STX>o`` (cut now) after every N-th record and after the last one.

Whether the printer then also cuts each label on its own depends on its cutter settings, which the
manual does not tie to ``<STX>o``; the finding this module adds says so, until a test print on
real hardware settles it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .enums import Attribute, FindingCode, Unit
from .findings import Finding, FindingCollector
from .profiles import Feature, get_profile, parse_firmware
from .render_context import RenderContext, RenderOptions, resolved_image_module, resolved_printer_state
from .units import DplUnit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .label import Cut

CUT_NOW = b"\x02o"


def _supports(options: RenderOptions, feature: Feature, findings: FindingCollector) -> tuple[bool, str]:
    profile = get_profile(options.model)
    ctx = RenderContext(
        profile=profile,
        firmware=parse_firmware(options.firmware),
        options=options.options,
        unit=Unit.MM,
        dpl_unit=DplUnit.INCH_HUNDREDTHS,
        findings=findings,
        image_module=resolved_image_module(options, profile),
        # A batch has no label of its own, so no label media wins over the options' here.
        printer_state=resolved_printer_state(options, profile, label_media=None),
        default_attribute=Attribute.XOR,
    )
    return ctx.supports(feature), ctx.requirement(feature)


def batch_cuts(count: int, *, cut: Cut, options: RenderOptions) -> tuple[tuple[bool, ...], tuple[Finding, ...]]:
    """Return, for each of ``count`` records, whether a cut follows it, and the batch-level findings.

    A cut follows every ``cut.every``-th record, and the last one when ``cut.at_end``; none
    follows any record on a model without the cut-now command, which the findings then report.

    Example:
        >>> from datamax_dpl.domain.label import Cut
        >>> batch_cuts(3, cut=Cut(every=2), options=RenderOptions(model="dmx-600"))[0]
        (False, True, True)
    """
    findings = FindingCollector(options.policy)
    supported, requirement = _supports(options, Feature.CUT_NOW, findings)
    if not supported:
        findings.deviate(
            FindingCode.BATCH_CUT_UNSUPPORTED,
            f"cutting every {cut.every} labels of a batch needs the cut-now command: {requirement}",
            fallback="the printer cuts as its own cutter setting says",
        )
        return (False,) * count, findings.findings()
    findings.warn(
        FindingCode.BATCH_CUT_UNVERIFIED,
        f"a cut command follows every {cut.every}th label; the printer must not also cut each label "
        "by itself (unverified on hardware)",
    )
    cuts = tuple(number % cut.every == 0 or (number == count and cut.at_end) for number in range(1, count + 1))
    return cuts, findings.findings()


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


def cut_across_records(
    jobs: Sequence[tuple[bytes, bytes]], *, cut: Cut, options: RenderOptions
) -> tuple[bytes, tuple[Finding, ...]]:
    """Join per-record jobs, inserting a cut after every ``cut.every``-th one (see :func:`batch_cuts`).

    Args:
        jobs: Each record's job and the closing bytes it ends with
            (:attr:`~datamax_dpl.domain.render.RenderResult.closing`).
        cut: How often to cut, and whether the last group is cut too.
        options: The render options, for the model's cut-now command.

    Returns:
        The joined bytes and the batch-level findings.

    Example:
        >>> from datamax_dpl.domain.label import Cut
        >>> jobs = [(b"Aq", b"q"), (b"Bq", b"q"), (b"C", b"")]
        >>> data, _ = cut_across_records(jobs, cut=Cut(every=2), options=RenderOptions(model="dmx-600"))
        >>> data
        b'AqB\\x02oqC\\x02o'
    """
    cuts, findings = batch_cuts(len(jobs), cut=cut, options=options)
    return b"".join(
        with_cut(job, closing, cut=after) for (job, closing), after in zip(jobs, cuts, strict=True)
    ), findings


__all__ = ["CUT_NOW", "batch_cuts", "cut_across_records", "with_cut"]
