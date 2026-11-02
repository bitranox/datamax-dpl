"""A Ctrl-C that a C extension swallowed on its way out still ends the batch.

pydantic-core reports an exception raised inside a callback it makes (``abc.__instancecheck__``) as
"unraisable" and validates on, so the interrupt can vanish while a record is filled and the whole
job then goes out with exit 0. The handler notes the interrupt before raising it, so that the loop
over the records finds it however the raise ended.
"""

from __future__ import annotations

import dataclasses
import json
import signal
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.cli.label_io import Records
from datamax_dpl.adapters.cli.main import main
from datamax_dpl.composition import AppServices, build_testing

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

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
