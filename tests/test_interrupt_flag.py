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
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.cli.job_spool import FileJobSpool
from datamax_dpl.adapters.cli.label_io import Records
from datamax_dpl.adapters.cli.main import main
from datamax_dpl.adapters.memory import RecordingPrinter
from datamax_dpl.composition import AppServices, build_testing

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamax_dpl.application.use_cases import BatchEntry

_INTERRUPTED_EXIT = 130


def _swallow_an_interrupt() -> None:
    """Deliver SIGINT to the handler in place, and end its raise as the validator does: nowhere."""
    handler = signal.getsignal(signal.SIGINT)
    assert callable(handler)
    try:
        handler(signal.SIGINT, None)
    except KeyboardInterrupt:
        return


def _interrupted_while_running() -> AppServices:
    """The services of a run whose Ctrl-C came, and was swallowed, once the handlers were set."""
    _swallow_an_interrupt()
    return dataclasses.replace(build_testing())


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
    verb, *rest = [word.format(out=out) for word in command]
    code = main([verb, label, "--data", data, "--model", "dmx-600", *rest], services_factory=_interrupted_while_running)
    assert code == _INTERRUPTED_EXIT, capsys.readouterr().err
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


@pytest.mark.os_agnostic
def test_the_handler_is_gone_after_the_run(tmp_path: Path) -> None:
    label, data = _batch(tmp_path)
    before = signal.getsignal(signal.SIGINT)
    assert main(["validate", label, "--data", data, "--model", "dmx-600"], services_factory=build_testing) == 0
    assert signal.getsignal(signal.SIGINT) is before


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
    code = main([verb, label, "--data", data, "--model", "dmx-600", *rest], services_factory=_losing_at_the_last(printer))
    captured = capfdbinary.readouterr()
    assert code == _INTERRUPTED_EXIT, captured.err
    assert printer.sent == []
    assert b"r0" not in captured.out
    assert linked.read_bytes() == b"before"
    assert not out.exists()


#: The functions that render a job; each CLI call of one must come through check_loaded.
_RENDERERS = frozenset({"check_batch", "render_label", "render_batch", "iter_batch"})
#: Where the CLI may call one directly, and why no byte of a job can follow it there.
_DIRECT_RENDERS = {
    ("labels.py", "check_loaded"): "the one gate: it raises a noted Ctrl-C before it returns",
    ("printer.py", "cli_preview.body"): "preview renders only for how the job sends its fields, and writes a picture",
}


class _RenderCalls(ast.NodeVisitor):
    """Each render call with the function around it, named with the functions it is nested in."""

    def __init__(self, module: str) -> None:
        self.module = module
        self.functions: list[str] = []
        self.found: set[tuple[str, str]] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in _RENDERERS:
            self.found.add((self.module, ".".join(self.functions)))
        self.generic_visit(node)


def _render_calls(path: Path) -> set[tuple[str, str]]:
    calls = _RenderCalls(path.name)
    calls.visit(ast.parse(path.read_text(encoding="utf-8")))
    return calls.found


@pytest.mark.os_agnostic
def test_every_cli_render_of_a_job_goes_through_the_gate() -> None:
    """A new route that renders a job beside check_loaded would send what a lost Ctrl-C should have stopped."""
    cli = Path(__file__).resolve().parents[1] / "src" / "datamax_dpl" / "adapters" / "cli"
    found = {site for path in sorted(cli.rglob("*.py")) for site in _render_calls(path)}
    assert found, "the scan found no render call at all: it is not looking where the CLI renders"
    assert found <= set(_DIRECT_RENDERS), sorted(found - set(_DIRECT_RENDERS))
