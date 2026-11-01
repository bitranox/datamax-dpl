"""Print a label and photograph the result, for a person or an LLM to compare with the preview.

The loop: send the job, wait until the printer reports it is idle (when the channel can read
status) or a fixed settle time (when it cannot), see :class:`IdleWait`, wait ``settle_seconds``
more so the label has stopped moving, then take one photo. Judging the photo is left to the
caller; the preview bitmap of the same label is returned beside it as the expected picture.
"""

from __future__ import annotations

from contextlib import closing
from typing import TYPE_CHECKING, NamedTuple

from pydantic import BaseModel, ConfigDict

from ..domain.camera import CameraKind, CameraSettings, CapturedImage
from ..domain.errors import CameraError, LabelRejectedError, PrinterTimeoutError
from ..domain.png import encode_png
from ..domain.preview import preview_bitmap
from ..domain.status import PrinterStatus
from ..domain.timing import check_timeout
from .use_cases import AnswerWait, poll_status

if TYPE_CHECKING:
    from pathlib import Path

    from ..domain.label import Label
    from ..domain.profiles import PrinterProfile
    from ..domain.render import RenderResult
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
        bytes_sent: Size of the job sent.
        status: Printer status after printing, or None when the channel cannot read it.
        photo: The photo of the printed label.
        expected_png: Preview of the label at printer resolution, as PNG bytes.
    """

    # PNG bytes are never UTF-8, so JSON carries them as base64.
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64")

    bytes_sent: int
    status: PrinterStatus | None
    photo: CapturedImage
    expected_png: bytes


def wait_idle(
    printer: PrinterTransport, *, uri: str, timeout: float, open_printer: OpenPrinter, wait: IdleWait
) -> PrinterStatus:
    """Poll the status until the printer is idle or reports a problem, within ``wait.answer``.

    ``printer`` is the open channel the job went out on, and stays the caller's to close; a
    connection opened here because ``printer`` took no request is closed here.

    Raises:
        PrinterTimeoutError: No answer within the wait (as ``status`` reports it), or still busy
            when it ran out; both messages give the seconds waited and end with
            ``wait.answer.wait_longer``.
        PrinterProtocolError: An answer was not a status string.
        TransportError: The printer could not be reached for another reason.
        ValueError: A wait value is not a usable number of seconds.
    """
    answer_wait = wait.answer
    check_timeout(answer_wait.seconds, name="wait")
    check_timeout(wait.poll_interval, name="poll interval")
    started = answer_wait.clock()
    channel = printer
    try:
        while answer_wait.clock() - started < answer_wait.seconds:
            channel, status = poll_status(
                channel, uri=uri, timeout=timeout, open_printer=open_printer, wait=answer_wait, started=started
            )
            if status.idle or status.problems:
                return status
            answer_wait.sleep(wait.poll_interval)
    finally:
        if channel is not printer:
            channel.close()
    advice = answer_wait.wait_longer
    raise PrinterTimeoutError(f"printer still busy after {answer_wait.clock() - started:.1f} s", advice=advice)


def _print_and_wait(
    data: bytes, *, uri: str, timeout: float, wait: IdleWait, open_printer: OpenPrinter, sleep: Sleep
) -> PrinterStatus | None:
    with closing(open_printer(uri, timeout=timeout)) as printer:
        printer.send(data)
        if not printer.bidirectional:
            sleep(wait.one_way_settle)
            return None
        return wait_idle(printer, uri=uri, timeout=timeout, open_printer=open_printer, wait=wait)


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
) -> VerifyReport:
    """Print ``result``, wait for the printer, photograph the label and return both pictures.

    ``options`` are the ones ``result`` was rendered with: the expected picture places each field
    at the row and column that job writes. ``wait`` is how long the printer may take to finish
    (see :class:`IdleWait`); None waits ``timeout`` seconds in all on ``clock`` and ``sleep``.

    Raises:
        LabelRejectedError: The job has error findings; nothing was printed.
        TransportError: The printer could not be reached, or did not finish in time.
        CameraError: No photo could be taken.
    """
    if not result.ok:
        msg = "label has errors; nothing was printed"
        raise LabelRejectedError(msg)
    if camera.kind is CameraKind.NONE:
        msg = "no camera configured: set [camera] kind; nothing was printed"
        raise CameraError(msg)
    idle_wait = IdleWait(answer=AnswerWait(seconds=timeout, clock=clock, sleep=sleep)) if wait is None else wait
    status = _print_and_wait(
        result.data, uri=uri, timeout=timeout, wait=idle_wait, open_printer=open_printer, sleep=sleep
    )
    sleep(camera.settle_seconds)
    photo = capture(camera, output_dir=output_dir)
    expected = encode_png(preview_bitmap(label, profile, sent_as=result.sent_as, options=options))
    return VerifyReport(bytes_sent=len(result.data), status=status, photo=photo, expected_png=expected)


__all__ = ["ONE_WAY_SETTLE_SECONDS", "POLL_SECONDS", "IdleWait", "VerifyReport", "verify_print", "wait_idle"]
