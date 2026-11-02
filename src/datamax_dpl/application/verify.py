"""Print a label and photograph the result, for a person or an LLM to compare with the preview.

The loop: send the job, wait until the printer reports it is idle (when the channel can read
status) or a fixed settle time (when it cannot), see :class:`IdleWait`, wait ``settle_seconds``
more so the label has stopped moving, then take one photo. Judging the photo is left to the
caller; the preview bitmap of the same label is returned beside it as the expected picture.
"""

from __future__ import annotations

import math
from contextlib import closing
from typing import TYPE_CHECKING, NamedTuple

from pydantic import BaseModel, ConfigDict

from ..domain.camera import CameraKind, CameraSettings, CapturedImage
from ..domain.errors import CameraError, LabelRejectedError, PrinterTimeoutError
from ..domain.png import encode_png
from ..domain.preview import preview_bitmap
from ..domain.render import RenderResult
from ..domain.status import PrinterStatus
from ..domain.timing import MAX_TIMEOUT_SECONDS, TimeoutName, check_timeout
from .upload_check import CheckedJob, Link, UploadRefusedError, send_checked_label
from .use_cases import AnswerWait, poll_status

if TYPE_CHECKING:
    from pathlib import Path

    from ..domain.label import Label
    from ..domain.profiles import PrinterProfile
    from ..domain.render_context import RenderOptions
    from .ports import CaptureImage, Clock, OpenPrinter, PrinterTransport, Sleep

#: Default of ``[printer] verify_poll_interval``: seconds between two status polls while the label prints.
POLL_SECONDS = 0.5
#: Default of ``[printer] one_way_settle``: the wait used instead of status polling on a one-way
#: channel (file, device file), which cannot tell when the printer is done.
ONE_WAY_SETTLE_SECONDS = 5.0


class IdleWait(NamedTuple):
    """How ``verify`` waits for the printer to finish the job it was sent.

    The polls share one :class:`~datamax_dpl.application.use_cases.AnswerWait`, the wait
    ``status`` uses, and each poll is asked as ``status`` asks (``poll_status``): a silent
    printer is read until the whole wait ends and never asked a second time, a connection that
    took no request is asked again on a new one, and a printer that answers busy is polled again
    after ``poll_interval`` until the wait runs out.

    Attributes:
        answer: The whole wait, from the first poll, and how a poll that took no request is
            asked again; its ``wait_longer`` ends the message when it runs out.
        poll_interval: Seconds between a busy answer and the next poll.
        one_way_settle: Seconds waited instead, on a channel that cannot read status.
    """

    answer: AnswerWait
    poll_interval: float = POLL_SECONDS
    one_way_settle: float = ONE_WAY_SETTLE_SECONDS


class VerifyReport(BaseModel):
    """What ``verify`` produced.

    Attributes:
        bytes_sent: Size of the job sent, every round of its upload check included.
        status: Printer status after printing, or None when the channel cannot read it.
        photo: The photo of the printed label.
        expected_png: Preview of the label at printer resolution, as PNG bytes.
        result: The job as it went out: with its upload check's findings, halved where a round
            halved it. None only for a report made without one.
    """

    # PNG bytes are never UTF-8, so JSON carries them as base64.
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64")

    bytes_sent: int
    status: PrinterStatus | None
    photo: CapturedImage
    expected_png: bytes
    result: RenderResult | None = None


def check_idle_wait(wait: IdleWait) -> IdleWait:
    """Return ``wait`` when all its seconds are usable, so ``verify`` refuses a bad one before printing.

    Every value but ``one_way_settle`` must be above 0; that one may be 0 (no wait at all). None
    may be NaN, infinite or above :data:`~datamax_dpl.domain.timing.MAX_TIMEOUT_SECONDS`.

    Raises:
        ValueError: A value is not a usable number of seconds; the message names it.

    Example:
        >>> from datamax_dpl.application.use_cases import AnswerWait
        >>> check_idle_wait(IdleWait(answer=AnswerWait(seconds=5.0), one_way_settle=0.0)).one_way_settle
        0.0
        >>> check_idle_wait(IdleWait(answer=AnswerWait(seconds=5.0), one_way_settle=-1.0))
        Traceback (most recent call last):
        ...
        ValueError: one-way settle must be at least 0 and at most 2147483 seconds, got -1.0
    """
    check_timeout(wait.answer.seconds, name=TimeoutName.WAIT)
    check_timeout(wait.answer.retry_interval, name=TimeoutName.RETRY_INTERVAL)
    check_timeout(wait.poll_interval, name=TimeoutName.POLL_INTERVAL)
    settle = wait.one_way_settle
    if not (math.isfinite(settle) and 0 <= settle <= MAX_TIMEOUT_SECONDS):
        msg = f"one-way settle must be at least 0 and at most {MAX_TIMEOUT_SECONDS:.0f} seconds, got {settle!r}"
        raise ValueError(msg)
    return wait


def wait_idle(
    printer: PrinterTransport, *, uri: str, timeout: float, open_printer: OpenPrinter, wait: IdleWait
) -> PrinterStatus:
    """Poll the status until the printer is idle or reports a problem, within ``wait.answer``.

    ``printer`` is the open channel the job went out on, and stays the caller's to close; a
    connection opened here because ``printer`` took no request is closed here.

    Raises:
        PrinterTimeoutError: No answer within the wait (as ``status`` reports it), or still busy
            when it ran out, also when the printer was last heard busy and the final poll got no
            answer (that poll's failure is chained); both messages give the seconds waited and end
            with ``wait.answer.wait_longer``.
        PrinterProtocolError: An answer was not a status string.
        TransportError: The printer could not be reached for another reason.
        ValueError: A wait value is not a usable number of seconds.
    """
    check_idle_wait(wait)
    answer_wait = wait.answer
    started = answer_wait.clock()
    deadline = started + answer_wait.seconds
    channel = printer
    heard_busy = False
    try:
        while answer_wait.clock() < deadline:
            try:
                poll = poll_status(
                    channel, uri=uri, timeout=timeout, open_printer=open_printer, wait=answer_wait, started=started
                )
            except PrinterTimeoutError as exc:
                # The poll is read exactly as status reads one; only what the message says differs,
                # since a printer last heard busy is most likely still busy.
                if not heard_busy:
                    raise
                raise _still_busy(answer_wait, started=started, unanswered=True) from exc
            channel, status = poll.channel, poll.status
            if status.idle or status.problems:
                return status
            heard_busy = True
            # Never past the end: a 20 s interval must not stretch a 30 s wait to 40 s.
            pause = min(wait.poll_interval, deadline - answer_wait.clock())
            if pause > 0:
                answer_wait.sleep(pause)
    finally:
        if channel is not printer:
            channel.close()
    raise _still_busy(answer_wait, started=started, unanswered=False)


def _still_busy(wait: AnswerWait, *, started: float, unanswered: bool) -> PrinterTimeoutError:
    """The error for a printer still busy when the wait ran out, naming how to wait longer."""
    last = " (the last poll got no answer)" if unanswered else ""
    return PrinterTimeoutError(
        f"printer still busy after {wait.clock() - started:.1f} s{last}", advice=wait.wait_longer
    )


class _Printed(NamedTuple):
    """What ``verify`` sent and how the printer ended up."""

    result: RenderResult
    bytes_sent: int
    status: PrinterStatus | None


def _print_and_wait(
    result: RenderResult,
    *,
    uri: str,
    timeout: float,
    wait: IdleWait,
    open_printer: OpenPrinter,
    sleep: Sleep,
    checked: CheckedJob | None,
) -> _Printed:
    with closing(open_printer(uri, timeout=timeout)) as printer:
        if not printer.bidirectional:
            printer.send(result.data)
            sleep(wait.one_way_settle)
            return _Printed(result=result, bytes_sent=len(result.data), status=None)
        if checked is None:
            printer.send(result.data)
            status = wait_idle(printer, uri=uri, timeout=timeout, open_printer=open_printer, wait=wait)
            return _Printed(result=result, bytes_sent=len(result.data), status=status)
        link = Link(printer=printer, uri=uri, timeout=timeout, open_printer=open_printer, reconnect=checked.check.wait)
        try:
            return _send_checked_and_wait(link, checked, wait=wait)
        finally:
            link.close_opened()


def _send_checked_and_wait(link: Link, checked: CheckedJob, *, wait: IdleWait) -> _Printed:
    """Send the label with its upload check on ``link``, then wait on the channel the job ended on.

    Raises:
        UploadRefusedError: The printer did not list the label's images after every round, or the
            label, rendered again for what the printer then held, has an error finding; its format
            was not sent.
    """
    send = send_checked_label(link, checked)
    if not send.all_sent:
        raise UploadRefusedError(send.result, bytes_sent=link.bytes_sent)
    # The rest of the job went out on the channel that answered the last read, so one is open.
    channel = link.printer or link.open_printer(link.uri, timeout=link.timeout)
    status = wait_idle(channel, uri=link.uri, timeout=link.timeout, open_printer=link.open_printer, wait=wait)
    return _Printed(result=send.result, bytes_sent=link.bytes_sent, status=status)


def verify_print(
    result: RenderResult,
    label: Label,
    profile: PrinterProfile,
    *,
    options: RenderOptions,
    uri: str,
    timeout: float,
    camera: CameraSettings,
    output_dir: Path,
    open_printer: OpenPrinter,
    capture: CaptureImage,
    sleep: Sleep,
    clock: Clock,
    wait: IdleWait | None = None,
    checked: CheckedJob | None = None,
) -> VerifyReport:
    """Print ``result``, wait for the printer, photograph the label and return both pictures.

    ``options`` are the ones ``result`` was rendered with: the expected picture places each field
    at the row and column that job writes, its images as the job sent them. ``wait`` is how long
    the printer may take to finish (see :class:`IdleWait`); None waits ``timeout`` seconds in all
    on ``clock`` and ``sleep``. ``checked`` sends a label that downloads an image after its upload
    check on a two-way channel (see :mod:`~datamax_dpl.application.upload_check`); None sends the
    job at once.

    Raises:
        LabelRejectedError: The job has error findings; nothing was printed.
        UploadRefusedError: The printer did not list the label's images after every round, or the
            label, rendered again for what the printer then held, has an error finding; its format
            was not sent, and no photo was taken.
        TransportError: The printer could not be reached, or did not finish in time.
        CameraError: No photo could be taken.
        ValueError: A value of ``wait`` is not a usable number of seconds (see
            :func:`check_idle_wait`); nothing was printed.
    """
    if not result.ok:
        msg = "label has errors; nothing was printed"
        raise LabelRejectedError(msg)
    if camera.kind is CameraKind.NONE:
        msg = "no camera configured: set [camera] kind; nothing was printed"
        raise CameraError(msg)
    idle_wait = IdleWait(answer=AnswerWait(seconds=timeout, clock=clock, sleep=sleep)) if wait is None else wait
    # Before the job goes out: refused afterwards, it would leave a label printed and not photographed.
    check_idle_wait(idle_wait)
    printed = _print_and_wait(
        result, uri=uri, timeout=timeout, wait=idle_wait, open_printer=open_printer, sleep=sleep, checked=checked
    )
    sleep(camera.settle_seconds)
    photo = capture(camera, output_dir=output_dir)
    expected = encode_png(preview_bitmap(label, profile, sent_as=printed.result.sent_as, options=options))
    return VerifyReport(
        bytes_sent=printed.bytes_sent, status=printed.status, photo=photo, expected_png=expected, result=printed.result
    )


__all__ = [
    "ONE_WAY_SETTLE_SECONDS",
    "POLL_SECONDS",
    "IdleWait",
    "VerifyReport",
    "check_idle_wait",
    "verify_print",
    "wait_idle",
]
