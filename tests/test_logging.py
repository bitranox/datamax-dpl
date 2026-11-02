"""Tests for the logging configuration model.

LoggingConfigModel validation is tested here. The init_logging function
is tested via CLI integration tests in test_cli_core.py.
"""

from __future__ import annotations

import pytest

from datamax_dpl.adapters.logging.setup import LoggingConfigModel


@pytest.mark.os_agnostic
def test_logging_config_model_reads_only_its_own_two_settings() -> None:
    """The section's other keys are lib_log_rich's: the model neither keeps nor refuses them."""
    parsed = LoggingConfigModel.model_validate({"service": "test", "environment": "dev", "custom_field": "value"})

    assert parsed.service == "test"
    assert parsed.environment == "dev"
    assert parsed.model_dump() == {"service": "test", "environment": "dev"}


@pytest.mark.os_agnostic
def test_logging_config_model_defaults() -> None:
    """Empty input produces sensible defaults."""
    parsed = LoggingConfigModel.model_validate({})

    assert parsed.service is None
    assert parsed.environment == "prod"
