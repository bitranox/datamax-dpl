"""The JSON contract through the real ``main()``: usage errors and the info and config commands.

``CliRunner`` runs click in standalone mode, which handles a usage error by itself, so these go
through ``main()`` as a console script does.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import lib_log_rich.runtime
import pytest
from lib_layered_config import Config, ConfigError
from pydantic import BaseModel

from datamax_dpl import __init__conf__
from datamax_dpl.adapters.cli.context import CLIContext
from datamax_dpl.adapters.cli.envelope import (
    CommandName,
    Envelope,
    OutputMode,
    forget_started_envelope,
    write_envelope,
)
from datamax_dpl.adapters.cli.main import main
from datamax_dpl.adapters.cli.runner import CommandResult, run_command
from datamax_dpl.adapters.config.display import display_config
from datamax_dpl.composition import AppServices, build_production, build_testing
from datamax_dpl.domain.errors import LabelRejectedError

if TYPE_CHECKING:
    from collections.abc import Callable


def _services(data: dict[str, Any] | None = None, **overrides: Any) -> Callable[[], AppServices]:
    config = Config(data or {}, {})

    def get_config(**_kwargs: Any) -> Config:
        return config

    def build() -> AppServices:
        return dataclasses.replace(build_testing(), **{"get_config": get_config, **overrides})

    return build


def _raising(error: BaseException) -> Callable[..., Any]:
    def raise_it(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    return raise_it


def _run(args: list[str], capsys: pytest.CaptureFixture[str], **overrides: Any) -> tuple[int, str, str]:
    code = main(args, services_factory=_services(**overrides))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


USAGE_ERRORS = [
    (["validate", "x.json", "--policy", "lax"], "validate", "BadParameter"),
    (["validate", "--bogus", "x.json"], "validate", "NoSuchOption"),
    (["validate"], "validate", "MissingParameter"),
    (["render", "x.json", "--cutter", "maybe"], "render", "BadParameter"),
    (["memory", "--kind", "bogus"], "memory", "BadParameter"),
    (["status", "--timeout", "abc"], "status", "BadParameter"),
    (["calibrate", "print", "--axis", "bad"], "calibrate print", "BadParameter"),
    (["no-such-command"], None, "NoSuchCommand"),
    (["--no-such-global", "validate"], "validate", "NoSuchOption"),
    (["--no-such-global"], None, "NoSuchOption"),
    (["--set", "bad", "info"], "info", "UsageError"),
    (["calibrate", "nosuch"], "calibrate", "NoSuchCommand"),
    (["config-generate-examples"], "config-generate-examples", "MissingParameter"),
    (["calibrate"], "calibrate", "UsageError"),
]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("args", "command", "error"), USAGE_ERRORS)
@pytest.mark.parametrize("flag", ["--json", "--json-bare"])
def test_a_usage_error_answers_with_an_envelope_under_json(
    capsys: pytest.CaptureFixture[str], flag: str, args: list[str], command: str | None, error: str
) -> None:
    """stdout is the envelope, the usage text stays on stderr, the code stays 2."""
    code, out, err = _run([flag, *args], capsys)
    assert code == 2
    document = json.loads(out)
    assert document["ok"] is False
    assert document["command"] == command
    assert document["error"] == error
    assert document["message"]
    assert document["message"] in err or "Usage" in err or "Error" in err
    assert (out.count("\n") == 1) is (flag == "--json-bare")


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("args", "command", "error"), USAGE_ERRORS)
def test_a_usage_error_prints_no_json_without_the_flag(
    capsys: pytest.CaptureFixture[str], args: list[str], command: str | None, error: str
) -> None:
    """Human mode keeps stdout empty and the box on stderr."""
    code, out, err = _run(args, capsys)
    assert code == 2
    assert out == ""
    assert err


@pytest.mark.os_agnostic
@pytest.mark.parametrize("flag", ["--json", "--json-bare"])
def test_the_json_flag_is_found_behind_a_global_option_that_takes_a_value(
    capsys: pytest.CaptureFixture[str], flag: str
) -> None:
    """--set takes a value, so the scan for the flag does not stop at it."""
    code, out, _ = _run(["--set", "a.b=1", "--profile", "x", flag, "validate", "--bogus"], capsys)
    assert code == 2
    assert json.loads(out)["error"] == "NoSuchOption"


@pytest.mark.os_agnostic
def test_a_value_spelled_like_the_flag_is_not_the_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """The word after --set is its value, and a word after the subcommand belongs to the subcommand."""
    code, out, _ = _run(["--set", "--json", "validate", "--bogus"], capsys)
    assert code == 2
    assert out == ""
    code, out, _ = _run(["validate", "--data", "--json-bare"], capsys)
    assert code == 2
    assert out == ""


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("flag", "indent"), [("--json", True), ("--json-bare", False)])
def test_the_json_flag_after_the_subcommand_is_answered_in_the_shape_it_asked_for(
    capsys: pytest.CaptureFixture[str], flag: str, indent: bool
) -> None:
    """It does not parse there, but a caller who typed it reads JSON: the envelope says what went wrong."""
    code, out, err = _run(["validate", "x.json", flag], capsys)
    assert code == 2
    document = json.loads(out)
    assert (document["command"], document["error"]) == ("validate", "NoSuchOption")
    assert (out.count("\n") > 1) is indent
    assert flag in err


# ======================== info and the config commands ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize("flag", ["--json", "--json-bare"])
def test_info_answers_with_an_envelope(capsys: pytest.CaptureFixture[str], flag: str) -> None:
    code, out, _ = _run([flag, "info"], capsys)
    assert code == 0
    document = json.loads(out)
    assert (document["ok"], document["command"], document["skipped"]) == (True, "info", [])
    assert document["data"]["version"] == __init__conf__.version
    assert document["data"]["name"] == __init__conf__.name
    assert set(document["data"]) == {
        "name",
        "title",
        "version",
        "homepage",
        "author",
        "author_email",
        "shell_command",
    }


@pytest.mark.os_agnostic
def test_info_keeps_its_text_without_the_flag(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(["info"], capsys)
    assert code == 0
    assert out.startswith(f"Info for {__init__conf__.name}:")


@pytest.mark.os_agnostic
def test_config_answers_with_the_dict_config_format_json_prints(capsys: pytest.CaptureFixture[str]) -> None:
    """data is what --format json shows, redaction included, whichever display port is wired."""
    data = {"camera": {"password": "secretPW", "url": "http://u:urlPW@192.0.2.9/x", "kind": "http"}, "s": {"k": 1}}
    code = main(["--json-bare", "config"], services_factory=_services(data))
    out = capsys.readouterr().out
    assert code == 0
    document = json.loads(out)
    assert (document["ok"], document["command"]) == (True, "config")
    assert document["data"]["s"] == {"k": 1}
    assert "secretPW" not in out
    assert "urlPW" not in out
    assert document["data"]["camera"]["url"] == "http://u:***REDACTED***@192.0.2.9/x"


@pytest.mark.os_agnostic
def test_config_section_answers_with_that_section_only(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--json", "config", "--section", "s"], services_factory=_services({"s": {"k": 1}, "t": {"k": 2}}))
    document = json.loads(capsys.readouterr().out)
    assert code == 0
    assert document["data"] == {"s": {"k": 1}}


@pytest.mark.os_agnostic
def test_a_missing_config_section_keeps_exit_22_with_an_error_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--json-bare", "config", "--section", "nope"], services_factory=_services({"s": {}}))
    captured = capsys.readouterr()
    assert code == 22
    document = json.loads(captured.out)
    assert (document["ok"], document["command"], document["error"]) == (False, "config", "ValueError")
    assert "nope" in document["message"]
    assert "nope" in captured.err


@pytest.mark.os_agnostic
def test_a_config_that_did_not_load_keeps_exit_78_with_an_error_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    message = "Invalid TOML in /etc/xdg/app/config.toml"
    code, out, err = _run(["--json-bare", "config"], capsys, get_config=_raising(ConfigError(message)))
    assert code == 78
    document = json.loads(out)
    assert (document["command"], document["error"], document["message"]) == ("config", "ConfigError", message)
    assert message in err


@pytest.mark.os_agnostic
def test_config_deploy_answers_with_the_written_paths(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    written = [tmp_path / "config.toml", tmp_path / "config.d" / "50-printer.toml"]

    def deploy(**_kwargs: Any) -> list[Path]:
        return written

    code, out, _ = _run(["--json-bare", "config-deploy", "--target", "user"], capsys, deploy_configuration=deploy)
    assert code == 0
    document = json.loads(out)
    assert (document["ok"], document["command"]) == (True, "config-deploy")
    assert document["data"]["paths"] == [str(path) for path in written]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("error", "code"),
    [(PermissionError("denied"), 13), (RuntimeError("boom"), 1)],
)
def test_a_failed_config_deploy_keeps_its_code_with_an_error_envelope(
    capsys: pytest.CaptureFixture[str], error: Exception, code: int
) -> None:
    got, out, err = _run(["--json", "config-deploy", "--target", "user"], capsys, deploy_configuration=_raising(error))
    assert got == code
    document = json.loads(out)
    assert (document["ok"], document["command"]) == (False, "config-deploy")
    assert document["error"] == type(error).__name__
    assert str(error) in document["message"]
    assert str(error) in err


@pytest.mark.os_agnostic
def test_a_refused_permission_section_keeps_exit_78_with_an_error_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = _run(
        ["--json-bare", "--set", "lib_layered_config.default_permissions=5", "config-deploy", "--target", "user"],
        capsys,
    )
    assert code == 78
    document = json.loads(out)
    assert (document["ok"], document["command"]) == (False, "config-deploy")
    assert "default_permissions" in document["message"]


@pytest.mark.os_agnostic
def test_config_generate_examples_answers_with_the_written_paths(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out, _ = _run(["--json-bare", "config-generate-examples", "--destination", str(tmp_path)], capsys)
    assert code == 0
    document = json.loads(out)
    assert (document["ok"], document["command"]) == (True, "config-generate-examples")
    assert document["data"]["paths"]
    assert all((tmp_path / path).exists() for path in document["data"]["paths"])


@pytest.mark.os_agnostic
def test_a_failed_config_generate_examples_keeps_exit_1_with_an_error_envelope(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A destination below a file cannot be created."""
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    code, out, _ = _run(["--json-bare", "config-generate-examples", "--destination", str(blocker / "sub")], capsys)
    assert code == 1
    document = json.loads(out)
    assert (document["ok"], document["command"]) == (False, "config-generate-examples")


# ======================== An unexpected failure ========================

UNEXPECTED = [OverflowError("cannot convert float infinity to integer"), MemoryError(), IndexError("x"), TypeError("y")]


def _calibrate_set(*flags: str) -> list[str]:
    return [*flags, "calibrate", "set", "--top", "2", "--bottom", "2"]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("error", UNEXPECTED, ids=lambda error: type(error).__name__)
def test_an_unexpected_failure_is_an_error_envelope_and_exit_2(
    error: Exception, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 1 documents a refusal; a caller must not read a crash as one, or find no JSON to parse."""
    code, out, err = _run(_calibrate_set("--json-bare"), capsys, set_printer_values=_raising(error))
    assert code == 2, err
    document = json.loads(out)
    assert document["ok"] is False
    assert document["command"] == "calibrate set"
    assert document["error"] == type(error).__name__
    assert document["message"]
    assert "Traceback" not in err


@pytest.mark.os_agnostic
def test_an_unexpected_failure_says_what_it_was_in_human_mode(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(_calibrate_set(), capsys, set_printer_values=_raising(MemoryError()))
    assert code == 2
    assert out == ""
    assert err.strip() == "Error: MemoryError"


@pytest.mark.os_agnostic
@pytest.mark.usefixtures("managed_traceback_state")
@pytest.mark.parametrize("flags", [("--traceback",), ("--traceback", "--json-bare")], ids=["human", "json"])
def test_traceback_still_shows_where_an_unexpected_failure_came_from(
    flags: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(_calibrate_set(*flags), capsys, set_printer_values=_raising(OverflowError("boom")))
    assert code == 2
    assert "Traceback" in err
    assert "boom" in err
    if "--json-bare" in flags:
        assert json.loads(out)["error"] == "OverflowError"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("raised", "expected"),
    [(click.exceptions.Exit(7), 7), (click.UsageError("bad option"), 2)],
    ids=["exit", "usage"],
)
def test_clicks_own_control_flow_is_not_an_unexpected_failure(
    raised: Exception, expected: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both reach click as they do without the net, or a command could not exit with its own code."""
    code, out, _err = _run(_calibrate_set(), capsys, set_printer_values=_raising(raised))
    assert code == expected
    assert out == ""


@pytest.mark.os_agnostic
def test_ctrl_c_is_not_an_unexpected_failure(capsys: pytest.CaptureFixture[str]) -> None:
    """click turns it into Abort, a RuntimeError; exit 1 would read as "refused, nothing was printed"."""
    code, out, err = _run(_calibrate_set("--json-bare"), capsys, set_printer_values=_raising(KeyboardInterrupt()))
    assert code == 130
    document = json.loads(out)
    assert document["ok"] is False
    assert document["command"] == "calibrate set"
    assert document["error"] == "KeyboardInterrupt"
    assert "interrupted" in err


INTERRUPTED_COMMANDS = [
    (["validate", "x.json"], "validate", False),
    (["config"], "config", False),
    (["--set", "printer.model=allegro-2", "status"], "status", False),
    (["print", "x.json"], "print", True),
    (["verify", "x.json"], "verify", True),
    (["calibrate", "print"], "calibrate print", True),
    (["reset"], "reset", True),
]


def _interrupted() -> AppServices:
    raise KeyboardInterrupt


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("args", "command", "may_have_sent"), INTERRUPTED_COMMANDS)
def test_ctrl_c_names_the_command_and_speaks_of_a_job_only_where_one_can_go(
    capsys: pytest.CaptureFixture[str], args: list[str], command: str, may_have_sent: bool
) -> None:
    code = main(["--json-bare", *args], services_factory=_interrupted)
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert code == 130
    assert document["command"] == command
    assert ("may already have been sent" in document["message"]) is may_have_sent
    assert ("may already have been sent" in captured.err) is may_have_sent


@pytest.mark.os_agnostic
def test_ctrl_c_during_logging_shutdown_keeps_the_answer_and_its_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command is done by then: its code stands, and no traceback follows its answer."""
    label = tmp_path / "ok.json"
    label.write_text(json.dumps({"elements": [{"type": "text", "x": 5, "y": 5, "text": "A"}]}), encoding="utf-8")
    monkeypatch.setattr(lib_log_rich.runtime, "is_initialised", lambda: True)
    monkeypatch.setattr(lib_log_rich.runtime, "shutdown", _raising(KeyboardInterrupt()))
    code, out, err = _run(["--json-bare", "validate", str(label)], capsys)
    # The root conftest shuts logging down after each test: it must meet the real library.
    monkeypatch.undo()
    assert code == 0
    assert json.loads(out)["ok"] is True
    assert "Traceback" not in err


@pytest.mark.os_agnostic
def test_ctrl_c_in_human_mode_says_so_on_stderr_only(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(_calibrate_set(), capsys, set_printer_values=_raising(KeyboardInterrupt()))
    assert code == 130
    assert out == ""
    assert "Error: interrupted" in err
    assert "Abort" not in err


@pytest.mark.os_agnostic
def test_the_end_of_a_prompt_s_input_is_not_an_interrupt(capsys: pytest.CaptureFixture[str]) -> None:
    """click spells it as Abort too, and nothing was interrupted, so it must not read as Ctrl-C."""
    code, _out, _err = _run(_calibrate_set(), capsys, set_printer_values=_raising(EOFError()))
    assert code != 130


@pytest.mark.os_agnostic
def test_a_group_with_no_subcommand_says_so_without_a_stray_exit_line(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["calibrate"], capsys)
    assert (code, out) == (2, "")
    assert "calibrate needs a subcommand" in err
    assert "SystemExit" not in err


@pytest.mark.os_agnostic
def test_no_command_stays_help_for_a_person(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = _run([], capsys)
    assert code == 0
    assert "Usage:" in out


@pytest.mark.os_agnostic
@pytest.mark.parametrize("flag", ["--json", "--json-bare"])
def test_no_command_is_a_usage_error_envelope_for_a_machine(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = _run([flag], capsys)
    assert code == 2
    document = json.loads(out)
    assert (document["ok"], document["command"], document["error"]) == (False, None, "UsageError")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("mode", [[], ["--json"]], ids=["human", "json"])
def test_an_empty_config_section_is_not_found_in_both_modes(
    mode: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    code, _out, err = _run([*mode, "config", "--section", ""], capsys, display_config=display_config)
    assert code == 22
    assert "Section '' not found" in err


#: A venv's launcher on Windows rewrites a lone surrogate in argv to U+FFFD before Python sees it, so
#: a child that must receive one starts from the base interpreter, with this one's import path.
_BASE_PYTHON: str = getattr(sys, "_base_executable", sys.executable)


def _env_for_base_python() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS refuses a file name holding a lone surrogate")
def test_an_undecodable_destination_reaches_the_log_without_a_logging_error(tmp_path: Path) -> None:
    """A real process with real logging: the failure was 112 lines from the logging system at exit 0."""
    done = subprocess.run(
        [sys.executable, "-m", "datamax_dpl", "config-generate-examples", "--destination", "ex\udcff"],
        executable=_BASE_PYTHON,
        capture_output=True,
        timeout=120,
        check=False,
        cwd=tmp_path,
        env=_env_for_base_python(),
    )
    assert done.returncode == 0
    assert b"Logging error" not in done.stderr


def test_an_undecodable_section_reaches_the_log_without_a_logging_error(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "datamax_dpl", "config", "--section", "\udcff\udcfe"],
        executable=_BASE_PYTHON,
        capture_output=True,
        timeout=120,
        check=False,
        cwd=tmp_path,
        env=_env_for_base_python(),
    )
    assert done.returncode == 22
    assert b"Logging error" not in done.stderr


_DEV_FULL = Path("/dev/full")


@pytest.mark.skipif(not _DEV_FULL.exists(), reason="only Linux has a device that refuses every write")
def test_an_answer_that_cannot_be_written_is_a_failure_with_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``validate`` writes its answer through the command's own handling, which sees the full disk first."""
    label = tmp_path / "ok.json"
    label.write_text(json.dumps({"elements": [{"type": "text", "x": 5, "y": 5, "text": "A"}]}), encoding="utf-8")
    with _DEV_FULL.open("w", encoding="utf-8") as full:
        monkeypatch.setattr(sys, "stdout", full)
        code = main(["--json", "validate", str(label)], services_factory=_services())
        monkeypatch.undo()
    err = capsys.readouterr().err
    assert code == 2
    assert err.count("could not write the output") == 1
    assert "OSError" not in err


@pytest.mark.skipif(not _DEV_FULL.exists(), reason="only Linux has a device that refuses every write")
def test_short_output_lost_at_the_final_flush_is_a_failure_with_one_line() -> None:
    """``info`` stays in the stream's buffer until the flush past every command's own handling."""
    with _DEV_FULL.open("wb") as full:
        done = subprocess.run(
            [sys.executable, "-m", "datamax_dpl", "info"], stdout=full, stderr=subprocess.PIPE, timeout=120, check=False
        )
    assert done.returncode == 2
    assert done.stderr.count(b"could not write the output") == 1


@pytest.mark.skipif(not _DEV_FULL.exists(), reason="only Linux has a device that refuses every write")
def test_a_failure_over_the_same_full_disk_says_so_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``render -o -`` fails on its own write; the flush at the end must not report it a second time."""
    label = tmp_path / "ok.json"
    label.write_text(json.dumps({"elements": [{"type": "text", "x": 5, "y": 5, "text": "A"}]}), encoding="utf-8")
    with _DEV_FULL.open("w", encoding="utf-8") as full:
        monkeypatch.setattr(sys, "stdout", full)
        code = main(["render", str(label), "-o", "-"], services_factory=_services())
        monkeypatch.undo()
    err = capsys.readouterr().err
    assert code == 2
    assert err.count("Error:") == 1


@pytest.mark.skipif(sys.platform == "win32", reason="the library reads the mode settings only where POSIX modes apply")
@pytest.mark.usefixtures("no_ambient_configuration")
def test_config_deploy_refuses_a_permission_setting_with_exit_78(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATAMAX_DPL___LIB_LAYERED_CONFIG__DEFAULT_PERMISSIONS__USER_FILE", "400")
    code = main(["--json", "config-deploy", "--target", "user"], services_factory=build_production)
    document = json.loads(capsys.readouterr().out)
    assert code == 78
    assert document["error"] == "DeployPermissionsError"
    assert "user_file" in document["message"]


@pytest.mark.os_agnostic
def test_a_label_rejected_inside_a_command_is_the_answer_no_not_a_failure(capsys: pytest.CaptureFixture[str]) -> None:
    """A use case refuses a label with error findings by raising: exit 1 says so, with a command's envelope."""

    def body(cli_ctx: CLIContext) -> CommandResult:
        raise LabelRejectedError("label has errors; nothing was printed")

    state = CLIContext(
        traceback=False,
        config=Config({}, {}),
        services=build_testing(),
        mode=OutputMode.of(as_json=True, as_json_bare=False),
    )
    with pytest.raises(click.exceptions.Exit) as caught:
        run_command(click.Context(click.Command("print"), obj=state), command=CommandName.PRINT, body=body)
    assert caught.value.exit_code == 1
    assert json.loads(capsys.readouterr().out)["error"] == "LabelRejectedError"


class _LargestWrite(io.StringIO):
    """A stdout that remembers the most text one write handed it: what every layer below copies."""

    def __init__(self) -> None:
        super().__init__()
        self.largest = 0

    def write(self, text: str, /) -> int:
        self.largest = max(self.largest, len(text))
        return super().write(text)


class _Big(BaseModel):
    dpl: str


@pytest.mark.os_agnostic
@pytest.mark.parametrize("bare", [False, True])
def test_a_large_envelope_goes_to_stdout_in_pieces(bare: bool) -> None:
    """A whole batch's job in one write cost several times its size again in the stream's own copies."""
    job = "<STX>L<CR>" * 100_000
    stream = _LargestWrite()
    envelope = Envelope[BaseModel](ok=True, command=CommandName.RENDER, data=_Big(dpl=job), skipped=[])
    try:
        with contextlib.redirect_stdout(stream):
            write_envelope(envelope, mode=OutputMode.of(as_json=not bare, as_json_bare=bare))
    finally:
        forget_started_envelope()
    assert json.loads(stream.getvalue())["data"]["dpl"] == job
    assert stream.getvalue().endswith("\n")
    assert stream.largest <= 64 * 1024, stream.largest
