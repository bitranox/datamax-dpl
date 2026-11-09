"""A symbology given as its plain name is checked as its member is.

An untyped caller passes ``"pdf417"`` where the signature says :class:`Symbology`. The enum is a str
mixin, so that name equals the member, and the record budgets of the two-dimensional codes have to
treat the two alike.
"""

from __future__ import annotations

from typing import cast

import pytest

from datamax_dpl.domain.barcodes import check_barcode
from datamax_dpl.domain.enums import BarcodeProblemCode
from datamax_dpl.domain.symbology import Symbology

_REFUSED_PDF417 = "record data is 3001 characters, the printer takes at most 3000"
_REFUSED_DATAMATRIX = "record data is 256 characters, the printer takes at most 255"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("member", "length", "refusals"),
    [
        pytest.param(Symbology.PDF417, 300, [], id="pdf417-past-the-plain-field-limit"),
        pytest.param(Symbology.PDF417, 2992, [], id="pdf417-fills-its-limit-with-the-header"),
        pytest.param(Symbology.PDF417, 2993, [_REFUSED_PDF417], id="pdf417-one-past-its-limit"),
        pytest.param(Symbology.DATAMATRIX, 246, [_REFUSED_DATAMATRIX], id="datamatrix-header-counts"),
    ],
)
def test_a_raw_symbology_name_gets_the_record_budget_of_its_member(
    member: Symbology, length: int, refusals: list[str]
) -> None:
    data = "A" * length

    check = check_barcode(cast("Symbology", member.value), data)

    assert [problem.message for problem in check.problems if problem.code is BarcodeProblemCode.TOO_LONG] == refusals
    assert check == check_barcode(member, data)
