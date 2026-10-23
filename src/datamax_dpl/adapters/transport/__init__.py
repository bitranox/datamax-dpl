"""Byte transports to a printer: raw TCP, serial, device file, file and stdout.

:func:`open_printer` is the :class:`~datamax_dpl.application.ports.OpenPrinter` adapter: it parses
the printer URI and opens the matching transport. Every operating-system failure reaches the
caller as a :class:`~datamax_dpl.domain.errors.TransportError` whose one-line message names the
URI kind and target, never as a bare ``OSError``.

Contents:
    * :mod:`.uri` - URI parsing and the helpers every transport shares
    * :mod:`.tcp` - raw socket
    * :mod:`.serial` - serial port (pyserial)
    * :mod:`.device` - write-only device file
    * :mod:`.file` - file and stdout
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamax_dpl.domain.errors import ConfigurationError, TransportError

from .device import DescriptorTransport, DeviceTransport
from .file import FileTransport, StdoutTransport
from .serial import SerialPort, SerialTransport
from .tcp import MAX_ANSWER_BYTES, TcpTransport
from .uri import (
    ACCEPTED_FORMS,
    DeviceUri,
    FileUri,
    Parity,
    PrinterUri,
    SerialUri,
    StdoutUri,
    TcpUri,
    check_timeout,
    parse_printer_uri,
)

if TYPE_CHECKING:
    from datamax_dpl.application.ports import OpenPrinter, PrinterTransport


def open_printer(uri: str, *, timeout: float) -> PrinterTransport:
    """Open the transport ``uri`` names.

    Args:
        uri: A printer URI, see :data:`~.uri.ACCEPTED_FORMS`.
        timeout: Seconds for connecting and for every write.

    Returns:
        The open transport; close it (or use it as a context manager) when done.

    Raises:
        ConfigurationError: ``uri`` is not a printer URI.
        ValueError: ``timeout`` is not a usable number of seconds.
        TransportError: The channel could not be opened (refused, no such device, permission
            denied); a connection timeout is the subclass ``PrinterTimeoutError``.

    Example:
        >>> open_printer("lpd://queue", timeout=1.0)
        Traceback (most recent call last):
        ...
        datamax_dpl.domain.errors.ConfigurationError: printer URI 'lpd://queue': unknown scheme; ...
    """
    check_timeout(timeout)
    try:
        parsed = parse_printer_uri(uri)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    return _open(parsed, timeout=timeout)


def _open(uri: PrinterUri, *, timeout: float) -> PrinterTransport:
    """Open the transport for the parsed ``uri``."""
    if isinstance(uri, TcpUri):
        return TcpTransport.connect(uri, timeout=timeout)
    if isinstance(uri, SerialUri):
        return SerialTransport.open(uri, timeout=timeout)
    if isinstance(uri, DeviceUri):
        return DeviceTransport.open(uri)
    if isinstance(uri, FileUri):
        return FileTransport.open(uri)
    return StdoutTransport()


if TYPE_CHECKING:
    _assert_open_printer: OpenPrinter = open_printer
    _assert_tcp: PrinterTransport = TcpTransport.connect(TcpUri(host="h"), timeout=1.0)
    _assert_serial: PrinterTransport = SerialTransport.open(SerialUri(path="p"), timeout=1.0)
    _assert_device: PrinterTransport = DeviceTransport.open(DeviceUri(path="p"))
    _assert_file: PrinterTransport = FileTransport.open(FileUri(path="p"))
    _assert_stdout: PrinterTransport = StdoutTransport()

__all__ = [
    "ACCEPTED_FORMS",
    "MAX_ANSWER_BYTES",
    "DescriptorTransport",
    "DeviceTransport",
    "DeviceUri",
    "FileTransport",
    "FileUri",
    "Parity",
    "PrinterUri",
    "SerialPort",
    "SerialTransport",
    "SerialUri",
    "StdoutTransport",
    "StdoutUri",
    "TcpTransport",
    "TcpUri",
    "TransportError",
    "open_printer",
    "parse_printer_uri",
]
