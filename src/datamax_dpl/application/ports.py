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
    from collections.abc import Callable, Iterator, Mapping, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from lib_layered_config import Config

    from ..domain.camera import CameraSettings, CapturedImage
    from ..domain.enums import DeployTarget, OutputFormat
    from ..domain.profiles import CalibrationKey
    from ..domain.timing import TransportTuning
    from .use_cases import BatchEntry


class GetConfig(Protocol):
    """Load layered configuration with application defaults."""

    def __call__(
        self, *, profile: str | None = ..., start_dir: str | None = ..., dotenv_path: str | None = ...
    ) -> Config: ...


class SetPrinterValues(Protocol):
    """Write values into a printer's section of the user configuration layer; return the file written.

    ``printer`` names a ``[printers.<name>]`` section, or is "" for ``[printer]``; each key of
    ``values`` is written as the configuration key it names.
    """

    def __call__(self, *, printer: str, values: Mapping[CalibrationKey, float], profile: str | None = ...) -> Path: ...


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

    ``query`` sends a request and reads the answer up to ``terminator``; ``read_answer`` reads an
    answer that has no terminator until the printer falls silent. A one-way channel (a file,
    stdout, most device files) raises :class:`~datamax_dpl.domain.errors.TransportError` in both,
    and :attr:`bidirectional` says so in advance.
    """

    @property
    def bidirectional(self) -> bool:
        """True when the channel can read the printer's answers."""
        ...

    def send(self, data: bytes) -> None:
        """Write ``data`` completely, or raise ``TransportError``."""
        ...

    def query(
        self,
        request: bytes,
        *,
        timeout: float,
        terminator: bytes = b"\r",
        lines: int = 1,
        accept: Callable[[bytes], bool] | None = None,
    ) -> bytes:
        """Send ``request`` and return the answer through the ``lines``-th ``terminator``.

        Most answers are one line; the ``<STX>KQ`` memory report has eight. ``accept``, when given,
        judges each line read (terminator included): a line it refuses is skipped and does not
        count toward ``lines``, so a line a printer still owed an earlier query is never read as
        this answer. Skipped bytes count toward the transport's size bound, so a printer sending
        nothing but refused lines is cut off as one sending an endless answer is.
        """
        ...

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = ...) -> bytes:
        """Send ``request`` and return every byte until ``idle`` seconds pass without a new one.

        For answers with no terminator and no fixed length, such as the ``<STX>W`` module
        directory. ``timeout`` bounds the wait for the first byte (XON/XOFF alone do not count),
        ``total`` the whole answer, which then raises ``PrinterTimeoutError``; an answer longer
        than the transport's size bound is refused rather than read without end.
        """
        ...

    def peer_closed(self) -> bool:
        """True when the far end is known to have closed or reset the channel.

        Asked without waiting and without taking a byte of an answer that is waiting. False when
        the channel is open, or when it cannot tell (a serial line, a file): a close that comes
        after this question, or after the next write, is not seen. Over TCP (measured on Linux
        loopback) a byte still waiting ahead of a close hides that close, so the rest goes to a
        closed connection, and a far end that only stops writing (a half close) reads as closed.
        """
        ...

    def close(self) -> None:
        """Release the channel; safe to call twice."""
        ...


class OpenPrinter(Protocol):
    """Open a :class:`PrinterTransport` for a printer URI (``tcp://host:9100``, ``serial:///dev/ttyS0``...)."""

    def __call__(self, uri: str, *, timeout: float) -> PrinterTransport: ...


class OpenTunablePrinter(Protocol):
    """An :class:`OpenPrinter` that also takes how the transport paces its writes.

    The tuning comes from the printer settings, which only the command reading them knows; the
    composition binds it, so the use cases keep opening printers by URI and timeout alone.
    """

    def __call__(self, uri: str, *, timeout: float, tuning: TransportTuning = ...) -> PrinterTransport: ...


class CaptureImage(Protocol):
    """Take one photo with the configured camera and store it in ``output_dir``.

    Raises ``CameraError`` when no image could be taken.
    """

    def __call__(self, settings: CameraSettings, *, output_dir: Path) -> CapturedImage: ...


class JobSpool(Protocol):
    """Where a batch's check pass keeps each record as it was rendered, for the send to read back.

    The check pass renders every record once to refuse a bad batch before any of it goes out; kept
    here, the send reads that render instead of making it again. Each record goes out of memory as
    it is added, and :meth:`entries` reads one at a time, so memory does not grow with the records:
    what grows is the space the spool takes, about the size of the job.
    """

    def add(self, entry: BatchEntry) -> None:
        """Keep ``entry``, the next record as the check pass rendered it."""
        ...

    def seal(self) -> None:
        """End the adding: every record is in, and :meth:`entries` may read them."""
        ...

    def entries(self) -> Iterator[BatchEntry]:
        """Read the records back from the first, one at a time; each call starts over."""
        ...


class OpenJobSpool(Protocol):
    """Make an empty :class:`JobSpool` for one batch; leaving its ``with`` block removes it."""

    def __call__(self) -> AbstractContextManager[JobSpool]: ...


class Sleep(Protocol):
    """Pause for ``seconds``; injected so polling loops run instantly under test."""

    def __call__(self, seconds: float, /) -> None: ...


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
    "JobSpool",
    "OpenJobSpool",
    "OpenPrinter",
    "OpenTunablePrinter",
    "PrinterTransport",
    "SetPrinterValues",
    "Sleep",
]
