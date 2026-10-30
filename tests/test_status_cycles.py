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

from datamax_dpl import __init__conf__
from datamax_dpl.domain.label import Label
from datamax_dpl.domain.render import build_job
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.status import STATUS_REQUEST

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "status_cycles.py"
_SRC = str(Path(__file__).resolve().parent.parent / "src")
_ENV_PREFIX = __init__conf__.LAYEREDCONF_SLUG.upper().replace("-", "_")
_LABEL = {"elements": [{"type": "text", "x": 5, "y": 5, "text": "Hello"}]}
_MODEL = "allegro-2"
#: ``<SOH>A`` answer of a printer with no flag set.
_IDLE_STATUS = b"NNNNNNNN\r"
_FIELDS = ["t", "elapsed", "cycle", "step", "rc", "bytes", "answer", "error"]
#: Short enough for a test, long enough that a busy runner keeps the order of the connects.
_INTERVAL = 0.2


def _names() -> dict[str, Any]:
    """The script's module namespace, loaded from its file the way a maintainer runs it."""
    return runpy.run_path(str(_SCRIPT), run_name="status_cycles")


def _main() -> Callable[[list[str]], int]:
    main: Callable[[list[str]], int] = _names()["main"]
    return main


@pytest.fixture(autouse=True)
def isolated_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Give the datamax-dpl subprocesses no ambient configuration, and the package from this tree.

    The rootdir conftest already points the configuration layers under ``tmp_path`` through
    ``os.environ``, which the subprocesses inherit; a ``DATAMAX_DPL___`` variable or a ``.env``
    above the working directory would still set a scale and change the bytes compared here.
    """
    for name in [name for name in os.environ if name.startswith(_ENV_PREFIX)]:
        monkeypatch.delenv(name)
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", f"{_SRC}{os.pathsep}{existing}" if existing else _SRC)
    monkeypatch.chdir(tmp_path)


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
def test_a_port_that_stops_accepting_ends_the_run_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], connections: Callable[[int], ConnectionsServer]
) -> None:
    """The server takes the print and the three status questions, then nothing listens on the port."""
    server = connections(1 + 3)

    code = _main()(_args(server, tmp_path, "--cycles", "2", "--connects", "2", "--give-up", "0.5"))
    server.join()

    assert code == 3
    rows = _log_rows(tmp_path / "cycles.jsonl")
    assert [row["step"] for row in rows[:4]] == ["print", "status", "status", "status"]
    assert rows[-1]["step"] == "stop"
    assert rows[-1]["cycle"] == 1
    assert "looks wedged" in rows[-1]["error"]
    refused = [row for row in rows if row["step"] == "connect" and row["error"] == "refused"]
    assert len(refused) >= 2
    assert refused[-1]["elapsed"] - refused[0]["elapsed"] >= 0.5 - _INTERVAL
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
