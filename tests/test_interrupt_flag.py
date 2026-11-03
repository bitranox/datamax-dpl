"""A Ctrl-C that a C extension swallowed on its way out still ends the batch.

pydantic-core reports an exception raised inside a callback it makes (``abc.__instancecheck__``) as
"unraisable" and validates on, so the interrupt can vanish while a record is filled and the whole
job then goes out with exit 0. The handler notes the interrupt before raising it, so that the loop
over the records finds it however the raise ended.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from printer_server import ImageModules, ModulePrinter

from datamax_dpl.adapters.cli.job_spool import FileJobSpool
from datamax_dpl.adapters.cli.label_io import Records
from datamax_dpl.adapters.cli.main import main
from datamax_dpl.adapters.memory import RecordingPrinter
from datamax_dpl.composition import AppServices, build_testing
from datamax_dpl.domain.timing import DEFAULT_TRANSPORT_TUNING, TransportTuning

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from datamax_dpl.application.ports import PrinterTransport
    from datamax_dpl.application.use_cases import BatchEntry

_INTERRUPTED_EXIT = 130
#: Where a label's format begins: <STX>L.
_FORMAT_START = b"\x02L"


def _swallow_an_interrupt() -> None:
    """Deliver SIGINT to the handler in place, and end its raise as the validator does: nowhere."""
    handler = signal.getsignal(signal.SIGINT)
    assert callable(handler)
    try:
        handler(signal.SIGINT, None)
    except KeyboardInterrupt:
        return


def _interrupted_while_running(printer: RecordingPrinter) -> Callable[[], AppServices]:
    """The services of a run whose Ctrl-C came, and was swallowed, once the handlers were set."""

    def services() -> AppServices:
        _swallow_an_interrupt()
        return build_testing(printer=printer)

    return services


def _batch(tmp_path: Path) -> tuple[str, str]:
    label = tmp_path / "label.json"
    label.write_text(json.dumps({"elements": [{"type": "text", "x": 1, "y": 1, "text": "{a}"}]}), encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_bytes(b"a\nr0\nr1\n")
    return str(label), str(data)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "command",
    [
        pytest.param(("print", "--printer", "file:{out}"), id="print"),
        pytest.param(("render", "-o", "{out}"), id="render-file"),
        pytest.param(("validate",), id="validate"),
    ],
)
def test_a_swallowed_interrupt_ends_a_batch_with_130_and_nothing_out(
    command: tuple[str, ...], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    label, data = _batch(tmp_path)
    out = tmp_path / "job.dpl"
    # Every printer URI opens this recording printer, a file: one included: the file is never written.
    printer = RecordingPrinter()
    verb, *rest = [word.format(out=out) for word in command]
    services = _interrupted_while_running(printer)
    code = main([verb, label, "--data", data, "--model", "dmx-600", *rest], services_factory=services)
    assert code == _INTERRUPTED_EXIT, capsys.readouterr().err
    assert printer.sent == []
    assert not out.exists()


@pytest.mark.os_agnostic
def test_a_run_with_no_interrupt_is_left_alone(tmp_path: Path) -> None:
    """The control, and a run after an interrupted one: the note does not outlive its run."""
    label, data = _batch(tmp_path)
    assert main(["validate", label, "--data", data, "--model", "dmx-600"], services_factory=build_testing) == 0


@pytest.mark.os_agnostic
def test_a_handler_the_host_set_stays(tmp_path: Path) -> None:
    label, data = _batch(tmp_path)
    seen: list[Callable[..., object] | int | None] = []

    def ours(_signum: int, _frame: object) -> None:
        return

    def services() -> AppServices:
        seen.append(signal.getsignal(signal.SIGINT))
        return build_testing()

    before = signal.signal(signal.SIGINT, ours)
    try:
        assert main(["validate", label, "--data", data, "--model", "dmx-600"], services_factory=services) == 0
        assert seen == [ours]
        assert signal.getsignal(signal.SIGINT) is ours
    finally:
        signal.signal(signal.SIGINT, before)


#: The console keys: Ctrl-C everywhere, Ctrl-Break on Windows alone.
_CONSOLE_KEYS = [
    pytest.param("SIGINT", id="ctrl-c"),
    pytest.param(
        "SIGBREAK",
        id="ctrl-break",
        marks=pytest.mark.skipif(sys.platform != "win32", reason="SIGBREAK is a Windows console signal"),
    ),
]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", _CONSOLE_KEYS)
def test_the_handler_is_gone_after_the_run(key: str, tmp_path: Path) -> None:
    signum = signal.Signals[key]
    label, data = _batch(tmp_path)
    before = signal.getsignal(signum)
    assert main(["validate", label, "--data", data, "--model", "dmx-600"], services_factory=build_testing) == 0
    assert signal.getsignal(signum) is before


@pytest.mark.os_agnostic
def test_records_outside_a_run_read_as_ever(tmp_path: Path) -> None:
    """A library caller of Records has no handler set, and nothing to find."""
    _, data = _batch(tmp_path)
    assert [row["a"] for row in Records((tmp_path / "data.csv").read_bytes(), data)] == ["r0", "r1"]


class _SwallowingAtTheLast(FileJobSpool):
    """A real spool that loses a Ctrl-C while it keeps the batch's last record, as a fill can.

    The last record is filled and kept after the pass over the records has looked for the note for
    the last time, so a Ctrl-C lost there is found only by what runs between the check and the send.
    """

    def __init__(self, *, records: int) -> None:
        super().__init__()
        self._left = records

    def add(self, entry: BatchEntry) -> None:
        self._left -= 1
        if self._left == 0:
            _swallow_an_interrupt()
        super().add(entry)


def _losing_at_the_last(printer: RecordingPrinter) -> Callable[[], AppServices]:
    def services() -> AppServices:
        return dataclasses.replace(build_testing(printer=printer), open_spool=lambda: _SwallowingAtTheLast(records=2))

    return services


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "command",
    [
        pytest.param(("print", "--printer", "file:{out}"), id="print-one-way"),
        pytest.param(("print", "--printer", "tcp://192.0.2.60:9100"), id="print-two-way"),
        pytest.param(("render", "-o", "-"), id="render-stdout"),
        pytest.param(("render", "-o", "{linked}"), id="render-file-direct"),
    ],
)
def test_a_ctrl_c_lost_on_the_last_record_sends_nothing(
    command: tuple[str, ...], tmp_path: Path, capfdbinary: pytest.CaptureFixture[bytes]
) -> None:
    label, data = _batch(tmp_path)
    out = tmp_path / "job.dpl"
    # A second hard link sends render -o to the direct route, which writes from the spool.
    linked = tmp_path / "linked.dpl"
    linked.write_bytes(b"before")
    (tmp_path / "other-name.dpl").hardlink_to(linked)
    printer = RecordingPrinter()
    verb, *rest = [word.format(out=out, linked=linked) for word in command]
    code = main(
        [verb, label, "--data", data, "--model", "dmx-600", *rest], services_factory=_losing_at_the_last(printer)
    )
    captured = capfdbinary.readouterr()
    assert code == _INTERRUPTED_EXIT, captured.err
    assert printer.sent == []
    assert b"r0" not in captured.out
    assert linked.read_bytes() == b"before"
    assert not out.exists()


class _SerializingAnInterrupt(FileJobSpool):
    """A spool whose write turns a Ctrl-C into its own error, as pydantic's JSON serializer does."""

    def add(self, entry: BatchEntry) -> None:
        _swallow_an_interrupt()
        raise ValueError("Error serializing to JSON: KeyboardInterrupt")


@pytest.mark.os_agnostic
def test_a_ctrl_c_a_library_turned_into_its_own_error_still_ends_with_130(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    label, data = _batch(tmp_path)
    printer = RecordingPrinter()

    def services() -> AppServices:
        return dataclasses.replace(build_testing(printer=printer), open_spool=_SerializingAnInterrupt)

    code = main(["print", label, "--data", data, "--model", "dmx-600", "--printer", "-"], services_factory=services)
    captured = capsys.readouterr()
    assert code == _INTERRUPTED_EXIT, captured.err
    assert "serializing" not in captured.err
    assert printer.sent == []


class _LosingAtTheSend(FileJobSpool):
    """A real spool that loses a Ctrl-C while the send reads record ``at`` back to render it again.

    The check pass looked for the note for the last time before the send began, so only what the
    send itself passes can stop the records after this one.
    """

    def __init__(self, *, at: int) -> None:
        super().__init__()
        self._at = at

    def entries(self) -> Iterator[BatchEntry]:
        for number, entry in enumerate(super().entries()):
            if number == self._at:
                _swallow_an_interrupt()
            yield entry


def _image_batch(tmp_path: Path, *, records: int) -> tuple[str, str]:
    """A label whose image goes to module B, which no clear empties: each label renders again at send."""
    label = tmp_path / "label.json"
    bitmap = ["#." * 8] * 16
    elements = [
        {"type": "image", "x": 1, "y": 1, "name": "LOGO", "bitmap": bitmap},
        {"type": "text", "x": 10, "y": 40, "text": "{a}"},
    ]
    label.write_text(json.dumps({"elements": elements}), encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_bytes(b"a\n" + b"".join(b"r%d\n" % number for number in range(records)))
    return str(label), str(data)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("at", [2, 6])
def test_a_ctrl_c_lost_while_the_send_renders_a_record_again_stops_at_that_record(
    at: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    label, data = _image_batch(tmp_path, records=8)
    printer = ModulePrinter(ImageModules())

    def services() -> AppServices:
        return dataclasses.replace(build_testing(printer=printer), open_spool=lambda: _LosingAtTheSend(at=at))

    code = main(
        [
            *("--set", "printer.image_module=B", "print", label, "--data", data),
            *("--model", "dmx-600", "--printer", "tcp://192.0.2.60:9100"),
        ],
        services_factory=services,
    )
    assert code == _INTERRUPTED_EXIT, capsys.readouterr().err
    assert b"".join(printer.sent).count(_FORMAT_START) == at


def _opening_after_a_loss(printer: RecordingPrinter) -> Callable[[], AppServices]:
    """Services whose opener loses a Ctrl-C as it opens: the label renders after, and then goes nowhere."""

    def services() -> AppServices:
        base = build_testing(printer=printer)
        inner = base.open_printer

        def losing(uri: str, *, timeout: float, tuning: TransportTuning = DEFAULT_TRANSPORT_TUNING) -> PrinterTransport:
            _swallow_an_interrupt()
            return inner(uri, timeout=timeout, tuning=tuning)

        return dataclasses.replace(base, open_printer=losing)

    return services


@pytest.mark.os_agnostic
@pytest.mark.parametrize("uri", ["tcp://192.0.2.60:9100", "file:{out}"], ids=["two-way", "one-way"])
def test_a_ctrl_c_lost_before_a_single_label_goes_sends_nothing(
    uri: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    label = tmp_path / "label.json"
    label.write_text(json.dumps({"elements": [{"type": "text", "x": 1, "y": 1, "text": "HELLO"}]}), encoding="utf-8")
    printer = RecordingPrinter()
    target = uri.format(out=tmp_path / "job.dpl")
    arguments = ["print", str(label), "--model", "dmx-600", "--printer", target]
    code = main(arguments, services_factory=_opening_after_a_loss(printer))
    assert code == _INTERRUPTED_EXIT, capsys.readouterr().err
    assert printer.sent == []


@pytest.mark.os_agnostic
def test_a_ctrl_c_lost_before_the_calibration_labels_go_sends_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    printer = RecordingPrinter()
    code = main(
        [
            *("calibrate", "print", "--model", "allegro-2", "--printer", "tcp://192.0.2.60:9100"),
            *("--width", "100", "--height", "55"),
        ],
        services_factory=_opening_after_a_loss(printer),
    )
    assert code == _INTERRUPTED_EXIT, capsys.readouterr().err
    assert printer.sent == []


_SRC = Path(__file__).resolve().parents[1] / "src" / "datamax_dpl"
_CLI = _SRC / "adapters" / "cli"
#: The application's functions that render a job; the domain's are each export returning a RenderResult,
#: so a renderer added there is in the scan without a line here.
_APPLICATION_RENDERERS = frozenset({"check_batch", "render_label", "render_batch", "iter_batch", "record_renders"})
_SENDS_THROUGH_THE_CHANNEL = "it sends only through printer_opener's channel, which raises a noted Ctrl-C first"
#: Where the CLI may render directly, and why no byte of a job can follow a lost Ctrl-C there.
_DIRECT_RENDERS = {
    ("labels.py", "check_loaded"): "the one gate for files and stdout: it raises a noted Ctrl-C before it returns",
    ("printer.py", "cli_preview.body"): "preview renders only for how the job sends its fields, and writes a picture",
    ("printer.py", "_print_body"): _SENDS_THROUGH_THE_CHANNEL,
    ("calibrate.py", "cli_calibrate_print.body"): _SENDS_THROUGH_THE_CHANNEL,
}
#: The functions through which a CLI module could reach a printer opener that is not interrupt-checked.
_OPENER_FUNCTIONS = frozenset({"open_printer", "tuned_open_printer", "bind_tuning", "open_recording_printer"})


def _transport_classes() -> frozenset[str]:
    """Every transport class the transport package defines: built directly, each opens a printer past the check.

    Read from the package rather than listed, so a transport added later is covered without
    anyone remembering this test.
    """
    return frozenset(
        node.name
        for path in sorted((_SRC / "adapters" / "transport").glob("*.py"))
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name.endswith("Transport")
    )


#: The transport classes, which a CLI module may not name at all: not to build one, not to import one.
_TRANSPORTS = _transport_classes()
#: The names through which a CLI module could reach a printer channel that is not interrupt-checked.
_OPENERS = _OPENER_FUNCTIONS | _TRANSPORTS


def _job_builders(path: Path) -> frozenset[str]:
    """The functions a domain module exports whose return names a ``RenderResult``: the ones that make a job."""
    body = ast.parse(path.read_text(encoding="utf-8")).body
    exported: set[str] = set()
    for node in body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            exported.update(ast.literal_eval(node.value))
    return frozenset(
        node.name
        for node in body
        if isinstance(node, ast.FunctionDef)
        and node.name in exported
        and node.returns is not None
        and "RenderResult" in ast.unparse(node.returns)
    )


def _renderers() -> frozenset[str]:
    domain = _job_builders(_SRC / "domain" / "render.py") | _job_builders(_SRC / "domain" / "reset_label.py")
    return domain | _APPLICATION_RENDERERS


def _called_name(node: ast.Call) -> str | None:
    """The name a call calls, whether bare (``render(...)``) or an attribute (``use_cases.render(...)``)."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


class _Sites(ast.NodeVisitor):
    """Each render call and each reach for an opener, with the functions around it."""

    def __init__(self, module: str, renderers: frozenset[str]) -> None:
        self.module = module
        self.renderers = renderers
        self.functions: list[str] = []
        self.renders: set[tuple[str, str]] = set()
        self.openers: set[tuple[str, str]] = set()

    def _here(self) -> tuple[str, str]:
        return (self.module, ".".join(self.functions))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if _called_name(node) in self.renderers:
            self.renders.add(self._here())
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _OPENERS:
            self.openers.add(self._here())
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        # Opener functions only by attribute or import: a parameter may carry the name (the wrapper's own).
        if node.id in _TRANSPORTS:
            self.openers.add(self._here())

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if any(alias.name in _OPENERS for alias in node.names):
            self.openers.add(self._here())


def _cli_sites() -> list[_Sites]:
    renderers = _renderers()
    found: list[_Sites] = []
    for path in sorted(_CLI.rglob("*.py")):
        sites = _Sites(path.name, renderers)
        sites.visit(ast.parse(path.read_text(encoding="utf-8")))
        found.append(sites)
    return found


@pytest.mark.os_agnostic
def test_every_cli_render_of_a_job_goes_through_a_gate() -> None:
    """A new route that renders a job beside the gates would send what a lost Ctrl-C should have stopped."""
    assert {"build_job", "render", "iter_jobs", "build_record", "calibration_job"} <= _renderers()
    found = {site for sites in _cli_sites() for site in sites.renders}
    assert found, "the scan found no render call at all: it is not looking where the CLI renders"
    assert found <= set(_DIRECT_RENDERS), sorted(found - set(_DIRECT_RENDERS))


@pytest.mark.os_agnostic
def test_every_cli_printer_channel_comes_from_the_interrupt_checked_opener() -> None:
    """A route opening a printer any other way, a transport class built directly too, sends past a lost Ctrl-C."""
    assert {"TcpTransport", "SerialTransport", "DeviceTransport", "FileTransport", "StdoutTransport"} <= _TRANSPORTS
    found = {site for sites in _cli_sites() for site in sites.openers}
    assert found == {("model_options.py", "printer_opener")}, sorted(found)
    tree = ast.parse((_CLI / "commands" / "model_options.py").read_text(encoding="utf-8"))
    opener = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "printer_opener")
    returned = [n.value for n in ast.walk(opener) if isinstance(n, ast.Return)]
    assert [isinstance(r, ast.Call) and _called_name(r) for r in returned] == ["interrupt_checked"]


class _KeyAtTheFirst(FileJobSpool):
    """A real spool whose first record meets a console key: its handler runs in place, its raise goes on up."""

    def __init__(self, *, signum: signal.Signals, directories: list[Path]) -> None:
        super().__init__()
        self._signum = signum
        self._directories = directories

    def add(self, entry: BatchEntry) -> None:
        super().add(entry)
        assert self.path is not None
        self._directories.append(self.path.parent)
        handler = signal.getsignal(self._signum)
        # The system's default for a key ends the process with no finally run; only a handler is testable.
        if callable(handler):
            handler(self._signum, None)


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", _CONSOLE_KEYS)
def test_a_console_key_during_a_batch_ends_it_with_130_and_removes_the_spool(
    key: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-Break on Windows takes Ctrl-C's way out: before, the system ended the process and the spool stayed."""
    label, data = _batch(tmp_path)
    printer = RecordingPrinter()
    directories: list[Path] = []

    def services() -> AppServices:
        spool = _KeyAtTheFirst(signum=signal.Signals[key], directories=directories)
        return dataclasses.replace(build_testing(printer=printer), open_spool=lambda: spool)

    code = main(
        ["print", label, "--data", data, "--model", "dmx-600", "--printer", "tcp://192.0.2.60:9100"],
        services_factory=services,
    )
    assert code == _INTERRUPTED_EXIT, capsys.readouterr().err
    assert printer.sent == []
    assert len(directories) == 1
    assert not directories[0].exists()
