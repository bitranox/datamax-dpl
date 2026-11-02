"""Domain-level exception types.

Each error names one way a request can fail, so the CLI maps it to its own exit code and the
JSON envelope carries the class name as the error type. All of them derive from
:class:`DatamaxDplError`, so a library caller can catch everything this package raises at once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .findings import Finding


class DatamaxDplError(Exception):
    """Base of every exception this package raises on purpose.

    A type that used to be a plain ``ValueError`` or ``LookupError`` keeps that as a second base, so
    a caller already catching it still does.

    Example:
        >>> issubclass(ConfigurationError, DatamaxDplError), issubclass(PreviewTooLongError, ValueError)
        (True, True)
    """


class ConfigurationError(DatamaxDplError):
    """Configuration is missing or invalid.

    Example:
        >>> from datamax_dpl.domain.errors import ConfigurationError
        >>> err = ConfigurationError("No printer URI configured")
        >>> str(err)
        'No printer URI configured'
    """


class LabelRejectedError(DatamaxDplError):
    """The label cannot be rendered for the chosen model (an error-level finding).

    The findings travel with the exception so a caller can report every reason at once.
    """

    def __init__(self, message: str, findings: tuple[Finding, ...] = ()) -> None:
        """Keep the message and the findings that caused the rejection."""
        super().__init__(message)
        self.findings = findings


class TransportError(DatamaxDplError):
    """A printer channel could not be opened, written or read.

    ``unanswered`` marks a channel that took no request: the connection was refused or timed
    out, or the channel hung up before the request was sent or before a byte of answer came. No
    answer is owed on it, so asking again on a new connection leaves no second request
    outstanding. Every other failure leaves it False.

    ``advice`` is what the reader might do about it; the message ends with it, and ``reason`` is
    the message without it, for a caller that gives advice of its own.

    Example:
        >>> str(TransportError("connection refused"))
        'connection refused'
        >>> TransportError("connection refused", unanswered=True).unanswered
        True
        >>> error = TransportError("no answer", advice="try again")
        >>> str(error), error.reason
        ('no answer; try again', 'no answer')
    """

    def __init__(self, message: str, *, unanswered: bool = False, advice: str = "") -> None:
        """Keep the message, the advice that ends it and whether the channel took no request."""
        super().__init__(f"{message}; {advice}" if advice else message)
        self.reason = message
        self.advice = advice
        self.unanswered = unanswered


class PrinterTimeoutError(TransportError):
    """The printer did not answer, or did not finish, within the timeout."""


class AnswerNotEndedError(PrinterTimeoutError):
    """The printer answered, but its answer was still arriving when the whole wait for it ended.

    It took the request, so asking again would only start a second answer behind the first.
    """


class UnsupportedOperationError(TransportError):
    """The channel cannot do what was asked, such as reading an answer from a file or stdout."""


class PrinterProtocolError(TransportError):
    """The printer answered something the DPL manual does not describe for that request."""


class CameraError(DatamaxDplError):
    """The camera could not take or deliver a photo."""


class BatchTooLargeError(DatamaxDplError, ValueError):
    """A batch's jobs passed the byte limit it was rendered under; the records after that were not rendered."""


class PreviewTooLongError(DatamaxDplError, ValueError):
    """A preview would be longer than the limit it was asked to keep to; nothing was drawn."""


__all__ = [
    "AnswerNotEndedError",
    "BatchTooLargeError",
    "CameraError",
    "ConfigurationError",
    "DatamaxDplError",
    "LabelRejectedError",
    "PreviewTooLongError",
    "PrinterProtocolError",
    "PrinterTimeoutError",
    "TransportError",
    "UnsupportedOperationError",
]
