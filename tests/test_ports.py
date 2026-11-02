"""Port behavioral contract tests - verify in-memory adapter implementations.

Tests exercise in-memory adapters only - production adapters are tested
via CLI integration tests. Static type conformance is enforced by pyright.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import lib_log_rich.runtime
import pytest
from lib_layered_config import Config

from datamax_dpl.adapters.memory import (
    get_config_in_memory,
    get_default_config_path_in_memory,
    init_logging_in_memory,
)

if TYPE_CHECKING:
    from datamax_dpl.application.ports import (
        GetConfig,
        GetDefaultConfigPath,
        InitLogging,
    )


# ======================== In-Memory Adapter Contract Tests ========================


@pytest.fixture
def get_config_impl() -> GetConfig:
    """Provide in-memory GetConfig implementation."""
    return get_config_in_memory


@pytest.fixture
def get_default_config_path_impl() -> GetDefaultConfigPath:
    """Provide in-memory GetDefaultConfigPath implementation."""
    return get_default_config_path_in_memory


@pytest.fixture
def init_logging_impl() -> InitLogging:
    """Provide in-memory InitLogging implementation."""
    return init_logging_in_memory


@pytest.mark.os_agnostic
def test_get_config_returns_an_empty_config_whatever_it_is_asked(get_config_impl: GetConfig) -> None:
    """No file, no environment: a test double reading neither answers empty to every argument."""
    assert get_config_impl().as_dict() == {}
    assert get_config_impl(profile="staging", start_dir="/opt/x", dotenv_path="/opt/x/.env").as_dict() == {}


@pytest.mark.os_agnostic
def test_get_default_config_path_returns_toml_path(get_default_config_path_impl: GetDefaultConfigPath) -> None:
    """GetDefaultConfigPath must return a Path ending in .toml."""
    path = get_default_config_path_impl()
    assert isinstance(path, Path)
    assert path.suffix == ".toml"


@pytest.mark.os_agnostic
def test_init_logging_leaves_a_runtime_a_command_can_bind_against(init_logging_impl: InitLogging) -> None:
    """Every command binds onto the process-global runtime, which raises when none is live."""
    if lib_log_rich.runtime.is_initialised():
        lib_log_rich.runtime.shutdown()
    config = Config({}, {})
    init_logging_impl(config)
    try:
        assert lib_log_rich.runtime.is_initialised()
        init_logging_impl(config)
        with lib_log_rich.runtime.bind(job_id="a-job", extra={}):
            assert lib_log_rich.runtime.is_initialised()
    finally:
        lib_log_rich.runtime.shutdown()


# ======================== Composition Wiring Tests ========================
