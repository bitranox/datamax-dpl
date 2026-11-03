"""Seconds a printer exchange may wait: the one rule every timeout, idle and deadline obeys.

It lives in the domain so the application can refuse a bad value before any channel is opened,
and the transports apply the same rule to what reaches them directly. :class:`TransportTuning`
carries the settings that pace a transport's writes, which decide what a write timeout measures.
"""

from __future__ import annotations

import math
from enum import Enum

from pydantic import BaseModel, Field

#: Largest timeout every platform accepts: Windows stores a socket timeout as 2**31-1 ms.
MAX_TIMEOUT_SECONDS = 2147483.0
#: Default of ``[printer] timeout``: seconds for one transport operation (a connect, a write of one
#: piece of a job, the first byte of an answer).
TIMEOUT_SECONDS = 10.0
#: Longest an answer read until silence (``PrinterTransport.read_answer``) may take, from the
#: request to the silence that ends it. Without it a device sending one byte just inside every
#: ``idle`` would keep the read going until the size bound, for days. Sized on the slowest link, a
#: 9600 baud 8N1 serial line moving 960 bytes/s: a realistic worst listing of 64 KiB (about 3,800
#: full-length names over five modules, more than a 256K or 512K module holds) takes 68 s there,
#: and 120 s leaves margin. A listing that genuinely needs longer is refused rather than read
#: without end; an Allegro 2's whole answer was 100 bytes.
MAX_ANSWER_SECONDS = 120.0
#: Default of ``[printer] send_chunk_bytes``: the largest piece of a job handed to one write call.
#: A write timeout bounds a whole call, so the piece size decides what the timeout measures. At
#: about 1390 bytes/s (a 9600 baud printer behind a print server, measured) a 64 KiB piece needs
#: 47 s and fails a 10 s timeout although the printer is taking data; 1 KiB needs under a second,
#: so the timeout then means the printer stopped taking data.
SEND_CHUNK_BYTES = 1024
#: Largest ``send_chunk_bytes`` taken. Windows takes one send of any size whole while its socket
#: buffer is not full, so a larger piece would come back "sent" from a printer that reads nothing
#: and the timeout would never fire (an 8 MiB piece of a 4 MiB job returned in 0.00 s, measured).
#: With pieces of at most 64 KiB a printer that stops reading fills the buffer, and a later piece
#: times out.
MAX_SEND_CHUNK_BYTES = 65536
#: Default of ``[printer] refused_connect_wait``: longest one opening of a TCP connection retries a
#: refused connect. A print server that just closed a job's connection refuses the next one for a
#: moment (about 1-2 s measured on a SerComm PS301), so a status query sent right after a print would
#: otherwise fail although nothing is wrong. Other connect errors (unknown host, unreachable,
#: timeout) are not retried.
REFUSED_CONNECT_WAIT_SECONDS = 3.0
#: Default of ``[printer] refused_connect_retry_interval``: the pause before a refused connect is
#: tried again within that window.
REFUSED_CONNECT_RETRY_INTERVAL_SECONDS = 0.25


class TransportTuning(BaseModel, frozen=True, extra="forbid"):
    """How the transports pace what they write, from the printer settings.

    Attributes:
        send_chunk_bytes: Largest piece of a job one write call takes, at most
            :data:`MAX_SEND_CHUNK_BYTES`; the write timeout bounds each piece.
        refused_connect_wait: Longest a TCP opening retries a refused connect, never longer
            than its timeout.
        refused_connect_retry_interval: Pause before a refused connect is tried again.

    Example:
        >>> TransportTuning().send_chunk_bytes
        1024
    """

    send_chunk_bytes: int = Field(default=SEND_CHUNK_BYTES, gt=0, le=MAX_SEND_CHUNK_BYTES)
    refused_connect_wait: float = Field(default=REFUSED_CONNECT_WAIT_SECONDS, gt=0, le=MAX_TIMEOUT_SECONDS)
    refused_connect_retry_interval: float = Field(
        default=REFUSED_CONNECT_RETRY_INTERVAL_SECONDS, gt=0, le=MAX_TIMEOUT_SECONDS
    )


#: The tuning a transport uses when it is given none.
DEFAULT_TRANSPORT_TUNING = TransportTuning()


class TimeoutName(str, Enum):
    """Which seconds :func:`check_timeout` checks, as its refusal names them.

    Example:
        >>> TimeoutName.RETRY_INTERVAL.value
        'retry interval'
    """

    TIMEOUT = "timeout"
    IDLE = "idle"
    TOTAL = "total"
    WAIT = "wait"
    RESET_CHECK_WAIT = "reset check wait"
    RETRY_INTERVAL = "retry interval"


def check_timeout(timeout: float, *, name: TimeoutName = TimeoutName.TIMEOUT) -> float:
    """Return ``timeout`` when it is a usable number of seconds.

    Args:
        timeout: Seconds.
        name: What the seconds are, for the refusal message.

    Returns:
        ``timeout`` unchanged.

    Raises:
        ValueError: ``timeout`` is not above 0, not finite, or above :data:`MAX_TIMEOUT_SECONDS`.

    Example:
        >>> check_timeout(2.5)
        2.5
        >>> check_timeout(0.0, name=TimeoutName.IDLE)
        Traceback (most recent call last):
        ...
        ValueError: idle must be above 0 and at most 2147483 seconds, got 0.0
    """
    try:
        usable = math.isfinite(timeout) and 0 < timeout <= MAX_TIMEOUT_SECONDS
    except OverflowError:
        # An int past what a float holds: isfinite converts it first.
        usable = False
    if not usable:
        raise ValueError(f"{name.value} must be above 0 and at most {MAX_TIMEOUT_SECONDS:.0f} seconds, got {timeout!r}")
    return timeout


__all__ = [
    "DEFAULT_TRANSPORT_TUNING",
    "MAX_ANSWER_SECONDS",
    "MAX_SEND_CHUNK_BYTES",
    "MAX_TIMEOUT_SECONDS",
    "REFUSED_CONNECT_RETRY_INTERVAL_SECONDS",
    "REFUSED_CONNECT_WAIT_SECONDS",
    "SEND_CHUNK_BYTES",
    "TIMEOUT_SECONDS",
    "TimeoutName",
    "TransportTuning",
    "check_timeout",
]
