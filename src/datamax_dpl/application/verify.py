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
from ..domain.status import STATUS_REQUEST, PrinterStatus, is_status_line, parse_ascii_status
from ..domain.timing import check_timeout

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

    A printer can report busy for longer than one transport ``timeout`` after an image job, so
    the whole wait is its own value, as ``status`` has ``status_wait``; each poll's answer wait is
    the transport timeout, never past the end of the whole wait. A poll that gets no answer in
    its wait ends the wait with that timeout: the printer is not asked again on the connection
    an answer may still be owed on.

    Attributes:
        seconds: The whole wait for the printer to report idle (or a problem), from the first poll.
        poll_interval: Seconds between two polls on a channel that reads status.
        one_way_settle: Seconds waited instead, on a channel that cannot read status.
        wait_longer: How the caller lengthens the wait, added to the message when it runs out.
    """

    seconds: float
    poll_interval: float = POLL_SECONDS
    one_way_settle: float = ONE_WAY_SETTLE_SECONDS
    wait_longer: str = ""


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
    printer: PrinterTransport, *, timeout: float, wait: IdleWait, sleep: Sleep, clock: Clock
) -> PrinterStatus:
    """Poll the status every ``wait.poll_interval`` until the printer is idle or reports a problem.

    Each poll waits ``timeout`` for its answer, but never past the end of ``wait.seconds``.

    Raises:
        PrinterTimeoutError: Still busy when ``wait.seconds`` ran out; the message gives the
            seconds waited and ends with ``wait.wait_longer``. A poll that got no answer in time
            raises its own timeout.
        ValueError: ``timeout`` or a wait value is not a usable number of seconds.
    """
    check_timeout(timeout)
    check_timeout(wait.seconds, name="wait")
    check_timeout(wait.poll_interval, name="poll interval")
    started = clock()
    deadline = started + wait.seconds
    while (left := deadline - clock()) > 0:
        answer = printer.query(STATUS_REQUEST, timeout=min(timeout, left), accept=is_status_line)
        status = parse_ascii_status(answer)
        if status.idle or status.problems:
            return status
        sleep(wait.poll_interval)
    msg = f"printer still busy after {clock() - started:.1f} s"
    raise PrinterTimeoutError(msg, advice=wait.wait_longer)


def _print_and_wait(
    data: bytes, *, uri: str, timeout: float, wait: IdleWait, open_printer: OpenPrinter, sleep: Sleep, clock: Clock
) -> PrinterStatus | None:
    with closing(open_printer(uri, timeout=timeout)) as printer:
        printer.send(data)
        if not printer.bidirectional:
            sleep(wait.one_way_settle)
            return None
        return wait_idle(printer, timeout=timeout, wait=wait, sleep=sleep, clock=clock)


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
    (see :class:`IdleWait`); None waits ``timeout`` seconds in all.

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
    idle_wait = IdleWait(seconds=timeout) if wait is None else wait
    status = _print_and_wait(
        result.data, uri=uri, timeout=timeout, wait=idle_wait, open_printer=open_printer, sleep=sleep, clock=clock
    )
    sleep(camera.settle_seconds)
    photo = capture(camera, output_dir=output_dir)
    expected = encode_png(preview_bitmap(label, profile, sent_as=result.sent_as, options=options))
    return VerifyReport(bytes_sent=len(result.data), status=status, photo=photo, expected_png=expected)


__all__ = ["ONE_WAY_SETTLE_SECONDS", "POLL_SECONDS", "IdleWait", "VerifyReport", "verify_print", "wait_idle"]
