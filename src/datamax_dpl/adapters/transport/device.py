"""Device file transport, such as a USB printer class device (``/dev/usb/lp0``).

The device is opened write-only and never created: a mistyped device path must fail rather than
leave a regular file holding the job where the device should be. Reading answers back is left out
because a printer class device does not reliably deliver them.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from datamax_dpl.domain.errors import TransportError
from datamax_dpl.domain.timing import MAX_ANSWER_SECONDS

from .uri import ClosingTransport, DeviceUri, FileUri, channel_name, transport_error, unsupported_query

if TYPE_CHECKING:
    from collections.abc import Callable

# Windows opens files in text mode unless told otherwise, which would rewrite every LF byte of a
# job; POSIX has no such flag.
_BINARY = os.O_BINARY if sys.platform == "win32" else 0
#: Flags a write-only descriptor transport opens its path with; a file adds ``os.O_CREAT``.
WRITE_FLAGS = os.O_WRONLY | os.O_APPEND | _BINARY


class DescriptorTransport(ClosingTransport):
    """A path open for writing through a raw file descriptor; reading answers is refused."""

    def __init__(self, fd: int, *, uri: DeviceUri | FileUri) -> None:
        """Wrap the open file descriptor ``fd``; the transport closes it."""
        self._fd: int | None = fd
        self._uri = uri

    @property
    def bidirectional(self) -> bool:
        """A descriptor transport is written only."""
        return False

    def send(self, data: bytes) -> None:
        """Write ``data`` completely, looping over short writes.

        Raises:
            TransportError: The write failed or the transport is closed.
        """
        if self._fd is None:
            raise TransportError(f"{channel_name(self._uri)}: the transport is closed")
        view = memoryview(data)
        try:
            while view:
                view = view[os.write(self._fd, view) :]
        except OSError as exc:
            raise transport_error(self._uri, action="write to", error=exc) from exc

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
    def open(cls, uri: DeviceUri) -> DeviceTransport:
        """Open the existing device ``uri`` names for writing.

        Raises:
            TransportError: The device does not exist or cannot be opened.
        """
        try:
            fd = os.open(uri.path, WRITE_FLAGS)
        except OSError as exc:
            raise transport_error(uri, action="open", error=exc) from exc
        return cls(fd, uri=uri)


__all__ = ["WRITE_FLAGS", "DescriptorTransport", "DeviceTransport"]
