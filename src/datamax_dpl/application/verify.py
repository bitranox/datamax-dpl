"""Print a label and photograph the result, for a person or an LLM to compare with the preview.

The loop: send the job, wait until the printer reports it is idle (when the channel can read
status) or a fixed settle time (when it cannot), wait ``settle_seconds`` more so the label has
stopped moving, then take one photo. Judging the photo is left to the caller; the preview bitmap
of the same label is returned beside it as the expected picture.
"""

from __future__ import annotations

from contextlib import closing
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ..domain.camera import CameraKind, CameraSettings, CapturedImage
from ..domain.errors import CameraError, LabelRejectedError, PrinterTimeoutError
from ..domain.png import encode_png
from ..domain.preview import preview_bitmap
from ..domain.status import STATUS_REQUEST, PrinterStatus, parse_ascii_status

if TYPE_CHECKING:
    from pathlib import Path

    from ..domain.label import Label
    from ..domain.profiles import PrinterProfile
    from ..domain.render import RenderResult
    from .ports import CaptureImage, Clock, OpenPrinter, PrinterTransport, Sleep

#: Seconds between two status polls while the label prints.
POLL_SECONDS = 0.5
#: Wait used instead of status polling on a one-way channel (file, device file).
ONE_WAY_SETTLE_SECONDS = 5.0


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


def wait_idle(printer: PrinterTransport, *, timeout: float, sleep: Sleep, clock: Clock) -> PrinterStatus:
    """Poll the status until the printer is idle or reports a problem.

    Raises:
        PrinterTimeoutError: Still busy at the deadline.
    """
    deadline = clock() + timeout
    while True:
        status = parse_ascii_status(printer.query(STATUS_REQUEST, timeout=timeout))
        if status.idle or status.problems:
            return status
        if clock() >= deadline:
            msg = f"printer still busy after {timeout:g} s"
            raise PrinterTimeoutError(msg)
        sleep(POLL_SECONDS)


def _print_and_wait(
    data: bytes, *, uri: str, timeout: float, open_printer: OpenPrinter, sleep: Sleep, clock: Clock
) -> PrinterStatus | None:
    with closing(open_printer(uri, timeout=timeout)) as printer:
        printer.send(data)
        if not printer.bidirectional:
            sleep(ONE_WAY_SETTLE_SECONDS)
            return None
        return wait_idle(printer, timeout=timeout, sleep=sleep, clock=clock)


def verify_print(
    result: RenderResult,
    label: Label,
    profile: PrinterProfile,
    *,
    uri: str,
    timeout: float,
    camera: CameraSettings,
    output_dir: Path,
    open_printer: OpenPrinter,
    capture: CaptureImage,
    sleep: Sleep,
    clock: Clock,
) -> VerifyReport:
    """Print ``result``, wait for the printer, photograph the label and return both pictures.

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
    status = _print_and_wait(result.data, uri=uri, timeout=timeout, open_printer=open_printer, sleep=sleep, clock=clock)
    sleep(camera.settle_seconds)
    photo = capture(camera, output_dir=output_dir)
    expected = encode_png(preview_bitmap(label, profile, halved=frozenset(result.halved_elements)))
    return VerifyReport(bytes_sent=len(result.data), status=status, photo=photo, expected_png=expected)


__all__ = ["ONE_WAY_SETTLE_SECONDS", "POLL_SECONDS", "VerifyReport", "verify_print", "wait_idle"]
