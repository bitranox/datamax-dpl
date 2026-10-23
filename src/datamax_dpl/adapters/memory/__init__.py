"""In-memory adapter implementations for testing.

Provides lightweight implementations of all application ports that operate
entirely in memory -- no filesystem, no network, and logging to a quiet lib_log_rich
runtime with no system sinks (the commands bind against a live runtime).

Contents:
    * :mod:`.config` - In-memory configuration adapters
    * :mod:`.logging` - In-memory logging adapter
    * :mod:`.camera` - Camera double writing a fixed image
    * :mod:`.printer` - Recording printer transport and its opener
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .camera import FakeCamera
from .config import (
    deploy_configuration_in_memory,
    display_config_in_memory,
    get_config_in_memory,
    get_default_config_path_in_memory,
)
from .logging import check_logging_config_in_memory, init_logging_in_memory
from .printer import RecordingPrinter, open_recording_printer

# Static conformance assertions
if TYPE_CHECKING:
    from datamax_dpl.application.ports import (
        CheckLoggingConfig,
        GetConfig,
        GetDefaultConfigPath,
        InitLogging,
        PrinterTransport,
    )

    _assert_get_config: GetConfig = get_config_in_memory
    _assert_get_default_config_path: GetDefaultConfigPath = get_default_config_path_in_memory
    _assert_init_logging: InitLogging = init_logging_in_memory
    _assert_check_logging_config: CheckLoggingConfig = check_logging_config_in_memory
    _assert_recording_printer: PrinterTransport = RecordingPrinter()

__all__ = [
    "FakeCamera",
    "RecordingPrinter",
    "check_logging_config_in_memory",
    "deploy_configuration_in_memory",
    "display_config_in_memory",
    "get_config_in_memory",
    "get_default_config_path_in_memory",
    "init_logging_in_memory",
    "open_recording_printer",
]
