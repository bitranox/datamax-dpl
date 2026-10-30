"""scripts/status_cycles.py prints, asks for status and probes the port, logging one JSON row per event.

The datamax-dpl commands run as real subprocesses (``python -m datamax_dpl``) against a server on
127.0.0.1 that takes several connections one after another and then stops listening, the way a
wedged print server port refuses.
"""

from __future__ import annotations

import itertools
import json
import os
import runpy
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from printer_server import ConnectionsServer

from datamax_dpl.domain.label import Label
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.status import STATUS_REQUEST

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "status_cycles.py"
_SRC = str(Path(__file__).resolve().parent.parent / "src")
_LABEL = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "Hello"}]}
_MODEL = "allegro-2"
#: ``<SOH>A`` answer of a printer with no flag set.
_IDLE_STATUS = b"NNNNNNNN\r"
_FIELDS = ["t", "elapsed", "cycle", "step", "rc", "bytes", "answer", "error"]
#: Short enough for a test, long enough that a busy runner keeps the order of the connects.
_INTERVAL = 0.2
#: Seconds of failing connects the wedged-port test stops at. Below one Windows refusal (about
#: 2 s, where nothing is bound), so there the first refused connect already ends the run, while a
#: platform that refuses at once probes several times: the assertions hold for both.
_GIVE_UP = 0.5

pytestmark = pytest.mark.usefixtures("no_ambient_configuration", "package_from_this_tree")


def _names() -> dict[str, Any]:
    """The script's module namespace, loaded from its file the way a maintainer runs it."""
    return runpy.run_path(str(_SCRIPT), run_name="status_cycles")


def _main() -> Callable[[list[str]], int]:
    main: Callable[[list[str]], int] = _names()["main"]
    return main


@pytest.fixture
def package_from_this_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the ``python -m datamax_dpl`` subprocesses import the package from this tree's ``src``.

    The configuration they read is kept clean by ``no_ambient_configuration``, which they inherit
    through ``os.environ`` and the working directory.
    """
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", f"{_SRC}{os.pathsep}{existing}" if existing else _SRC)


@pytest.fixture
def connections() -> Iterator[Callable[[int], ConnectionsServer]]:
    """Start multi-connection printer servers; every one is joined at teardown."""
    servers: list[ConnectionsServer] = []

    def start(limit: int) -> ConnectionsServer:
        server = ConnectionsServer(limit=limit, status_reply=_IDLE_STATUS)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.join()
    assert all(server.ended for server in servers)


def _label(tmp_path: Path) -> Path:
    path = tmp_path / "label.json"
    path.write_text(json.dumps(_LABEL), encoding="utf-8")
    return path


def _log_rows(path: Path) -> list[dict[str, Any]]:
    return [cast("dict[str, Any]", json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]


def _args(server: ConnectionsServer, tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--printer",
        server.address,
        "--label",
        str(_label(tmp_path)),
        "--log",
        str(tmp_path / "cycles.jsonl"),
        "--model",
        _MODEL,
        "--interval",
        str(_INTERVAL),
        *extra,
    ]


@pytest.mark.os_agnostic
def test_a_cycle_prints_asks_status_three_times_and_connects_bare(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], connections: Callable[[int], ConnectionsServer]
) -> None:
    server = connections(1 + 3 + 3)

    code = _main()(_args(server, tmp_path, "--cycles", "1", "--connects", "3"))
    server.join()

    assert code == 0, capsys.readouterr()
    rows = _log_rows(tmp_path / "cycles.jsonl")
    assert [row["step"] for row in rows] == ["print", "status", "status", "status", "connect", "connect", "connect"]
    assert all(list(row) == _FIELDS and row["rc"] == 0 and row["error"] is None for row in rows)
    job = build_job(Label.model_validate(_LABEL), RenderOptions(model=_MODEL)).data
    assert server.connections == [job, STATUS_REQUEST, STATUS_REQUEST, STATUS_REQUEST, b"", b"", b""]
    assert rows[0]["bytes"] == len(job)
    assert all(row["answer"]["idle"] is True for row in rows[1:4])
    connects = [row["elapsed"] for row in rows[4:]]
    assert all(later - earlier >= _INTERVAL * 0.9 for earlier, later in itertools.pairwise(connects))
    assert capsys.readouterr().out.splitlines() == (tmp_path / "cycles.jsonl").read_text(encoding="utf-8").splitlines()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("accepted_connects", [0, 1])
def test_a_port_that_stops_accepting_ends_the_run_and_says_so(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    connections: Callable[[int], ConnectionsServer],
    accepted_connects: int,
) -> None:
    """The server takes the print, the three status questions and ``accepted_connects``, then stops listening.

    A connect made just before the listener closes can still be taken from its backlog, so the
    accepted connects are a floor; what follows the first refused connect must all be refused. The
    run stops in cycle 1, only after the port has failed for ``--give-up`` seconds counted from the
    START of the first refused connect (a Windows refusal alone takes about 2 s).
    """
    server = connections(1 + 3 + accepted_connects)

    code = _main()(_args(server, tmp_path, "--cycles", "2", "--connects", "3", "--give-up", str(_GIVE_UP)))
    server.join()

    assert code == 3
    rows = _log_rows(tmp_path / "cycles.jsonl")
    assert [row["step"] for row in rows[:4]] == ["print", "status", "status", "status"]
    stop = rows[-1]
    assert (stop["step"], stop["cycle"], stop["rc"]) == ("stop", 1, 3)
    assert "looks wedged" in stop["error"]
    connects = [row for row in rows if row["step"] == "connect"]
    first_refused = next(index for index, row in enumerate(connects) if row["error"] == "refused")
    assert first_refused >= accepted_connects
    assert all(row["rc"] == 0 for row in connects[:first_refused])
    assert all(row["error"] == "refused" for row in connects[first_refused:])
    refused_started = connects[first_refused]["elapsed"] - connects[first_refused]["answer"]["seconds"]
    assert stop["elapsed"] - refused_started >= _GIVE_UP
    assert "stopped:" in capsys.readouterr().err


@pytest.mark.os_agnostic
def test_a_printer_that_is_not_tcp_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--printer", f"file:{tmp_path / 'out.prn'}", "--label", str(_label(tmp_path))]
    assert _main()([*args, "--log", str(tmp_path / "log.jsonl")]) == 2
    assert "tcp://" in capsys.readouterr().err


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("error", "name"),
    [
        (ConnectionRefusedError(111, "refused"), "refused"),
        (TimeoutError("timed out"), "timeout"),
        (ConnectionResetError(104, "reset"), "reset"),
        (OSError(113, "no route"), "OSError errno 113"),
    ],
)
def test_a_failed_connect_is_named_by_what_failed(error: OSError, name: str) -> None:
    connect_error: Callable[[OSError], str] = _names()["connect_error"]
    assert connect_error(error) == name
