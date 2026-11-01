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


def cut_across_records(
    jobs: Sequence[tuple[bytes, bytes]], *, cut: Cut, options: RenderOptions
) -> tuple[bytes, tuple[Finding, ...]]:
    """Join per-record jobs, inserting a cut after every ``cut.every``-th one.

    The cut goes before the record's closing clears, so they stay the last of its bytes as in a
    single label's job: the next record, or the next program, starts from an empty module.

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
    findings = FindingCollector(options.policy)
    supported, requirement = _supports(options, Feature.CUT_NOW, findings)
    if not supported:
        findings.deviate(
            FindingCode.BATCH_CUT_UNSUPPORTED,
            f"cutting every {cut.every} labels of a batch needs the cut-now command: {requirement}",
            fallback="the printer cuts as its own cutter setting says",
        )
        return b"".join(job for job, _ in jobs), findings.findings()
    findings.warn(
        FindingCode.BATCH_CUT_UNVERIFIED,
        f"a cut command follows every {cut.every}th label; the printer must not also cut each label "
        "by itself (unverified on hardware)",
    )
    pieces: list[bytes] = []
    for number, (job, closing) in enumerate(jobs, start=1):
        last = number == len(jobs)
        if not (number % cut.every == 0 or (last and cut.at_end)):
            pieces.append(job)
            continue
        pieces += [job[: len(job) - len(closing)], CUT_NOW, closing]
    return b"".join(pieces), findings.findings()


__all__ = ["CUT_NOW", "cut_across_records"]
