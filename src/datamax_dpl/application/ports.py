"""Application ports - callable Protocol definitions for adapter functions.

Each Protocol class defines a ``__call__`` method whose signature exactly
matches the corresponding adapter function.  Existing module-level functions
satisfy these protocols automatically via structural subtyping (PEP 544).

System Role:
    Sits between domain and adapters.  Infrastructure types (``Config``)
    are imported under ``TYPE_CHECKING`` only so that
    import-linter layer contracts remain satisfied at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from lib_layered_config import Config

    from ..domain.camera import CameraSettings, CapturedImage
    from ..domain.enums import DeployTarget, OutputFormat


class GetConfig(Protocol):
    """Load layered configuration with application defaults."""

    def __call__(
        self, *, profile: str | None = ..., start_dir: str | None = ..., dotenv_path: str | None = ...
    ) -> Config: ...


class GetDefaultConfigPath(Protocol):
    """Return the path to the bundled default configuration file."""

    def __call__(self) -> Path: ...


class DeployConfiguration(Protocol):
    """Deploy default configuration to specified target layers."""

    def __call__(
        self,
        *,
        targets: Sequence[DeployTarget],
        force: bool = ...,
        profile: str | None = ...,
        set_permissions: bool | None = ...,
        dir_mode: int | None = ...,
        file_mode: int | None = ...,
        permission_overrides: Mapping[str, object] | None = ...,
    ) -> list[Path]: ...


class DisplayConfig(Protocol):
    """Display the provided configuration in the requested format."""

    def __call__(
        self, config: Config, *, output_format: OutputFormat = ..., section: str | None = ..., profile: str | None = ...
    ) -> None: ...


class InitLogging(Protocol):
    """Initialize lib_log_rich runtime with the provided configuration.

    ``dotenv_path`` is the ``.env`` the configuration was loaded with, or None for the one the
    configuration's provenance names.
    A refused logging setting raises ``InvalidLoggingConfigError`` only after logging has been
    started with its defaults, so the caller never has to start it a second time.
    """

    def __call__(self, config: Config, *, dotenv_path: str | None = None) -> None: ...


class CheckLoggingConfig(Protocol):
    """Refuse ``config``'s logging settings as ``InitLogging`` would, leaving the running runtime alone.

    For a configuration loaded after logging is running; a refused setting raises
    ``InvalidLoggingConfigError`` and logging keeps running with the settings it started with.
    """

    def __call__(self, config: Config) -> None: ...


class PrinterTransport(Protocol):
    """An open byte channel to one printer (raw socket, serial port, device file, file, stdout).

    ``query`` sends a request and reads the answer up to ``terminator``; a one-way channel (a
    file, stdout, most device files) raises :class:`~datamax_dpl.domain.errors.TransportError`
    there, and :attr:`bidirectional` says so in advance.
    """

    @property
    def bidirectional(self) -> bool:
        """True when the channel can read the printer's answers."""
        ...

    def send(self, data: bytes) -> None:
        """Write ``data`` completely, or raise ``TransportError``."""
        ...

    def query(self, request: bytes, *, timeout: float, terminator: bytes = b"\r") -> bytes:
        """Send ``request`` and return the answer including ``terminator``, or raise ``TransportError``."""
        ...

    def close(self) -> None:
        """Release the channel; safe to call twice."""
        ...


class OpenPrinter(Protocol):
    """Open a :class:`PrinterTransport` for a printer URI (``tcp://host:9100``, ``serial:///dev/ttyS0``...)."""

    def __call__(self, uri: str, *, timeout: float) -> PrinterTransport: ...


class CaptureImage(Protocol):
    """Take one photo with the configured camera and store it in ``output_dir``.

    Raises ``CameraError`` when no image could be taken.
    """

    def __call__(self, settings: CameraSettings, *, output_dir: Path) -> CapturedImage: ...


class Sleep(Protocol):
    """Pause for ``seconds``; injected so polling loops run instantly under test."""

    def __call__(self, seconds: float) -> None: ...


class Clock(Protocol):
    """Monotonic seconds, for deadlines."""

    def __call__(self) -> float: ...


__all__ = [
    "CaptureImage",
    "CheckLoggingConfig",
    "Clock",
    "DeployConfiguration",
    "DisplayConfig",
    "GetConfig",
    "GetDefaultConfigPath",
    "InitLogging",
    "OpenPrinter",
    "PrinterTransport",
    "Sleep",
]
