"""Domain error types: instantiation and message preservation."""

from __future__ import annotations

import pytest

from datamax_dpl.domain.errors import ConfigurationError


@pytest.mark.os_agnostic
def test_configuration_error_preserves_message() -> None:
    """Instantiation stores the message for display."""
    exc = ConfigurationError("No device host configured")
    assert str(exc) == "No device host configured"
