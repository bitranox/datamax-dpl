"""The calibration label that goes before the first label after a printer reset.

With no status read first, the first label after a power cycle printed about 12 mm shifted on one
Allegro 2 (CA02 03.04, 2026-11-02); with a text label sent first, that label took the shift on one
paper label and the next one sat right. So on a reset report one such label goes first: it carries
the job's own printer settings and size, so the printer starts from what the job relies on, and
one text in the middle, so that a 15 mm shift still leaves the text on the paper. It clears nothing:
what the module holds after a reset is what the reads say, and the job's own clears stay the job's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from ._invariants import require_update_fields
from .charset import encode_for_bitmapped_font
from .enums import Unit
from .label import MAX_FIELD_DATA, Cut, DotSize, Label, Text
from .preview import canvas_for, field_area
from .profiles import get_profile
from .render import RenderResult, build_job
from .render_context import RenderOptions
from .units import MM_PER_INCH

if TYPE_CHECKING:
    from .enums import Attribute
    from .label import Element

#: Default of ``[printer] reset_check_wait``: seconds the one status read before a job waits. Three,
#: not the 30 of status_wait: it runs before every job on a two-way link, and an idle printer answers
#: at once, so a printer silent for this long is one the job should not wait on.
RESET_CHECK_WAIT_SECONDS = 3.0
#: Default of ``[printer] calibrate_after_reset`` (user decision, 2026-11-02).
CALIBRATE_AFTER_RESET = True
#: Default of ``[printer] calibration_label_text`` (user decision, 2026-11-02).
CALIBRATION_LABEL_TEXT = "Printer Reset - Calibration Feed"
#: The font of the calibration text, the default of a text field, which every model has.
_FONT = Text(x=0, y=0, text="x").font


class _CalibrationFields(TypedDict):
    """What the calibration label replaces of the job's label: all but its printer settings and size."""

    elements: list[Element]
    quantity: int
    cut: Cut | None
    dot_size: DotSize | None
    column_offset: float | None
    row_offset: float | None
    attribute: Attribute | None
    mirror: bool
    pause_each_label: bool
    count_by: int | None
    plain_zero: bool


class _NoClears(TypedDict):
    """The options a calibration label renders with: no clear before or after it."""

    clear_graphics_before_label: bool
    clear_graphics_after_label: bool


require_update_fields(_CalibrationFields, Label)
require_update_fields(_NoClears, RenderOptions)


def check_label_text(text: str) -> str:
    """Return ``text`` when a text field can hold it.

    Raises:
        ValueError: It is empty, longer than a field's data, or not text at all (a lone surrogate,
            which is how a value that is not UTF-8 reaches ``argv`` or the environment).

    Example:
        >>> check_label_text("Feed")
        'Feed'
        >>> check_label_text("")
        Traceback (most recent call last):
        ...
        ValueError: needs 1 to 255 characters, got 0
    """
    if not 1 <= len(text) <= MAX_FIELD_DATA:
        raise ValueError(f"needs 1 to {MAX_FIELD_DATA} characters, got {len(text)}")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"holds a character that is not text, at position {exc.start}") from None
    return text


def characters_replaced(text: str) -> tuple[str, ...]:
    """Return each character of ``text`` the calibration label's font cannot print, which prints as ``?``.

    Example:
        >>> characters_replaced("Feed"), [f"U+{ord(c):04X}" for c in characters_replaced("Feed " + chr(0x2192))]
        ((), ['U+2192'])
    """
    return encode_for_bitmapped_font(text, font=_FONT).unmappable


def calibration_job(label: Label, options: RenderOptions, *, text: str) -> RenderResult:
    """Render the calibration label for ``label``: the same printer settings and size, ``text`` in the middle.

    Args:
        label: The job's label; its elements, copies and format settings stay out, since this
            label only has to come out once, the right size.
        options: The options the job renders with, except that this label clears nothing.
        text: What it says, mapped as any text field's text is.

    Returns:
        The label's job; check ``ok`` before sending it.

    Raises:
        UnknownModelError: ``options.model`` names no profile.

    Example:
        >>> label = Label(width=100, height=60, elements=[Text(x=5, y=5, text="Hi")])
        >>> job = calibration_job(label, RenderOptions(model="allegro-2"), text="Feed")
        >>> job.ok, b"Feed" in job.data, b"Hi" in job.data, job.closing
        (True, True, False, b'')
    """
    placed = _placed_text(label, options, text=text)
    # model_copy, not a new Label: the job's label is validated already, and only these fields change.
    calibration = label.model_copy(
        update=_CalibrationFields(
            elements=[placed],
            quantity=1,
            # Cut at this label's own end where the job cuts at all, so its stop position stays the job's.
            cut=None if label.cut is None else Cut(every=1, at_end=False),
            dot_size=None,
            column_offset=None,
            row_offset=None,
            attribute=None,
            mirror=False,
            # A pause here would hold the printer before the job it is there to prepare.
            pause_each_label=False,
            count_by=None,
            plain_zero=False,
        )
    )
    no_clears = options.model_copy(
        update=_NoClears(clear_graphics_before_label=False, clear_graphics_after_label=False)
    )
    # Marked filled: braces in the text are the text, not placeholders nothing will fill.
    return build_job(calibration.marked_filled(), no_clears)


def _placed_text(label: Label, options: RenderOptions, *, text: str) -> Text:
    """The text field in the middle of the label, in the label's unit."""
    profile = get_profile(options.model)
    canvas = canvas_for(label, profile, options=options)
    length = field_area(Text(x=0, y=0, text=text, font=_FONT), canvas).length
    return Text(
        x=_length_of(max(0, canvas.width - length) // 2, unit=label.unit, dpi=profile.dpi),
        y=_length_of(canvas.height // 2, unit=label.unit, dpi=profile.dpi),
        text=text,
        font=_FONT,
    )


def _length_of(dots: int, *, unit: Unit, dpi: float) -> float:
    """Return ``dots`` as a length in ``unit``.

    Example:
        >>> _length_of(203, unit=Unit.MM, dpi=203), _length_of(203, unit=Unit.DOTS, dpi=203)
        (25.4, 203.0)
    """
    if unit is Unit.DOTS:
        return float(dots)
    inches = dots / dpi
    return inches * MM_PER_INCH if unit is Unit.MM else inches


__all__ = [
    "CALIBRATE_AFTER_RESET",
    "CALIBRATION_LABEL_TEXT",
    "RESET_CHECK_WAIT_SECONDS",
    "calibration_job",
    "characters_replaced",
    "check_label_text",
]
