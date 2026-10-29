"""Composition root wiring adapters to application ports."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

# Printer and camera
from ..adapters.camera import capture_image
from ..adapters.config.deploy import deploy_configuration
from ..adapters.config.display import display_config

# Configuration services
from ..adapters.config.loader import get_config, get_default_config_path
from ..adapters.config.user_edit import set_user_printer_values

# Logging services
from ..adapters.logging.setup import check_logging_config, init_logging
from ..adapters.transport import open_printer

# Static conformance assertions - pyright verifies that each adapter function
# structurally satisfies its corresponding Protocol at type-check time.
if TYPE_CHECKING:
    from ..adapters.memory import FakeCamera, RecordingPrinter
    from ..application.ports import (
        CaptureImage,
        CheckLoggingConfig,
        Clock,
        DeployConfiguration,
        DisplayConfig,
        GetConfig,
        GetDefaultConfigPath,
        InitLogging,
        OpenPrinter,
        SetPrinterValues,
        Sleep,
    )

    _assert_get_config: GetConfig = get_config
    _assert_get_default_config_path: GetDefaultConfigPath = get_default_config_path
    _assert_deploy_configuration: DeployConfiguration = deploy_configuration
    _assert_display_config: DisplayConfig = display_config
    _assert_init_logging: InitLogging = init_logging
    _assert_check_logging_config: CheckLoggingConfig = check_logging_config
    _assert_open_printer: OpenPrinter = open_printer
    _assert_capture_image: CaptureImage = capture_image
    _assert_set_printer_values: SetPrinterValues = set_user_printer_values
    _assert_sleep: Sleep = time.sleep
    _assert_clock: Clock = time.monotonic


@dataclass(frozen=True, slots=True)
class AppServices:
    """Frozen container holding all application port implementations."""

    get_config: GetConfig
    get_default_config_path: GetDefaultConfigPath
    deploy_configuration: DeployConfiguration
    set_printer_values: SetPrinterValues
    display_config: DisplayConfig
    init_logging: InitLogging
    check_logging_config: CheckLoggingConfig
    open_printer: OpenPrinter
    capture_image: CaptureImage
    sleep: Sleep
    clock: Clock


class _FakeClock:
    """A clock that only moves when slept on, so polling loops finish instantly in tests."""

    def __init__(self) -> None:
        self._now = 0.0

    def now(self) -> float:
        return self._now

    def sleep(self, seconds: float, /) -> None:
        self._now += seconds


def build_production() -> AppServices:
    """Wire production adapters into an AppServices container."""
    return AppServices(
        get_config=get_config,
        get_default_config_path=get_default_config_path,
        deploy_configuration=deploy_configuration,
        set_printer_values=set_user_printer_values,
        display_config=display_config,
        init_logging=init_logging,
        check_logging_config=check_logging_config,
        open_printer=open_printer,
        capture_image=capture_image,
        sleep=time.sleep,
        clock=time.monotonic,
    )


def build_testing(
    *,
    printer: RecordingPrinter | None = None,
    camera: FakeCamera | None = None,
) -> AppServices:
    """Wire in-memory adapters into an AppServices container.

    Args:
        printer: Recording printer every printer URI opens; a fresh one when None.
        camera: Camera double; a fresh one when None.

    Returns:
        AppServices container with in-memory adapters.
    """
    from ..adapters.memory import (  # noqa: PLC0415 - deferred: keeps in-memory test doubles out of the production import path
        FakeCamera,
        RecordingPrinter,
        check_logging_config_in_memory,
        deploy_configuration_in_memory,
        display_config_in_memory,
        get_config_in_memory,
        get_default_config_path_in_memory,
        init_logging_in_memory,
        open_recording_printer,
        set_user_printer_values_in_memory,
    )

    clock = _FakeClock()
    return AppServices(
        get_config=get_config_in_memory,
        get_default_config_path=get_default_config_path_in_memory,
        deploy_configuration=deploy_configuration_in_memory,
        set_printer_values=set_user_printer_values_in_memory,
        display_config=display_config_in_memory,
        init_logging=init_logging_in_memory,
        check_logging_config=check_logging_config_in_memory,
        open_printer=open_recording_printer(printer or RecordingPrinter()),
        capture_image=camera or FakeCamera(),
        sleep=clock.sleep,
        clock=clock.now,
    )


__all__ = [
    "AppServices",
    "build_production",
    "build_testing",
    "check_logging_config",
    "deploy_configuration",
    "display_config",
    "get_config",
    "get_default_config_path",
    "init_logging",
]
