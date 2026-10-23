"""Printer URI parsing: every accepted form, and refusals that name the accepted forms."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from datamax_dpl.adapters.transport.uri import (
    ACCEPTED_FORMS,
    MAX_TIMEOUT_SECONDS,
    DeviceUri,
    FileUri,
    Parity,
    SerialUri,
    StdoutUri,
    TcpUri,
    check_timeout,
    parse_printer_uri,
    transport_error,
    unsupported_query,
)
from datamax_dpl.domain.errors import PrinterTimeoutError, TransportError, UnsupportedOperationError

# --- tcp ---------------------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("uri", "host", "port"),
    [
        ("tcp://printer.local:9100", "printer.local", 9100),
        ("tcp://10.0.0.5", "10.0.0.5", 9100),
        ("tcp://10.0.0.5/", "10.0.0.5", 9100),
        ("tcp://label-01:6101", "label-01", 6101),
        ("tcp://[::1]:9101", "::1", 9101),
        ("tcp://[fe80::1%eth0]", "fe80::1%eth0", 9100),
        ("tcp://h:65535", "h", 65535),
        ("tcp://h:1", "h", 1),
    ],
)
def test_a_tcp_uri_gives_host_and_port(uri: str, host: str, port: int) -> None:
    assert parse_printer_uri(uri) == TcpUri(host=host, port=port)


@pytest.mark.os_agnostic
def test_a_tcp_target_brackets_an_ipv6_host() -> None:
    assert parse_printer_uri("tcp://[::1]:9100").target == "[::1]:9100"
    assert parse_printer_uri("tcp://host:9100").target == "host:9100"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "uri",
    [
        "tcp://",
        "tcp://:9100",
        "tcp://h:",
        "tcp://h:0",
        "tcp://h:65536",
        "tcp://h:abc",
        "tcp://h:-1",
        "tcp://::1:9100",  # IPv6 without brackets
        "tcp://[::1",
        "tcp://[not-ipv6]:9100",
        "tcp://user@h:9100",
        "tcp://h:9100/path",
        "tcp://h:9100?x=1",
        "tcp://h:9100#frag",
        "tcp://bad_host:9100",
        "tcp://-h:9100",
        "tcp://h" + chr(0x00E4) + ":9100",
    ],
)
def test_a_malformed_tcp_uri_is_refused_with_the_accepted_forms(uri: str) -> None:
    with pytest.raises(ValueError, match="accepted forms") as caught:
        parse_printer_uri(uri)
    assert ACCEPTED_FORMS in str(caught.value)


# --- serial ------------------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_a_bare_serial_uri_takes_the_manual_defaults() -> None:
    assert parse_printer_uri("serial:///dev/ttyS0") == SerialUri(
        path="/dev/ttyS0", baud=9600, parity=Parity.NONE, bytesize=8, stopbits=1, rtscts=False, xonxoff=False
    )


@pytest.mark.os_agnostic
def test_every_serial_setting_is_read() -> None:
    uri = "serial:///dev/ttyUSB0?baud=19200&parity=e&bytesize=7&stopbits=2&rtscts=1&xonxoff=1"
    assert parse_printer_uri(uri) == SerialUri(
        path="/dev/ttyUSB0", baud=19200, parity=Parity.EVEN, bytesize=7, stopbits=2, rtscts=True, xonxoff=True
    )


@pytest.mark.os_agnostic
def test_a_windows_serial_uri_names_the_port() -> None:
    parsed = parse_printer_uri("serial://COM3?baud=38400&parity=O")
    assert parsed == SerialUri(path="COM3", baud=38400, parity=Parity.ODD)
    assert parsed.target == "COM3"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "uri",
    [
        "serial://",
        "serial:///dev/ttyS0?baud=0",
        "serial:///dev/ttyS0?baud=fast",
        "serial:///dev/ttyS0?baud=-9600",
        "serial:///dev/ttyS0?baud=99999999",
        "serial:///dev/ttyS0?baud=" + chr(0x0669) * 4,
        "serial:///dev/ttyS0?parity=X",
        "serial:///dev/ttyS0?bytesize=6",
        "serial:///dev/ttyS0?stopbits=1.5",
        "serial:///dev/ttyS0?xonxoff=yes",
        "serial:///dev/ttyS0?rtscts=2",
        "serial:///dev/ttyS0?speed=9600",
        "serial:///dev/ttyS0?baud=9600&baud=19200",
        "serial:///dev/ttyS0?baud",
        "serial:///dev/ttyS0#x",
        "serial://dev/ttyS0",  # a name and a path at once
    ],
)
def test_a_malformed_serial_uri_is_refused_with_the_accepted_forms(uri: str) -> None:
    with pytest.raises(ValueError, match="accepted forms"):
        parse_printer_uri(uri)


# --- device, file, stdout ----------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_a_device_uri_gives_its_path() -> None:
    assert parse_printer_uri("device:///dev/usb/lp0") == DeviceUri(path="/dev/usb/lp0")
    assert parse_printer_uri("device://LPT1") == DeviceUri(path="LPT1")


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("uri", "path"),
    [
        ("file:out.dpl", "out.dpl"),
        ("file:/tmp/job.dpl", "/tmp/job.dpl"),
        ("file:with%20percent", "with%20percent"),  # taken literally
        ("file:///abs/job.dpl", "/abs/job.dpl"),
        ("file:///abs/my%20job.dpl", "/abs/my job.dpl"),
        ("file:///C:/labels/job.dpl", "C:/labels/job.dpl"),
    ],
)
def test_a_file_uri_gives_its_path(uri: str, path: str) -> None:
    assert parse_printer_uri(uri) == FileUri(path=path)


@pytest.mark.os_agnostic
def test_a_dash_is_stdout() -> None:
    assert parse_printer_uri("-") == StdoutUri()


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "uri",
    [
        "",
        "--",
        "printer",
        "http://h/",
        "lpd://h/queue",
        "TCP://h:9100",
        " tcp://h:9100",
        "tcp://h:9100\n",
        "device://",
        "device:///dev/lp0#x",
        "file:",
        "file://",
        "file://host/share/job.dpl",
        "file:///abs?x=1",
        "file:a\x00b",
    ],
)
def test_an_unknown_or_empty_uri_is_refused_with_the_accepted_forms(uri: str) -> None:
    with pytest.raises(ValueError, match="accepted forms"):
        parse_printer_uri(uri)


@pytest.mark.os_agnostic
@given(text=st.text(max_size=60))
def test_any_text_parses_or_is_refused_with_a_value_error(text: str) -> None:
    try:
        parsed = parse_printer_uri(text)
    except ValueError as exc:
        assert "accepted forms" in str(exc)
    else:
        assert parsed.kind in {"tcp", "serial", "device", "file", "stdout"}


# --- shared helpers ----------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize("timeout", [0.001, 1.0, MAX_TIMEOUT_SECONDS])
def test_a_usable_timeout_passes(timeout: float) -> None:
    assert check_timeout(timeout) == timeout


@pytest.mark.os_agnostic
@pytest.mark.parametrize("timeout", [0.0, -1.0, math.inf, math.nan, MAX_TIMEOUT_SECONDS + 1])
def test_an_unusable_timeout_is_refused(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        check_timeout(timeout)


@pytest.mark.os_agnostic
def test_a_wrapped_os_error_names_kind_target_and_reason() -> None:
    error = transport_error(
        TcpUri(host="h", port=9100), action="connect to", error=ConnectionRefusedError(111, "refused")
    )
    assert type(error) is TransportError
    assert str(error) == "cannot connect to tcp h:9100: refused"


@pytest.mark.os_agnostic
def test_a_wrapped_timeout_becomes_a_printer_timeout() -> None:
    error = transport_error(DeviceUri(path="/dev/lp0"), action="write to", error=TimeoutError())
    assert isinstance(error, PrinterTimeoutError)
    assert str(error) == "cannot write to device /dev/lp0: TimeoutError"


@pytest.mark.os_agnostic
def test_a_one_way_refusal_names_the_channel() -> None:
    error = unsupported_query(FileUri(path="job.dpl"))
    assert isinstance(error, UnsupportedOperationError)
    assert "file job.dpl" in str(error)
