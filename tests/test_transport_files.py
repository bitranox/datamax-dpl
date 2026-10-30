"""One-way transports: device file, file and stdout, against tmp_path files and byte streams."""

from __future__ import annotations

import io
import sys
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.transport import open_printer
from datamax_dpl.adapters.transport.device import DeviceTransport
from datamax_dpl.adapters.transport.file import FileTransport, StdoutTransport
from datamax_dpl.adapters.transport.uri import DeviceUri, FileUri
from datamax_dpl.domain.errors import ConfigurationError, TransportError, UnsupportedOperationError
from datamax_dpl.domain.status import is_status_line

if TYPE_CHECKING:
    from pathlib import Path

PAYLOAD = bytes(range(256)) + b"\x02L\r\n" + b"\r\n" * 3


# --- device ------------------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_a_device_receives_the_bytes_unchanged(tmp_path: Path) -> None:
    device = tmp_path / "lp0"
    device.write_bytes(b"")
    with DeviceTransport.open(DeviceUri(path=str(device))) as printer:
        assert not printer.bidirectional
        printer.send(PAYLOAD)
        printer.send(b"tail")
    assert device.read_bytes() == PAYLOAD + b"tail"


@pytest.mark.os_agnostic
def test_a_missing_device_is_refused_and_not_created(tmp_path: Path) -> None:
    device = tmp_path / "lp9"
    with pytest.raises(TransportError, match=r"cannot open device .*lp9"):
        DeviceTransport.open(DeviceUri(path=str(device)))
    assert not device.exists()


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="a device URI path is a POSIX path here")
def test_open_printer_opens_a_device_uri(tmp_path: Path) -> None:
    device = tmp_path / "lp0"
    device.write_bytes(b"")
    printer = open_printer(f"device://{device}", timeout=1.0)
    try:
        printer.send(b"\x02L\r")
    finally:
        printer.close()
    assert device.read_bytes() == b"\x02L\r"


@pytest.mark.os_agnostic
def test_a_device_cannot_answer_a_query(tmp_path: Path) -> None:
    device = tmp_path / "lp0"
    device.write_bytes(b"")
    with (
        DeviceTransport.open(DeviceUri(path=str(device))) as printer,
        pytest.raises(UnsupportedOperationError, match="is one-way: it cannot read the printer's answer"),
    ):
        printer.query(b"\x01A", timeout=1.0, accept=is_status_line)


@pytest.mark.os_agnostic
def test_a_closed_device_refuses_and_closes_twice(tmp_path: Path) -> None:
    device = tmp_path / "lp0"
    device.write_bytes(b"")
    printer = DeviceTransport.open(DeviceUri(path=str(device)))
    printer.close()
    printer.close()
    with pytest.raises(TransportError, match="the transport is closed"):
        printer.send(b"x")


# --- file --------------------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_a_file_is_created_and_receives_the_bytes_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "job.dpl"
    printer = open_printer(f"file:{target}", timeout=1.0)
    try:
        printer.send(PAYLOAD)
    finally:
        printer.close()
    assert target.read_bytes() == PAYLOAD


@pytest.mark.os_agnostic
def test_a_file_is_appended_to_and_never_truncated(tmp_path: Path) -> None:
    target = tmp_path / "job.dpl"
    target.write_bytes(b"earlier job\n")
    with FileTransport.open(FileUri(path=str(target))) as printer:
        printer.send(b"next job\r\n")
    assert target.read_bytes() == b"earlier job\nnext job\r\n"


@pytest.mark.os_agnostic
def test_a_file_url_reaches_the_same_file(tmp_path: Path) -> None:
    target = tmp_path / "my job.dpl"
    printer = open_printer(target.as_uri(), timeout=1.0)
    try:
        printer.send(b"x")
    finally:
        printer.close()
    assert target.read_bytes() == b"x"


@pytest.mark.os_agnostic
def test_a_file_in_a_missing_directory_is_a_transport_error(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "job.dpl"
    with pytest.raises(TransportError, match="cannot open file"):
        open_printer(f"file:{target}", timeout=1.0)


@pytest.mark.os_agnostic
def test_a_directory_as_file_target_is_a_transport_error(tmp_path: Path) -> None:
    with pytest.raises(TransportError, match="cannot open file"):
        open_printer(f"file:{tmp_path}", timeout=1.0)


@pytest.mark.os_agnostic
def test_a_file_cannot_answer_a_query(tmp_path: Path) -> None:
    with (
        FileTransport.open(FileUri(path=str(tmp_path / "job.dpl"))) as printer,
        pytest.raises(UnsupportedOperationError, match=r"^file .* is one-way"),
    ):
        printer.query(b"\x01A", timeout=1.0)


# --- stdout ------------------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_stdout_writes_the_bytes_unchanged_to_its_stream() -> None:
    stream = io.BytesIO()
    with StdoutTransport(stream) as printer:
        assert not printer.bidirectional
        printer.send(PAYLOAD)
    assert stream.getvalue() == PAYLOAD
    assert not stream.closed


@pytest.mark.os_agnostic
def test_open_printer_dash_writes_to_the_real_stdout(capfdbinary: pytest.CaptureFixture[bytes]) -> None:
    printer = open_printer("-", timeout=1.0)
    try:
        printer.send(PAYLOAD)
    finally:
        printer.close()
    assert capfdbinary.readouterr().out == PAYLOAD


@pytest.mark.os_agnostic
def test_stdout_cannot_answer_a_query() -> None:
    with (
        StdoutTransport(io.BytesIO()) as printer,
        pytest.raises(UnsupportedOperationError, match=r"^stdout is one-way"),
    ):
        printer.query(b"\x01A", timeout=1.0, accept=is_status_line)


@pytest.mark.os_agnostic
def test_no_one_way_channel_can_read_an_answer_ended_by_silence(tmp_path: Path) -> None:
    device = tmp_path / "lp0"
    device.write_bytes(b"")
    channels = (
        DeviceTransport.open(DeviceUri(path=str(device))),
        FileTransport.open(FileUri(path=str(tmp_path / "job.dpl"))),
        StdoutTransport(io.BytesIO()),
    )
    for printer in channels:
        with printer, pytest.raises(UnsupportedOperationError, match="is one-way: it cannot read the printer's answer"):
            printer.read_answer(b"\x02WG", timeout=1.0, idle=0.5)
    assert (tmp_path / "job.dpl").read_bytes() == b""


@pytest.mark.os_agnostic
def test_writing_to_a_closed_stdout_stream_is_a_transport_error() -> None:
    stream = io.BytesIO()
    printer = StdoutTransport(stream)
    stream.close()
    with pytest.raises(TransportError, match="cannot write to stdout"):
        printer.send(b"x")
    printer.close()


class _BrokenPipe(io.RawIOBase):
    def writable(self) -> bool:
        return True

    def write(self, b: object) -> int:
        raise BrokenPipeError(32, "Broken pipe")


@pytest.mark.os_agnostic
def test_a_reader_that_went_away_is_a_transport_error() -> None:
    printer = StdoutTransport(io.BufferedWriter(_BrokenPipe(), buffer_size=1))
    with pytest.raises(TransportError, match="cannot write to stdout: Broken pipe"):
        printer.send(b"xx")


# --- open_printer ------------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_a_bad_uri_is_a_configuration_error_naming_the_accepted_forms() -> None:
    with pytest.raises(ConfigurationError, match="accepted forms"):
        open_printer("lpd://queue", timeout=1.0)


@pytest.mark.os_agnostic
def test_a_bad_timeout_is_refused_before_anything_opens(tmp_path: Path) -> None:
    target = tmp_path / "job.dpl"
    with pytest.raises(ValueError, match="timeout must be above 0"):
        open_printer(f"file:{target}", timeout=0.0)
    assert not target.exists()
