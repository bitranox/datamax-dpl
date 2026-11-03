"""Application ports - callable Protocol definitions for adapter functions.

Each Protocol class defines a ``__call__`` method whose signature exactly
matches the corresponding adapter function.  Existing module-level functions
satisfy these protocols automatically via structural subtyping (PEP 544).
:class:`PrinterEndpoint` bundles the :class:`OpenPrinter` port with the URI
and timeout it is always called with, :class:`DeployPermissionRequest` the
permission values :class:`DeployConfiguration` hands to lib_layered_config.

System Role:
    Sits between domain and adapters.  Infrastructure types (``Config``)
    are imported under ``TYPE_CHECKING`` only so that
    import-linter layer contracts remain satisfied at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from lib_layered_config import Config

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


@dataclass(frozen=True)
class DeployPermissionRequest:
    """What a deploy is asked to do with the modes of the files it writes; None leaves a value to the configuration.

    The four always travel together, from the command line to lib_layered_config's
    ``deploy_config``, which takes each under the same name and reads the configured
    ``[lib_layered_config.default_permissions]`` for whatever is left None.

    Attributes:
        set_permissions: True sets modes, False leaves them to the umask, None follows the
            configured ``enabled`` (on when unset).
        dir_mode: Directory mode for every target, over the configured and built-in ones.
        file_mode: File mode for every target, over the configured and built-in ones.
        permission_overrides: Runtime values for keys of ``[lib_layered_config.default_permissions]``
            by setting name (``{"user_file": "0o640"}``), laid over the configured ones.

    Example:
        >>> DeployPermissionRequest(dir_mode=0o750).set_permissions is None
        True
    """

    set_permissions: bool | None = None
    dir_mode: int | None = None
    file_mode: int | None = None
    permission_overrides: Mapping[str, object] | None = None


#: A deploy that asks nothing of the modes: the configuration decides them all.
CONFIGURED_PERMISSIONS = DeployPermissionRequest()


class DeployConfiguration(Protocol):
    """Deploy default configuration to specified target layers."""

    def __call__(
        self,
        *,
        targets: Sequence[DeployTarget],
        force: bool = ...,
        profile: str | None = ...,
        permissions: DeployPermissionRequest = ...,
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


@dataclass(frozen=True)
class PrinterEndpoint:
    """One printer as the use cases reach it: its URI, the seconds a connect or write may take, the opener.

    The three always travel together: every use case that talks to a printer opens it by this URI
    with this timeout through this opener, and hands all three on to the reads it makes.

    Attributes:
        uri: The printer (``tcp://host:9100``, ``serial:///dev/ttyS0``...).
        timeout: Seconds for each connect and write.
        open_printer: Opens a channel to ``uri``.

    Example:
        >>> from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
        >>> printer = RecordingPrinter()
        >>> opener = open_recording_printer(printer)
        >>> endpoint = PrinterEndpoint(uri="tcp://192.0.2.1:9100", timeout=5.0, open_printer=opener)
        >>> endpoint.open().send(b"job")
        >>> printer.sent
        [b'job']
    """

    uri: str
    timeout: float
    open_printer: OpenPrinter

    def open(self, *, timeout: float | None = None) -> PrinterTransport:
        """Open a channel to :attr:`uri` within ``timeout`` seconds, :attr:`timeout` when None.

        Args:
            timeout: Seconds for this connect, for a caller whose wait leaves less than
                :attr:`timeout`; None takes :attr:`timeout`.

        Returns:
            The open channel, the caller's to close.

        Raises:
            TransportError: The printer could not be reached.
        """
        return self.open_printer(self.uri, timeout=self.timeout if timeout is None else timeout)


class OpenTunablePrinter(Protocol):
    """An :class:`OpenPrinter` that also takes how the transport paces its writes.

    The tuning comes from the printer settings, which only the command reading them knows; the
    composition binds it, so the use cases keep opening printers by URI and timeout alone.
    """

    def __call__(self, uri: str, *, timeout: float, tuning: TransportTuning = ...) -> PrinterTransport: ...


class JobSpool(Protocol):
    """Where a batch's check pass keeps each record as it was rendered, for the send to read back.

    The check pass renders every record once to refuse a bad batch before any of it goes out; kept
    here, the send reads that render instead of making it again, and a label it renders again for
    what the printer answers fills from the values kept with it (:attr:`BatchEntry.record`), so the
    records are never read again. Each record goes out of memory as it is added, and :meth:`entries`
    reads one at a time, so memory does not grow with the records. What grows is the space the spool
    takes: per record its job, its values and its other fields, and of its findings only those the
    report keeps in full, the rest as counts (:attr:`BatchEntry.unkept`).
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
    "CONFIGURED_PERMISSIONS",
    "CheckLoggingConfig",
    "Clock",
    "DeployConfiguration",
    "DeployPermissionRequest",
    "DisplayConfig",
    "GetConfig",
    "GetDefaultConfigPath",
    "InitLogging",
    "JobSpool",
    "OpenJobSpool",
    "OpenPrinter",
    "OpenTunablePrinter",
    "PrinterEndpoint",
    "PrinterTransport",
    "SetPrinterValues",
    "Sleep",
]
