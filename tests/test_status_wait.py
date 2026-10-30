"""``status`` and ``memory`` keep trying for the printer's first answer within ``[printer] status_wait``.

A print server still feeding an earlier job refuses connections for a while, or takes one and
stays silent until the printer is free. Each test runs the CLI over the production wiring against a
printer server on 127.0.0.1 that plays one of those cases, and checks what every connection carried:
a request is sent again only on a new connection after the last one was refused or hung up with no
answer, never while one is still outstanding.
"""

from __future__ import annotations

import json
import re
import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from printer_server import LINGER_ZERO, answering, hanging_up, resetting, silent

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.composition import build_production
from datamax_dpl.domain.status import MEMORY_DIRECTORY_REQUESTS, STATUS_REQUEST

if TYPE_CHECKING:
    from click.testing import CliRunner, Result
    from printer_server import Play, PrinterServer, Serve

#: ``<SOH>A`` answer of a printer with no flag set.
IDLE_STATUS = b"NNNNNNNN\r"
#: A graphics directory captured from an Allegro 2.
GRAPHIC_DIRECTORY = (Path(__file__).parent / "fixtures" / "memory_allegro2_graphic.txt").read_bytes()
#: Slack over the waited seconds for starting the CLI, loading the configuration and one connect.
STARTUP_SLACK_SECONDS = 2.0
#: Seconds a fake server waits for a connection that must NOT come: a client that asks again does
#: so within its retry interval (0.25 s), so this ends the server thread soon either way.
NOT_AGAIN_SECONDS = 1.0
#: The seconds and attempts a give-up message reports.
GAVE_UP = re.compile(r": no answer in (?P<seconds>[0-9.]+) s \((?P<attempts>[0-9]+) attempts?\);")

pytestmark = pytest.mark.usefixtures("no_ambient_configuration")


def run(runner: CliRunner, args: list[str]) -> Result:
    return runner.invoke(cli_mod.cli, args, obj=build_production)


def envelope(result: Result) -> dict[str, Any]:
    return json.loads(result.stdout)


def gave_up(message: str) -> tuple[float, int]:
    """The seconds waited and the attempts a give-up message reports."""
    found = GAVE_UP.search(message)
    assert found is not None, message
    return float(found["seconds"]), int(found["attempts"])


def late(reply: bytes, *, after: float) -> Play:
    """A printer that takes the status request, says nothing for ``after`` seconds, then answers."""

    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, len(STATUS_REQUEST))
        time.sleep(after)
        connection.sendall(reply)
        server.read_to_end(connection)

    return play


@pytest.mark.os_agnostic
def test_status_keeps_reading_one_silent_connection_past_the_timeout_and_asks_once(
    cli_runner: CliRunner, serve: Serve
) -> None:
    server = serve(late(IDLE_STATUS, after=1.0))

    result = run(
        cli_runner,
        [
            "--json",
            "--set",
            "printer.timeout=0.3",
            "--set",
            "printer.status_wait=5",
            "status",
            "--printer",
            server.address,
        ],
    )
    server.join()

    assert result.exit_code == 0, result.output
    assert envelope(result)["data"]["idle"] is True
    assert server.connections == [bytearray(STATUS_REQUEST)]


@pytest.mark.os_agnostic
def test_status_asks_again_on_a_new_connection_after_one_closed_unanswered(cli_runner: CliRunner, serve: Serve) -> None:
    server = serve(hanging_up(STATUS_REQUEST), answering(STATUS_REQUEST, IDLE_STATUS))

    result = run(cli_runner, ["--json", "status", "--printer", server.address])
    server.join()

    assert result.exit_code == 0, result.output
    assert envelope(result)["data"]["idle"] is True
    assert server.connections == [bytearray(STATUS_REQUEST), bytearray(STATUS_REQUEST)]


def then_hanging_up(sent: bytes, *, reset: bool = False) -> Play:
    """A print server that takes the status request, sends ``sent``, then closes (or resets) the connection."""

    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, len(STATUS_REQUEST))
        if sent:
            connection.sendall(sent)
        if reset:
            # Linger on, zero seconds: the close that follows sends RST instead of FIN.
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, LINGER_ZERO)

    return play


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("sent", "reset"), [(b"", True), (b"\x11", False)], ids=["reset-before-a-byte", "closed-after-xon-only"]
)
def test_status_asks_again_after_a_reset_or_a_hang_up_that_brought_only_flow_control(
    cli_runner: CliRunner, serve: Serve, sent: bytes, reset: bool
) -> None:
    server = serve(then_hanging_up(sent, reset=reset), answering(STATUS_REQUEST, IDLE_STATUS))

    result = run(cli_runner, ["--json", "status", "--printer", server.address])
    server.join()

    assert result.exit_code == 0, result.output
    assert envelope(result)["data"]["idle"] is True
    assert server.connections == [bytearray(STATUS_REQUEST), bytearray(STATUS_REQUEST)]


@pytest.mark.os_agnostic
def test_status_does_not_ask_again_after_a_hang_up_that_followed_a_skipped_line(
    cli_runner: CliRunner, serve: Serve
) -> None:
    """A line came first, so the print server is passing on answers: this request may still be owed one."""
    server = serve(
        then_hanging_up(b"MODULE: A\r"),
        answering(STATUS_REQUEST, IDLE_STATUS),
        next_connection_within=NOT_AGAIN_SECONDS,
    )

    result = run(cli_runner, ["--json", "status", "--printer", server.address])
    server.join()

    assert result.exit_code == 2, result.output
    document = envelope(result)
    assert (document["error"], document["message"]) == (
        "TransportError",
        f"tcp 127.0.0.1:{server.port}: the printer closed the connection (10 bytes read)",
    )
    assert server.connections == [bytearray(STATUS_REQUEST)]


@pytest.mark.os_agnostic
def test_status_asks_again_after_a_print_server_reset_the_connection_on_accept(
    cli_runner: CliRunner, serve: Serve
) -> None:
    """The reset reaches the client while it drops stale bytes, writes the request or reads: all three retry."""
    server = serve(resetting, answering(STATUS_REQUEST, IDLE_STATUS))

    result = run(cli_runner, ["--json", "status", "--printer", server.address])
    server.join()

    assert result.exit_code == 0, result.output
    assert envelope(result)["data"]["idle"] is True
    assert server.connections == [bytearray(), bytearray(STATUS_REQUEST)]


@pytest.mark.os_agnostic
def test_status_keeps_connecting_while_a_print_server_refuses(cli_runner: CliRunner, serve: Serve) -> None:
    """Refused for longer than one connect's own refusal retries, which ``printer.timeout=1`` caps at 1 s."""
    server = serve(answering(STATUS_REQUEST, IDLE_STATUS), refuse_for=1.5)

    result = run(
        cli_runner,
        [
            "--json",
            "--set",
            "printer.timeout=1",
            "--set",
            "printer.status_wait=15",
            "status",
            "--printer",
            server.address,
        ],
    )
    server.join()

    assert result.exit_code == 0, result.output
    assert envelope(result)["data"]["idle"] is True
    assert server.connections == [bytearray(STATUS_REQUEST)]


@pytest.mark.os_agnostic
def test_status_connects_again_after_a_connect_timed_out(cli_runner: CliRunner, serve: Serve) -> None:
    """A full accept queue drops the SYN on Linux and macOS, so the connect times out (Windows refuses)."""
    server = serve(answering(STATUS_REQUEST, IDLE_STATUS), queue_full_for=1.5)

    result = run(
        cli_runner,
        [
            "--json",
            "--set",
            "printer.timeout=0.5",
            "--set",
            "printer.status_wait=15",
            "status",
            "--printer",
            server.address,
        ],
    )
    server.join()

    assert result.exit_code == 0, result.output
    assert envelope(result)["data"]["idle"] is True
    assert server.connections == [bytearray(STATUS_REQUEST)]


@pytest.mark.os_agnostic
def test_a_give_up_reports_the_seconds_waited_not_the_wait_asked_for(cli_runner: CliRunner, serve: Serve) -> None:
    """With 3 s asked for and a 2 s pause, the second hang-up leaves no time for a third attempt: about 2 s waited.

    Sized for a loaded runner: the first hang-up has about 1 s to come back before the pause no
    longer fits, and the server waits 3 s for the second connection, which comes after about 2 s.
    """
    wait, interval = 3.0, 2.0
    hang_up = hanging_up(STATUS_REQUEST)
    server = serve(hang_up, hang_up, next_connection_within=wait)

    started = time.monotonic()
    result = run(
        cli_runner,
        [
            "--json",
            "--set",
            f"printer.status_wait={wait:g}",
            "--set",
            f"printer.status_retry_interval={interval:g}",
            "status",
            "--printer",
            server.address,
        ],
    )
    elapsed = time.monotonic() - started
    server.join()

    assert result.exit_code == 2, result.output
    message = envelope(result)["message"]
    seconds, attempts = gave_up(message)
    assert attempts == 2
    # At least the pause (less a coarse Windows clock tick), at most what passed, and short of the wait.
    assert interval - 0.1 <= seconds <= elapsed + 0.05, f"{seconds=} {elapsed=:.2f}"
    assert seconds < wait, f"{seconds=}"
    assert message.endswith(
        f"Last failure: tcp 127.0.0.1:{server.port}: the printer closed the connection (0 bytes read)"
    )


@pytest.mark.os_agnostic
def test_status_waits_the_retry_interval_between_two_connections(cli_runner: CliRunner, serve: Serve) -> None:
    hang_up = hanging_up(STATUS_REQUEST)
    server = serve(hang_up, hang_up, answering(STATUS_REQUEST, IDLE_STATUS))
    interval = 0.6

    started = time.monotonic()
    result = run(
        cli_runner,
        ["--json", "--set", f"printer.status_retry_interval={interval}", "status", "--printer", server.address],
    )
    elapsed = time.monotonic() - started
    server.join()

    assert result.exit_code == 0, result.output
    assert len(server.connections) == 3
    # Two pauses between three connections; the lower margin allows a coarse Windows clock tick.
    assert elapsed >= 2 * interval - 0.1, f"{elapsed=:.2f}"


@pytest.mark.os_agnostic
def test_a_silent_printer_fails_status_after_the_configured_wait_naming_how_to_wait_longer(
    cli_runner: CliRunner, serve: Serve
) -> None:
    server = serve(silent)
    wait = 1.0

    started = time.monotonic()
    result = run(
        cli_runner, ["--json", "--set", f"printer.status_wait={wait:g}", "status", "--printer", server.address]
    )
    elapsed = time.monotonic() - started
    server.join()

    assert result.exit_code == 2, result.output
    document = envelope(result)
    assert document["error"] == "PrinterTimeoutError"
    message = document["message"]
    assert message.startswith(server.address)
    seconds, attempts = gave_up(message)
    assert attempts == 1
    assert wait - 0.1 <= seconds <= elapsed + 0.05, f"{seconds=} {elapsed=:.2f}"
    assert "the printer or print server may still be busy with an earlier job" in message
    assert "wait longer with [printer] status_wait or --timeout" in message
    # The transport's own advice ("raise the timeout") would point at a key that no longer
    # lengthens this wait, so the last failure is quoted without it.
    assert "raise the timeout" not in message
    assert server.connections == [bytearray(STATUS_REQUEST)]
    assert wait - 0.1 <= elapsed < wait + STARTUP_SLACK_SECONDS, f"{elapsed=:.2f}"


@pytest.mark.os_agnostic
def test_the_timeout_option_is_the_whole_wait_instead_of_status_wait(cli_runner: CliRunner, serve: Serve) -> None:
    server = serve(silent)
    timeout = 0.5

    started = time.monotonic()
    result = run(
        cli_runner,
        ["--json", "--set", "printer.status_wait=30", "status", "--printer", server.address, "--timeout", str(timeout)],
    )
    elapsed = time.monotonic() - started
    server.join()

    assert result.exit_code == 2, result.output
    seconds, _ = gave_up(envelope(result)["message"])
    assert timeout - 0.1 <= seconds <= elapsed + 0.05, f"{seconds=} {elapsed=:.2f}"
    assert timeout - 0.1 <= elapsed < timeout + STARTUP_SLACK_SECONDS, f"{elapsed=:.2f}"


@pytest.mark.os_agnostic
def test_a_malformed_status_answer_is_reported_and_not_asked_again(cli_runner: CliRunner, serve: Serve) -> None:
    # Eight flags whose reserved eighth is Y: a status line, but not one the manual describes.
    server = serve(
        answering(STATUS_REQUEST, b"NNNNNNNY\r"),
        answering(STATUS_REQUEST, IDLE_STATUS),
        next_connection_within=NOT_AGAIN_SECONDS,
    )

    result = run(cli_runner, ["--json", "status", "--printer", server.address])
    server.join()

    assert result.exit_code == 2, result.output
    assert envelope(result)["error"] == "PrinterProtocolError"
    assert server.connections == [bytearray(STATUS_REQUEST)]


@pytest.mark.os_agnostic
def test_memory_asks_again_on_a_new_connection_after_one_closed_unanswered(cli_runner: CliRunner, serve: Serve) -> None:
    request = MEMORY_DIRECTORY_REQUESTS["graphic"]
    server = serve(hanging_up(request), answering(request, GRAPHIC_DIRECTORY))

    result = run(cli_runner, ["--json", "memory", "--kind", "graphic", "--idle", "0.3", "--printer", server.address])
    server.join()

    assert result.exit_code == 0, result.output
    (directory,) = envelope(result)["data"]["directories"]
    assert (directory["kind"], directory["parsed"]) == ("graphic", True)
    assert server.connections == [bytearray(request), bytearray(request)]
