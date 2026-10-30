"""The CLI over the production wiring and a real channel: argv in, the bytes a printer receives out.

The other CLI tests run against the in-memory recording printer, which proves what a command
sends but not that ``--printer`` reaches a socket or a file through ``open_printer``. Here every
command runs with ``build_production()`` against a file or a printer server on 127.0.0.1, so the
URI parsing, the transport and the timeout plumbing are the ones a user gets.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from printer_server import answering, silent

from datamax_dpl import __init__conf__
from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.composition import build_production
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.status import MEMORY_DIRECTORY_REQUESTS, STATUS_REQUEST

if TYPE_CHECKING:
    import socket
    from collections.abc import Callable

    from click.testing import CliRunner, Result
    from printer_server import Play, PrinterServer

LABEL = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "Hello"}]}
#: A model with measured defaults (stop position, scale), so the job carries them.
MODEL = "allegro-2"
#: ``<SOH>A`` answer of a printer with no flag set: eight ``N`` and ``<CR>``.
IDLE_STATUS = b"NNNNNNNN\r"
#: A graphics directory captured from an Allegro 2: module A with its names and free bytes.
GRAPHIC_DIRECTORY = (Path(__file__).parent / "fixtures" / "memory_allegro2_graphic.txt").read_bytes()
#: The free bytes that directory reports for module A ("AVAILABLE BYTES IN MODULE : 00048631").
GRAPHIC_FREE_BYTES = 48631
#: Slack over the waited seconds for starting the CLI, loading the configuration and one connect.
STARTUP_SLACK_SECONDS = 2.0

_ENV_PREFIX = __init__conf__.LAYEREDCONF_SLUG.upper().replace("-", "_")


@pytest.fixture(autouse=True)
def no_ambient_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clear_config_cache: None) -> None:
    """Keep the developer's environment layer and ``.env`` out of the production loader.

    The rootdir conftest already moves the file layers under ``tmp_path``; a ``DATAMAX_DPL___``
    variable or a ``.env`` above the working directory would still set a scale or a stop position
    and change the bytes these tests compare.
    """
    for name in [name for name in os.environ if name.startswith(_ENV_PREFIX)]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


def run(runner: CliRunner, args: list[str]) -> Result:
    return runner.invoke(cli_mod.cli, args, obj=build_production)


def label_file(tmp_path: Path) -> str:
    path = tmp_path / "label.json"
    path.write_text(json.dumps(LABEL), encoding="utf-8")
    return str(path)


def expected_job() -> bytes:
    return build_job(Label.model_validate(LABEL), RenderOptions(model=MODEL)).data


def envelope(result: Result) -> dict[str, Any]:
    return json.loads(result.stdout)


@pytest.mark.os_agnostic
def test_print_to_a_file_writes_exactly_the_rendered_job(cli_runner: CliRunner, tmp_path: Path) -> None:
    output = tmp_path / "out.prn"

    result = run(cli_runner, ["print", label_file(tmp_path), "--model", MODEL, "--printer", f"file:{output}"])

    assert result.exit_code == 0, result.output
    assert output.read_bytes() == expected_job()


@pytest.mark.os_agnostic
def test_print_over_tcp_delivers_exactly_the_rendered_job(
    cli_runner: CliRunner, tmp_path: Path, serve: Callable[[Play], PrinterServer]
) -> None:
    server = serve(silent)

    result = run(cli_runner, ["print", label_file(tmp_path), "--model", MODEL, "--printer", server.address])
    server.join()

    assert result.exit_code == 0, result.output
    assert bytes(server.received) == expected_job()
    assert f"sent {len(expected_job())} bytes to {server.address}" in result.stdout


@pytest.mark.os_agnostic
def test_status_over_tcp_reads_an_idle_printer(cli_runner: CliRunner, serve: Callable[[Play], PrinterServer]) -> None:
    server = serve(answering(STATUS_REQUEST, IDLE_STATUS))

    result = run(cli_runner, ["--json", "status", "--printer", server.address])
    server.join()

    assert result.exit_code == 0, result.output
    document = envelope(result)
    assert (document["ok"], document["command"]) == (True, "status")
    assert (document["data"]["idle"], document["data"]["problems"]) == (True, [])
    assert bytes(server.received) == STATUS_REQUEST


@pytest.mark.os_agnostic
def test_status_skips_a_stale_memory_line_sent_after_the_request(
    cli_runner: CliRunner, serve: Callable[[Play], PrinterServer]
) -> None:
    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, len(STATUS_REQUEST))
        connection.sendall(b"MODULE: A\r")
        connection.sendall(IDLE_STATUS)
        server.read_to_end(connection)

    server = serve(play)

    result = run(cli_runner, ["--json", "status", "--printer", server.address])
    server.join()

    assert result.exit_code == 0, result.output
    assert envelope(result)["data"]["idle"] is True


@pytest.mark.os_agnostic
def test_status_shows_a_truncated_answer_it_skipped(
    cli_runner: CliRunner, serve: Callable[[Play], PrinterServer]
) -> None:
    server = serve(answering(STATUS_REQUEST, b"NNNNNNN\r"))

    result = run(cli_runner, ["--json", "status", "--printer", server.address, "--timeout", "0.5"])
    server.join()

    assert result.exit_code == 2, result.output
    document = envelope(result)
    assert document["error"] == "PrinterTimeoutError"
    assert document["message"].endswith("(8 bytes read; 1 line skipped, last b'NNNNNNN\\r')")


@pytest.mark.os_agnostic
def test_memory_over_tcp_lists_the_module_and_its_free_bytes(
    cli_runner: CliRunner, serve: Callable[[Play], PrinterServer]
) -> None:
    request = MEMORY_DIRECTORY_REQUESTS["graphic"]
    server = serve(answering(request, GRAPHIC_DIRECTORY))

    result = run(cli_runner, ["--json", "memory", "--kind", "graphic", "--idle", "0.3", "--printer", server.address])
    server.join()

    assert result.exit_code == 0, result.output
    (directory,) = envelope(result)["data"]["directories"]
    assert (directory["kind"], directory["parsed"]) == ("graphic", True)
    (module,) = directory["modules"]
    assert (module["module"], module["available_bytes"]) == ("A", GRAPHIC_FREE_BYTES)
    assert bytes(server.received) == request


@pytest.mark.os_agnostic
def test_a_silent_printer_fails_status_within_the_given_timeout(
    cli_runner: CliRunner, serve: Callable[[Play], PrinterServer]
) -> None:
    # The server listens and accepts, so the one connect succeeds at once (no refusal retries,
    # which take about 2 s each on Windows); only the answer is missing.
    server = serve(silent)
    timeout = 0.5

    started = time.monotonic()
    result = run(cli_runner, ["status", "--printer", server.address, "--timeout", str(timeout)])
    elapsed = time.monotonic() - started

    assert result.exit_code == 2, result.output
    assert f"no complete answer within {timeout:g} s" in result.stderr
    assert "the printer may still be printing an earlier job; try again or raise the timeout" in result.stderr
    # The lower margin: on Windows (Python 3.10-3.12) the monotonic clock ticks every 15.6 ms and a
    # socket wait can return a tick early, as the transport tests also allow.
    assert timeout - 0.1 <= elapsed < timeout + STARTUP_SLACK_SECONDS, f"{elapsed=:.2f}"
