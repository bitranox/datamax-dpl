"""Printer URIs: which channel to open and with what settings.

Accepted forms (see :data:`ACCEPTED_FORMS`):

* ``tcp://HOST:PORT`` - raw socket, port 9100 when left out; an IPv6 host goes in brackets.
* ``serial:///dev/ttyS0?baud=9600&parity=N&bytesize=8&stopbits=1&rtscts=0&xonxoff=1`` (POSIX) or
  ``serial://COM3?baud=...`` (Windows) - every query parameter is optional.
* ``device:///dev/usb/lp0`` - a write-only device file.
* ``file:PATH`` or ``file:///abs/path`` - append the job to a file.
* ``-`` - write the job to standard output.

The module also holds what every transport shares: the timeout check, the wording of a wrapped
``OSError``, the one-way refusal and the context-manager base class.
"""

from __future__ import annotations

import ipaddress
import math
import re
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Literal, TypeVar
from urllib.parse import parse_qsl, unquote, urlsplit

from pydantic import BaseModel, Field

from datamax_dpl.domain.errors import PrinterTimeoutError, TransportError, UnsupportedOperationError

if TYPE_CHECKING:
    from types import TracebackType

#: The URI forms :func:`parse_printer_uri` accepts, quoted in every refusal.
ACCEPTED_FORMS = (
    "tcp://HOST[:PORT], serial:///dev/ttyS0?baud=9600&parity=N&bytesize=8&stopbits=1&rtscts=0&xonxoff=0, "
    "serial://COM3?baud=..., device:///dev/usb/lp0, file:PATH, file:///abs/path, - (stdout)"
)
#: The raw printing port Datamax network cards listen on.
DEFAULT_TCP_PORT = 9100
#: The manual's default serial speed.
DEFAULT_BAUD = 9600
MAX_TCP_PORT = 65535
#: Largest timeout every platform accepts: Windows stores a socket timeout as 2**31-1 ms.
MAX_TIMEOUT_SECONDS = 2147483.0

_STDOUT = "-"
_TCP_PREFIX = "tcp://"
_SERIAL_PREFIX = "serial://"
_DEVICE_PREFIX = "device://"
_FILE_PREFIX = "file:"
_FILE_URL_PREFIX = "file://"
_HOST_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")
_BAUD = re.compile(r"[0-9]{1,7}")
_WINDOWS_DRIVE = re.compile(r"/[A-Za-z]:[/\\]")
_SERIAL_KEYS = frozenset({"baud", "parity", "bytesize", "stopbits", "rtscts", "xonxoff"})
_FLAGS = {"0": False, "1": True}
_BYTESIZES: dict[str, Literal[7, 8]] = {"7": 7, "8": 8}
_STOPBITS: dict[str, Literal[1, 2]] = {"1": 1, "2": 2}
_T = TypeVar("_T")
_Closing = TypeVar("_Closing", bound="ClosingTransport")


class Parity(str, Enum):
    """Serial parity as the URI writes it.

    Example:
        >>> Parity("E") is Parity.EVEN
        True
    """

    NONE = "N"
    EVEN = "E"
    ODD = "O"


class TcpUri(BaseModel, frozen=True, extra="forbid"):
    """A raw TCP socket, as print servers and network cards offer on port 9100."""

    kind: Literal["tcp"] = "tcp"
    host: str
    port: int = Field(default=DEFAULT_TCP_PORT, ge=1, le=MAX_TCP_PORT)

    @property
    def target(self) -> str:
        """``host:port``, with an IPv6 host in brackets."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"


class SerialUri(BaseModel, frozen=True, extra="forbid"):
    """A serial port and its line settings."""

    kind: Literal["serial"] = "serial"
    path: str
    baud: int = Field(default=DEFAULT_BAUD, gt=0)
    parity: Parity = Parity.NONE
    bytesize: Literal[7, 8] = 8
    stopbits: Literal[1, 2] = 1
    rtscts: bool = False
    xonxoff: bool = False

    @property
    def target(self) -> str:
        """The port path."""
        return self.path


class DeviceUri(BaseModel, frozen=True, extra="forbid"):
    """A device file the job is written to, such as a USB printer class device."""

    kind: Literal["device"] = "device"
    path: str

    @property
    def target(self) -> str:
        """The device path."""
        return self.path


class FileUri(BaseModel, frozen=True, extra="forbid"):
    """A regular file the job is appended to."""

    kind: Literal["file"] = "file"
    path: str

    @property
    def target(self) -> str:
        """The file path."""
        return self.path


class StdoutUri(BaseModel, frozen=True, extra="forbid"):
    """Standard output."""

    kind: Literal["stdout"] = "stdout"

    @property
    def target(self) -> str:
        """Always ``stdout``."""
        return "stdout"


PrinterUri = Annotated[TcpUri | SerialUri | DeviceUri | FileUri | StdoutUri, Field(discriminator="kind")]


#: A configured printer's name: a word that can never be read as a URI (no ``:``, never ``-``).
_PRINTER_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}")


def is_printer_uri(text: str) -> bool:
    """Return True when ``text`` names a printer by address, as :func:`parse_printer_uri` reads it.

    It checks the form only (``-`` or a known scheme prefix); whether the rest parses is
    :func:`parse_printer_uri`'s job.

    Example:
        >>> is_printer_uri("tcp://192.0.2.60:9100"), is_printer_uri("-"), is_printer_uri("lager")
        (True, True, False)
    """
    return text == _STDOUT or text.startswith((_TCP_PREFIX, _SERIAL_PREFIX, _DEVICE_PREFIX, _FILE_PREFIX))


def is_printer_name(text: str) -> bool:
    """Return True when ``text`` is a valid name for a configured printer (``[printers.<name>]``).

    Example:
        >>> is_printer_name("lager"), is_printer_name("a:b"), is_printer_name("-")
        (True, False, False)
    """
    return _PRINTER_NAME.fullmatch(text) is not None


def parse_printer_uri(uri: str) -> PrinterUri:
    """Parse a printer URI into its typed form.

    Args:
        uri: One of the forms in :data:`ACCEPTED_FORMS`.

    Returns:
        The parsed URI; its ``kind`` names the transport.

    Raises:
        ValueError: The scheme is unknown or a value is malformed; the message names the
            accepted forms.

    Example:
        >>> parse_printer_uri("tcp://[::1]").target
        '[::1]:9100'
        >>> parse_printer_uri("serial:///dev/ttyS0?baud=19200&parity=E").baud
        19200
    """
    try:
        return _parse(uri)
    except ValueError as exc:
        raise ValueError(f"printer URI {uri!r}: {exc}; accepted forms: {ACCEPTED_FORMS}") from exc


def check_timeout(timeout: float, *, name: str = "timeout") -> float:
    """Return ``timeout`` when it is a usable number of seconds.

    Args:
        timeout: Seconds.
        name: What the seconds are, for the refusal message (``timeout``, ``idle``).

    Returns:
        ``timeout`` unchanged.

    Raises:
        ValueError: ``timeout`` is not above 0, not finite, or above :data:`MAX_TIMEOUT_SECONDS`.

    Example:
        >>> check_timeout(2.5)
        2.5
    """
    if not (math.isfinite(timeout) and 0 < timeout <= MAX_TIMEOUT_SECONDS):
        raise ValueError(f"{name} must be above 0 and at most {MAX_TIMEOUT_SECONDS:.0f} seconds, got {timeout!r}")
    return timeout


def transport_error(uri: PrinterUri, *, action: str, error: OSError) -> TransportError:
    """Turn an ``OSError`` into the :class:`~datamax_dpl.domain.errors.TransportError` callers see.

    A timeout becomes :class:`~datamax_dpl.domain.errors.PrinterTimeoutError`, so a caller can
    tell a silent printer from a missing one.

    Args:
        uri: The channel the error happened on.
        action: What was attempted, e.g. ``open`` or ``write to``.
        error: The operating system's error.

    Returns:
        The error to raise (chain it with ``from error``); its message names the URI kind, the
        target and the reason in one line.

    Example:
        >>> str(transport_error(StdoutUri(), action="write to", error=BrokenPipeError(32, "Broken pipe")))
        'cannot write to stdout: Broken pipe'
        >>> type(transport_error(StdoutUri(), action="read from", error=TimeoutError())).__name__
        'PrinterTimeoutError'
    """
    reason = error.strerror or str(error) or type(error).__name__
    message = f"cannot {action} {channel_name(uri)}: {reason}"
    if isinstance(error, TimeoutError):
        return PrinterTimeoutError(message)
    return TransportError(message)


class ClosingTransport:
    """Context-manager behaviour shared by every transport: leaving the block closes the channel."""

    def close(self) -> None:
        """Release the channel; overridden by each transport."""

    def __enter__(self: _Closing) -> _Closing:
        """Return the transport itself."""
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the transport, whether or not the block raised."""
        self.close()


def unsupported_query(uri: PrinterUri) -> UnsupportedOperationError:
    """Return the error a one-way channel raises when asked to read an answer.

    Example:
        >>> print(unsupported_query(FileUri(path="job.dpl")))
        file job.dpl is one-way: it cannot read the printer's answer
    """
    return UnsupportedOperationError(f"{channel_name(uri)} is one-way: it cannot read the printer's answer")


def channel_name(uri: PrinterUri) -> str:
    """Name the channel for a message: the URI kind and its target.

    Example:
        >>> channel_name(TcpUri(host="10.0.0.5")), channel_name(StdoutUri())
        ('tcp 10.0.0.5:9100', 'stdout')
    """
    return uri.kind if isinstance(uri, StdoutUri) else f"{uri.kind} {uri.target}"


def _parse(uri: str) -> PrinterUri:
    """Dispatch on the scheme prefix."""
    if any(char in uri for char in "\x00\r\n\t"):
        raise ValueError("control characters are not allowed")
    if uri == _STDOUT:
        return StdoutUri()
    if uri.startswith(_TCP_PREFIX):
        return _parse_tcp(uri)
    if uri.startswith(_SERIAL_PREFIX):
        return _parse_serial(uri)
    if uri.startswith(_DEVICE_PREFIX):
        return DeviceUri(path=_port_path(uri, what="device path"))
    if uri.startswith(_FILE_PREFIX):
        return FileUri(path=_file_path(uri))
    raise ValueError("unknown scheme")


def _parse_tcp(uri: str) -> TcpUri:
    """Parse ``tcp://HOST[:PORT]``."""
    parts = urlsplit(uri)
    if parts.path not in ("", "/") or parts.query or parts.fragment or "@" in parts.netloc:
        raise ValueError("a tcp URI takes only a host and a port")
    host = parts.hostname or ""
    port = parts.port  # raises ValueError for a non-numeric or out-of-range port
    if parts.netloc.startswith("["):
        ipaddress.IPv6Address(host)
    elif _HOST_NAME.fullmatch(host) is None:
        raise ValueError(f"invalid host {host!r}")
    if port == 0 or parts.netloc.endswith(":"):
        raise ValueError("the port must be a number from 1 to 65535")
    return TcpUri(host=host, port=DEFAULT_TCP_PORT if port is None else port)


def _parse_serial(uri: str) -> SerialUri:
    """Parse ``serial:///dev/ttyS0?...`` or ``serial://COM3?...``."""
    path = _port_path(uri, what="serial port")
    query = urlsplit(uri).query
    pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True) if query else []
    settings = dict(pairs)
    if len(settings) != len(pairs):
        raise ValueError("a serial setting is given twice")
    unknown = sorted(set(settings) - _SERIAL_KEYS)
    if unknown:
        raise ValueError(f"unknown serial setting(s) {', '.join(unknown)}")
    return SerialUri(
        path=path,
        baud=_baud(settings.get("baud", str(DEFAULT_BAUD))),
        parity=_choice(settings.get("parity", "N").upper(), {p.value: p for p in Parity}, what="parity"),
        bytesize=_choice(settings.get("bytesize", "8"), _BYTESIZES, what="bytesize"),
        stopbits=_choice(settings.get("stopbits", "1"), _STOPBITS, what="stopbits"),
        rtscts=_choice(settings.get("rtscts", "0"), _FLAGS, what="rtscts"),
        xonxoff=_choice(settings.get("xonxoff", "0"), _FLAGS, what="xonxoff"),
    )


def _port_path(uri: str, *, what: str) -> str:
    """Return the path of ``scheme:///abs/path`` or the name of ``scheme://NAME``, never both."""
    parts = urlsplit(uri)
    if parts.fragment:
        raise ValueError(f"a {what} takes no #fragment")
    if parts.netloc and parts.path:
        raise ValueError(f"{what} must be scheme:///abs/path or scheme://NAME")
    path = unquote(parts.netloc or parts.path)
    if not path:
        raise ValueError(f"{what} is missing")
    return path


def _file_path(uri: str) -> str:
    """Return the path of ``file:PATH`` (taken literally) or ``file:///abs/path`` (percent-decoded)."""
    if not uri.startswith(_FILE_URL_PREFIX):
        path = uri[len(_FILE_PREFIX) :]
        if not path:
            raise ValueError("file path is missing")
        return path
    parts = urlsplit(uri)
    if parts.netloc or parts.query or parts.fragment or not parts.path:
        raise ValueError("a file URL must be file:///abs/path")
    path = unquote(parts.path)
    # file:///C:/x names the Windows path C:/x; the slash in front belongs to the URL syntax.
    return path[1:] if _WINDOWS_DRIVE.match(path) else path


def _baud(text: str) -> int:
    """Return a positive baud rate."""
    if _BAUD.fullmatch(text) is None or int(text) == 0:
        raise ValueError(f"baud must be a positive whole number, got {text!r}")
    return int(text)


def _choice(text: str, choices: dict[str, _T], *, what: str) -> _T:
    """Return ``choices[text]`` or refuse with the allowed spellings."""
    if text not in choices:
        raise ValueError(f"{what} must be one of {'/'.join(choices)}, got {text!r}")
    return choices[text]


__all__ = [
    "ACCEPTED_FORMS",
    "DEFAULT_BAUD",
    "DEFAULT_TCP_PORT",
    "MAX_TCP_PORT",
    "MAX_TIMEOUT_SECONDS",
    "ClosingTransport",
    "DeviceUri",
    "FileUri",
    "Parity",
    "PrinterUri",
    "SerialUri",
    "StdoutUri",
    "TcpUri",
    "channel_name",
    "check_timeout",
    "is_printer_name",
    "is_printer_uri",
    "parse_printer_uri",
    "transport_error",
    "unsupported_query",
]
