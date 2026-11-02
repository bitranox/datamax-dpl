"""verify on a two-way channel: what it sends, which channel it polls on, and what it leaves stored.

The library route with no upload check, then the same job through ``print_checked``'s path, then the
CLI against a printer that keeps its image modules between connections.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
from printer_server import ImageModules, ModulePrinter

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.memory import RecordingPrinter, open_recording_printer
from datamax_dpl.application.upload_check import CheckedJob, UploadCheck
from datamax_dpl.application.use_cases import AnswerWait, record_renders, render_label
from datamax_dpl.application.verify import VerifyReport, verify_print
from datamax_dpl.composition import build_production
from datamax_dpl.domain.camera import CameraKind, CameraSettings, CapturedImage
from datamax_dpl.domain.enums import ImageModule, MediaType
from datamax_dpl.domain.errors import PrinterTimeoutError
from datamax_dpl.domain.label import Image, Label, Text
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.status import STATUS_REQUEST

if TYPE_CHECKING:
    import socket
    from pathlib import Path

    from click.testing import CliRunner
    from printer_server import Play, PrinterServer, Serve

    from datamax_dpl.application.ports import OpenPrinter, PrinterTransport
    from datamax_dpl.domain.render import RenderResult

FORMAT = b"\x02L\r"
IDLE = b"NNNNNNNN\r"
URI = "tcp://192.0.2.60:9100"
ALLEGRO = RenderOptions(model="allegro-2")
_PATTERN = ["".join("#" if (row * 7 + column * 13) % 5 == 0 else "." for column in range(64)) for row in range(64)]
LOGO = Image(x=1, y=1, name="LOGO", bitmap=_PATTERN)
#: 45000 bytes of dot data: past the Allegro 2's default budget, within what the fake module holds.
_BIG_PATTERN = ["".join("#" if (row * 3 + col * 11) % 7 == 0 else "." for col in range(600)) for row in range(600)]
BIG = Image(x=1, y=1, name="BIG", bitmap=_BIG_PATTERN)


def _verify(
    result: RenderResult, label: Label, open_printer: OpenPrinter, tmp_path: Path, *, checked: CheckedJob | None = None
) -> VerifyReport:
    def capture(settings: CameraSettings, *, output_dir: Path) -> CapturedImage:
        del settings, output_dir
        return CapturedImage(
            path=tmp_path / "photo.png", media_type=MediaType.PNG, captured_at=datetime.now(tz=timezone.utc)
        )

    return verify_print(
        result,
        label,
        options=ALLEGRO,
        uri=URI,
        timeout=5.0,
        camera=CameraSettings(kind=CameraKind.FILE),
        output_dir=tmp_path,
        open_printer=open_printer,
        capture=capture,
        sleep=lambda _seconds: None,
        checked=checked,
    )


@pytest.mark.os_agnostic
def test_a_library_verify_with_no_check_sends_the_job_on_a_two_way_channel(tmp_path: Path) -> None:
    label = Label(elements=[Text(x=1, y=1, text="A")])
    result = render_label(label, ALLEGRO)
    printer = RecordingPrinter(answers={STATUS_REQUEST: IDLE})

    report = _verify(result, label, open_recording_printer(printer), tmp_path)

    assert printer.data.startswith(result.data)
    assert report.bytes_sent == len(result.data)
    assert report.status is not None
    assert report.status.idle


class _HangsUpOnTheRead(ModulePrinter):
    """A channel lost during the directory read: the job has to go on, and end, on another."""

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = 120.0) -> bytes:
        del request, timeout, idle, total
        raise PrinterTimeoutError("no answer", unanswered=True)


@pytest.mark.os_agnostic
def test_verify_polls_on_the_channel_the_job_ended_on_and_closes_what_it_opened(tmp_path: Path) -> None:
    modules = ImageModules()
    first = _HangsUpOnTheRead(modules, answers={STATUS_REQUEST: IDLE})
    second = ModulePrinter(modules, answers={STATUS_REQUEST: IDLE})
    channels = iter([first, second])
    opened: list[PrinterTransport] = []

    def open_next(uri: str, *, timeout: float) -> PrinterTransport:
        del uri, timeout
        channel = next(channels)
        opened.append(channel)
        return channel

    label = Label(elements=[LOGO])
    job = CheckedJob(
        renders=record_renders(label, None, ALLEGRO), check=UploadCheck(wait=AnswerWait(seconds=1.0), rounds=3)
    )
    _verify(render_label(label, ALLEGRO), label, open_next, tmp_path, checked=job)

    assert opened == [first, second], "a third open means the status went on a new connection"
    assert STATUS_REQUEST in second.queries
    assert second.closed


def _modules_answering_status(modules: ImageModules) -> Play:
    """A printer that stores what it is sent and answers both its directory and its status."""

    def play(server: PrinterServer, connection: socket.socket) -> None:
        with contextlib.suppress(ConnectionResetError, TimeoutError):
            while chunk := connection.recv(65536):
                server.record(chunk)
                reply = modules.feed(chunk.replace(STATUS_REQUEST, b""))
                if STATUS_REQUEST in chunk:
                    reply += IDLE
                if reply:
                    connection.sendall(reply)

    return play


def _label_file(tmp_path: Path, element: Image) -> str:
    path = tmp_path / "label.json"
    document = {"elements": [element.model_dump(mode="json", exclude_none=True)]}
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def _verify_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATAMAX_DPL___PRINTER__STATUS_WAIT", "0.3")
    monkeypatch.setenv("DATAMAX_DPL___CAMERA__KIND", "file")
    monkeypatch.setenv("DATAMAX_DPL___CAMERA__DIRECTORY", str(tmp_path))
    monkeypatch.setenv("DATAMAX_DPL___CAMERA__TIMEOUT", "0.1")


@pytest.mark.usefixtures("no_ambient_configuration")
def test_cli_verify_with_no_clear_never_downloads_a_name_it_read(
    cli_runner: CliRunner, tmp_path: Path, serve: Serve, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-download of a held name silently loses the format on an Allegro 2."""
    options = RenderOptions(model="allegro-2", image_module=ImageModule.B, model_image_budget=False)
    [name] = render_label(Label(elements=[LOGO]), options).downloads
    modules = ImageModules(held=[("B", name, 2)])
    server = serve(_modules_answering_status(modules), _modules_answering_status(modules))
    _verify_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DATAMAX_DPL___PRINTER__IMAGE_MODULE", "B")

    result = cli_runner.invoke(
        cli_mod.cli,
        [
            "--json",
            "verify",
            _label_file(tmp_path, LOGO),
            "--output-dir",
            str(tmp_path / "out"),
            "--model",
            "allegro-2",
            "--printer",
            server.address,
        ],
        obj=build_production,
    )
    server.join()

    assert FORMAT in bytes(server.received), result.output
    assert modules.downloaded_held == [], result.output
    assert modules.downloads == [], result.output


@pytest.mark.usefixtures("no_ambient_configuration")
def test_cli_verify_over_tcp_keeps_full_size_past_the_models_budget(
    cli_runner: CliRunner, tmp_path: Path, serve: Serve, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The budget stands in for a check only where none can run; verify on TCP has one."""
    modules = ImageModules()
    server = serve(_modules_answering_status(modules))
    _verify_env(monkeypatch, tmp_path)
    [full] = render_label(Label(elements=[BIG]), RenderOptions(model="allegro-2", model_image_budget=False)).downloads

    result = cli_runner.invoke(
        cli_mod.cli,
        [
            "--json",
            "verify",
            _label_file(tmp_path, BIG),
            "--output-dir",
            str(tmp_path / "out"),
            "--model",
            "allegro-2",
            "--printer",
            server.address,
        ],
        obj=build_production,
    )
    server.join()

    assert FORMAT in bytes(server.received), result.output
    assert modules.downloads == [full], result.output
