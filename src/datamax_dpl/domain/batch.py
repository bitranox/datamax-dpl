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
from .render_context import RenderContext, RenderOptions, resolved_image_module
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
        default_attribute=Attribute.XOR,
    )
    return ctx.supports(feature), ctx.requirement(feature)


def cut_across_records(jobs: Sequence[bytes], *, cut: Cut, options: RenderOptions) -> tuple[bytes, tuple[Finding, ...]]:
    """Join per-record jobs, inserting a cut after every ``cut.every``-th one.

    Returns:
        The joined bytes and the batch-level findings.

    Example:
        >>> from datamax_dpl.domain.label import Cut
        >>> data, _ = cut_across_records([b"A", b"B", b"C"], cut=Cut(every=2), options=RenderOptions(model="dmx-600"))
        >>> data
        b'AB\\x02oC\\x02o'
    """
    findings = FindingCollector(options.policy)
    supported, requirement = _supports(options, Feature.CUT_NOW, findings)
    if not supported:
        findings.deviate(
            FindingCode.BATCH_CUT_UNSUPPORTED,
            f"cutting every {cut.every} labels of a batch needs the cut-now command: {requirement}",
            fallback="the printer cuts as its own cutter setting says",
        )
        return b"".join(jobs), findings.findings()
    findings.warn(
        FindingCode.BATCH_CUT_UNVERIFIED,
        f"a cut command follows every {cut.every}th label; the printer must not also cut each label "
        "by itself (unverified on hardware)",
    )
    pieces: list[bytes] = []
    for number, job in enumerate(jobs, start=1):
        pieces.append(job)
        last = number == len(jobs)
        if number % cut.every == 0 or (last and cut.at_end):
            pieces.append(CUT_NOW)
    return b"".join(pieces), findings.findings()


__all__ = ["CUT_NOW", "cut_across_records"]
