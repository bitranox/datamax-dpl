"""Domain-level exception types.

Each error names one way a request can fail, so the CLI maps it to its own exit code and the
JSON envelope carries the class name as the error type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .findings import Finding


class ConfigurationError(Exception):
    """Configuration is missing or invalid.

    Example:
        >>> from datamax_dpl.domain.errors import ConfigurationError
        >>> err = ConfigurationError("No printer URI configured")
        >>> str(err)
        'No printer URI configured'
    """


class LabelRejectedError(Exception):
    """The label cannot be rendered for the chosen model (an error-level finding).

    The findings travel with the exception so a caller can report every reason at once.
    """

    def __init__(self, message: str, findings: tuple[Finding, ...] = ()) -> None:
        """Keep the message and the findings that caused the rejection."""
        super().__init__(message)
        self.findings = findings


class TransportError(Exception):
    """A printer channel could not be opened, written or read.

    Example:
        >>> str(TransportError("connection refused"))
        'connection refused'
    """


class PrinterTimeoutError(TransportError):
    """The printer did not answer, or did not finish, within the timeout."""


class UnsupportedOperationError(TransportError):
    """The channel cannot do what was asked, such as reading an answer from a file or stdout."""


class PrinterProtocolError(TransportError):
    """The printer answered something the DPL manual does not describe for that request."""


class CameraError(Exception):
    """The camera could not take or deliver a photo."""


__all__ = [
    "CameraError",
    "ConfigurationError",
    "LabelRejectedError",
    "PrinterProtocolError",
    "PrinterTimeoutError",
    "TransportError",
    "UnsupportedOperationError",
]
