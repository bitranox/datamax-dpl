"""Device file transport, such as a USB printer class device (``/dev/usb/lp0``).

The device is opened write-only and never created: a mistyped device path must fail rather than
leave a regular file holding the job where the device should be. Reading answers back is left out
because a printer class device does not reliably deliver them.

On POSIX the descriptor is non-blocking and every piece of a job waits until the far end can take
it, so a device or FIFO that stops taking data times out, and opening a FIFO nobody reads fails at
once instead of waiting for a reader. Windows has neither for a file descriptor: there an open or a
write takes as long as the device takes, and ``timeout`` does not apply. Neither can bound a stuck
disk or network mount, where the kernel itself blocks the call.
"""

from __future__ import annotations

import math
import os
import select
import sys
import time
from typing import TYPE_CHECKING

from datamax_dpl.domain.errors import TransportError
from datamax_dpl.domain.timing import DEFAULT_TRANSPORT_TUNING, MAX_ANSWER_SECONDS, TransportTuning

from .uri import (
    ChannelAction,
    ClosingTransport,
    DeviceUri,
    FileUri,
    channel_name,
    transport_error,
    truncated_job_advice,
    unsupported_query,
)

if TYPE_CHECKING:
    from collections.abc import Callable

# Windows opens files in text mode unless told otherwise, which would rewrite every LF byte of a
# job; POSIX has no such flag.
_BINARY = os.O_BINARY if sys.platform == "win32" else 0
_NON_BLOCKING = 0 if sys.platform == "win32" else os.O_NONBLOCK
#: Flags a write-only descriptor transport opens its path with; a file adds ``os.O_CREAT``.
WRITE_FLAGS = os.O_WRONLY | os.O_APPEND | _BINARY | _NON_BLOCKING
#: Pause when a device calls itself writable and then refuses the write, as a driver with no poll
#: of its own does. A hard limit, so no setting: it only keeps that case from spinning a CPU.
_BUSY_PAUSE_SECONDS = 0.01
_MILLISECONDS = 1000


def _writable_within(fd: int, seconds: float) -> bool:
    """Whether ``fd`` can take data within ``seconds``; an error on it also reads as yes, for the write to raise.

    ``poll`` has no limit on the descriptor's number, which a long-running caller of the library
    can pass. macOS's ``poll`` is not trusted with devices, so it keeps ``select`` and its limit,
    and Windows has neither for a file descriptor and never waits here.

    Raises:
        OSError: The descriptor is past what ``select`` can wait on.
    """
    if sys.platform == "win32":
        return True
    if sys.platform == "darwin":
        try:
            return bool(select.select([], [fd], [], seconds)[1])
        except ValueError as exc:
            raise OSError(f"descriptor {fd} is too high to wait on here") from exc
    poller = select.poll()
    poller.register(fd, select.POLLOUT)
    return bool(poller.poll(math.ceil(seconds * _MILLISECONDS)))


class DescriptorTransport(ClosingTransport):
    """A path open for writing through a raw file descriptor; reading answers is refused."""

    def __init__(
        self, fd: int, *, uri: DeviceUri | FileUri, timeout: float, tuning: TransportTuning = DEFAULT_TRANSPORT_TUNING
    ) -> None:
        """Wrap the open file descriptor ``fd``; the transport closes it.

        Args:
            fd: The descriptor.
            uri: What it was opened from, for messages.
            timeout: Seconds a piece of a job may wait for the far end to take any of it (POSIX).
            tuning: The size of those pieces.
        """
        self._fd: int | None = fd
        self._uri = uri
        self._timeout = timeout
        self._chunk = tuning.send_chunk_bytes

    @property
    def bidirectional(self) -> bool:
        """A descriptor transport is written only."""
        return False

    def peer_closed(self) -> bool:
        """A file or device file has no far end that closes: always False."""
        return False

    def send(self, data: bytes) -> None:
        """Write ``data`` completely, in pieces, looping over short writes.

        Raises:
            PrinterTimeoutError: The far end took nothing for ``timeout`` seconds (POSIX only).
                Says how much went out, as any failed write of a job does.
            TransportError: The write failed or the transport is closed.
        """
        if self._fd is None:
            raise TransportError(f"{channel_name(self._uri)}: the transport is closed")
        view = memoryview(data)
        written = 0
        try:
            while written < len(data):
                written += self._write_piece(self._fd, view[written : written + self._chunk])
        except OSError as exc:
            advice = truncated_job_advice(written=written, size=len(data))
            raise transport_error(self._uri, action=ChannelAction.WRITE, error=exc, advice=advice) from exc

    def _write_piece(self, fd: int, piece: memoryview) -> int:
        """Write what the far end takes of ``piece``, waiting at most ``timeout`` for it to take any.

        A write that took nothing stays inside the one deadline, whether it refused or returned 0:
        only a write that took data starts a new one, so a device that keeps taking nothing times out.

        Raises:
            TimeoutError: It took nothing in that time.
            OSError: The write failed.
        """
        deadline = time.monotonic() + self._timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0 or not _writable_within(fd, left):
                raise TimeoutError(f"took no data for {self._timeout:g} s")
            try:
                taken = os.write(fd, piece)
            except BlockingIOError:
                taken = 0
            if taken:
                return taken
            time.sleep(_BUSY_PAUSE_SECONDS)

    def query(
        self,
        request: bytes,
        *,
        timeout: float,
        terminator: bytes = b"\r",
        lines: int = 1,
        accept: Callable[[bytes], bool] | None = None,
    ) -> bytes:
        """Refuse: the channel cannot read the printer's answer.

        Raises:
            UnsupportedOperationError: Always.
        """
        raise unsupported_query(self._uri)

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = MAX_ANSWER_SECONDS) -> bytes:
        """Refuse: the channel cannot read the printer's answer.

        Raises:
            UnsupportedOperationError: Always.
        """
        raise unsupported_query(self._uri)

    def close(self) -> None:
        """Close the descriptor; safe to call twice."""
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)


class DeviceTransport(DescriptorTransport):
    """A device file open for writing."""

    @classmethod
    def open(
        cls, uri: DeviceUri, *, timeout: float, tuning: TransportTuning = DEFAULT_TRANSPORT_TUNING
    ) -> DeviceTransport:
        """Open the existing device ``uri`` names for writing.

        Args:
            uri: The device.
            timeout: Seconds a piece of a job may wait for the device to take any of it (POSIX).
            tuning: The size of those pieces.

        Raises:
            TransportError: The device does not exist or cannot be opened, or is a FIFO no one reads.
        """
        try:
            fd = os.open(uri.path, WRITE_FLAGS)
        except OSError as exc:
            raise transport_error(uri, action=ChannelAction.OPEN, error=exc) from exc
        return cls(fd, uri=uri, timeout=timeout, tuning=tuning)


__all__ = ["WRITE_FLAGS", "DescriptorTransport", "DeviceTransport"]
