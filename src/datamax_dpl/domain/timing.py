"""Seconds a printer exchange may wait: the one rule every timeout, idle and deadline obeys.

It lives in the domain so the application can refuse a bad value before any channel is opened,
and the transports apply the same rule to what reaches them directly.
"""

from __future__ import annotations

import math

#: Largest timeout every platform accepts: Windows stores a socket timeout as 2**31-1 ms.
MAX_TIMEOUT_SECONDS = 2147483.0
#: Longest an answer read until silence (``PrinterTransport.read_answer``) may take, from the
#: request to the silence that ends it. Without it a device sending one byte just inside every
#: ``idle`` would keep the read going until the size bound, for days. Sized on the slowest link, a
#: 9600 baud 8N1 serial line moving 960 bytes/s: a realistic worst listing of 64 KiB (about 3,800
#: full-length names over five modules, more than a 256K or 512K module holds) takes 68 s there,
#: and 120 s leaves margin. A listing that genuinely needs longer is refused rather than read
#: without end; an Allegro 2's whole answer was 100 bytes.
MAX_ANSWER_SECONDS = 120.0


def check_timeout(timeout: float, *, name: str = "timeout") -> float:
    """Return ``timeout`` when it is a usable number of seconds.

    Args:
        timeout: Seconds.
        name: What the seconds are, for the refusal message (``timeout``, ``idle``, ``total``).

    Returns:
        ``timeout`` unchanged.

    Raises:
        ValueError: ``timeout`` is not above 0, not finite, or above :data:`MAX_TIMEOUT_SECONDS`.

    Example:
        >>> check_timeout(2.5)
        2.5
        >>> check_timeout(0.0, name="idle")
        Traceback (most recent call last):
        ...
        ValueError: idle must be above 0 and at most 2147483 seconds, got 0.0
    """
    if not (math.isfinite(timeout) and 0 < timeout <= MAX_TIMEOUT_SECONDS):
        raise ValueError(f"{name} must be above 0 and at most {MAX_TIMEOUT_SECONDS:.0f} seconds, got {timeout!r}")
    return timeout


__all__ = ["MAX_ANSWER_SECONDS", "MAX_TIMEOUT_SECONDS", "check_timeout"]
