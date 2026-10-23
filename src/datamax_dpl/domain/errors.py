"""Domain-specific exceptions for typed error handling at boundaries."""

from __future__ import annotations


class ConfigurationError(Exception):
    """Missing, invalid, or incomplete configuration.

    Raised when required configuration values are absent, malformed, or
    logically inconsistent. Typically caught at CLI boundaries to provide
    user-friendly error messages.

    Example:
        >>> from datamax_dpl.domain.errors import ConfigurationError
        >>> err = ConfigurationError("No printer URI configured")
        >>> str(err)
        'No printer URI configured'
    """


__all__ = [
    "ConfigurationError",
]
