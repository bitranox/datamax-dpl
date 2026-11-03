"""Values the code once fixed, each now a ``[printer]`` key whose default is what the code did before.

Every key here is tried at a value other than its default, distinct from the other keys' values, so a
key that reached the wrong place fails; and at its default, which must leave the job and the exchange
with the printer as they were.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import TYPE_CHECKING, Any

import pytest
import tomlkit
from lib_layered_config import Config
from printer_server import ImageModules, ModulePrinter

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.label_io import MAX_INPUT_BYTES, MAX_INPUT_BYTES_LIMIT, load_records
from datamax_dpl.adapters.cli.settings import (
    MAX_BATCH_RECORDS_LIMIT,
    PrinterOverrides,
    PrinterSettings,
    printer_settings,
    render_options,
)
from datamax_dpl.adapters.memory import RecordingPrinter
from datamax_dpl.application.use_cases import DIRECTORY_IDLE_SECONDS, render_label
from datamax_dpl.composition import build_testing
from datamax_dpl.domain.errors import ConfigurationError
from datamax_dpl.domain.label import Image, Label, Text
from datamax_dpl.domain.render import job_context
from datamax_dpl.domain.render_context import RenderOptions
from datamax_dpl.domain.reset_label import CALIBRATION_LABEL_TEXT
from datamax_dpl.domain.timing import MAX_ANSWER_SECONDS, MAX_TIMEOUT_SECONDS, TIMEOUT_SECONDS

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner, Result

pytestmark = pytest.mark.usefixtures("no_ambient_configuration")

URI = "tcp://192.0.2.60:9100"
#: What one Allegro 2 put in front of its first answer after a power cycle.
POWER_ON = b"R\x11"
TEXT_LABEL = Label(width=100, height=60, elements=[Text(x=5, y=5, text="Hello")])
_PATTERN = ["".join("#" if (row * 7 + column * 13) % 5 == 0 else "." for column in range(64)) for row in range(64)]
IMAGE_LABEL = Label(width=100, height=60, elements=[Image(x=1, y=1, name="LOGO", bitmap=_PATTERN)])


def _settings(config: dict[str, Any], printer: str | None = None) -> PrinterSettings:
    return printer_settings(Config(config, {}), PrinterOverrides(printer=printer))


def _label_file(tmp_path: Path, label: Label = TEXT_LABEL) -> str:
    path = tmp_path / "label.json"
    path.write_text(json.dumps(label.model_dump(mode="json", exclude_none=True)), encoding="utf-8")
    return str(path)


def _cli(runner: CliRunner, printer: ModulePrinter | None, *args: str) -> Result:
    return runner.invoke(cli_mod.cli, ["--json-bare", *args], obj=lambda: build_testing(printer=printer))


def _codes(result: Result) -> list[str]:
    return [finding["code"] for finding in json.loads(result.stdout)["data"]["findings"]]


def _shipped_printer_toml() -> str:
    shipped = resources.files("datamax_dpl") / "adapters" / "config" / "defaultconfig.d" / "50-printer.toml"
    return shipped.read_text(encoding="utf-8")


# ======================== timeout: a named default ========================


@pytest.mark.os_agnostic
def test_the_timeout_default_is_the_named_constant_and_the_shipped_file_says_the_same() -> None:
    text = _shipped_printer_toml()
    [documented] = re.findall(r"^# Default: (\S+)\n# Environment Variable: DATAMAX_DPL___PRINTER__TIMEOUT=", text, re.M)

    assert PrinterSettings().timeout == TIMEOUT_SECONDS == 10.0
    assert tomlkit.parse(text).unwrap()["printer"]["timeout"] == TIMEOUT_SECONDS
    assert float(documented) == TIMEOUT_SECONDS


# ======================== reset_check: the <SOH>A read before a job ========================


@pytest.mark.os_agnostic
def test_reset_check_defaults_on_and_the_job_asks_for_the_status_once(cli_runner: CliRunner, tmp_path: Path) -> None:
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)

    result = _cli(cli_runner, printer, "print", _label_file(tmp_path), "--model", "allegro-2", "--printer", URI)

    assert PrinterSettings().reset_check is True
    assert result.exit_code == 0, result.output
    assert modules.status_requests == 1
    assert "printer_reset_calibrated" in _codes(result)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("setting", "printer_arg"),
    [
        (["--set", "printer.reset_check=false"], URI),
        (["--set", f"printers.lager.uri={URI}", "--set", "printers.lager.reset_check=false"], "lager"),
    ],
)
def test_reset_check_off_asks_nothing_and_sends_the_job_alone(
    cli_runner: CliRunner, tmp_path: Path, setting: list[str], printer_arg: str
) -> None:
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)
    args = [*setting, "print", _label_file(tmp_path), "--model", "allegro-2", "--printer", printer_arg]

    result = _cli(cli_runner, printer, *args)

    assert result.exit_code == 0, result.output
    assert modules.status_requests == 0
    assert CALIBRATION_LABEL_TEXT.encode("ascii") not in printer.data
    assert not [code for code in _codes(result) if code.startswith("printer_reset")]


@pytest.mark.os_agnostic
def test_reset_check_off_leaves_a_report_a_names_read_carries_unacted_on(cli_runner: CliRunner, tmp_path: Path) -> None:
    modules = ImageModules(reset=POWER_ON)
    printer = ModulePrinter(modules)
    settings = ["--set", "printer.reset_check=false", "--set", "printer.clear_graphics_before=false"]
    args = [*settings, "print", _label_file(tmp_path, IMAGE_LABEL), "--model", "allegro-2", "--printer", URI]

    result = _cli(cli_runner, printer, *args)

    assert result.exit_code == 0, result.output
    assert (modules.status_requests, modules.requests) == (0, 2)
    assert CALIBRATION_LABEL_TEXT.encode("ascii") not in printer.data
    assert not [code for code in _codes(result) if code.startswith("printer_reset")]


@pytest.mark.os_agnostic
def test_reset_check_takes_only_a_boolean_and_names_its_key() -> None:
    with pytest.raises(ConfigurationError, match=r"^printer\.reset_check: Input should be a valid boolean"):
        _settings({"printer": {"reset_check": "sometimes"}})


# ======================== upload_check_rounds = 0: no read after the upload ========================


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("rounds", "requests", "lost_image_printed"), [(None, 2, False), ("0", 0, True)])
def test_upload_check_rounds_0_reaches_the_print_and_reads_nothing_after_the_upload(
    cli_runner: CliRunner, tmp_path: Path, rounds: str | None, requests: int, lost_image_printed: bool
) -> None:
    """The default reads the lost upload, clears and uploads again; 0 sends the format after the lost one."""
    modules = ImageModules(lose={0})
    printer = ModulePrinter(modules)
    setting = [] if rounds is None else ["--set", f"printer.upload_check_rounds={rounds}"]
    args = [*setting, "print", _label_file(tmp_path, IMAGE_LABEL), "--model", "allegro-2", "--printer", URI]

    result = _cli(cli_runner, printer, *args)

    assert result.exit_code == 0, result.output
    assert modules.requests == requests
    assert bool(modules.placed_missing) is lost_image_printed


# ======================== directory_idle and directory_read_limit: the <STX>W reads ========================

#: Answers a printer gives the three directory requests of ``memory --kind all``.
_DIRECTORIES = {
    b"\x02WF": b"No Modules Available\r",
    b"\x02WG": b"MODULE: A\rAVAILABLE BYTES IN MODULE:00049083\r",
    b"\x02WL": b"MODULE: C\rSHIP\r",
}


class _ReadsRecorded(ModulePrinter):
    """Keeps the silence and the overall bound each directory read was given: the transport port's own arguments."""

    def __init__(self, modules: ImageModules) -> None:
        super().__init__(modules)
        self.reads: list[tuple[float, float]] = []

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = MAX_ANSWER_SECONDS) -> bytes:
        self.reads.append((idle, total))
        return super().read_answer(request, timeout=timeout, idle=idle, total=total)


class _MemoryReadsRecorded(RecordingPrinter):
    """A printer answering the three directories, keeping the silence and the bound of each read."""

    def __init__(self) -> None:
        super().__init__(answers=_DIRECTORIES)
        self.reads: list[tuple[float, float]] = []

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = MAX_ANSWER_SECONDS) -> bytes:
        self.reads.append((idle, total))
        return super().read_answer(request, timeout=timeout, idle=idle, total=total)


def _print_reads(
    cli_runner: CliRunner, tmp_path: Path, *setting: str, printer_arg: str = URI
) -> list[tuple[float, float]]:
    """The reads of a print whose names are read before its downloads and once after them."""
    printer = _ReadsRecorded(ImageModules())
    args = [*setting, "--set", "printer.clear_graphics_before=false", "print", _label_file(tmp_path, IMAGE_LABEL)]
    result = _cli(cli_runner, printer, *args, "--model", "allegro-2", "--printer", printer_arg)
    assert result.exit_code == 0, result.output
    assert len(printer.reads) == 2
    return printer.reads


def _memory_reads(cli_runner: CliRunner, *args: str) -> list[tuple[float, float]]:
    printer = _MemoryReadsRecorded()
    result = cli_runner.invoke(
        cli_mod.cli, [*args, "--kind", "all", "--printer", URI], obj=lambda: build_testing(printer=printer)
    )
    assert result.exit_code == 0, result.output
    assert len(printer.reads) == 3
    return printer.reads


@pytest.mark.os_agnostic
def test_the_directory_keys_default_to_the_values_the_code_fixed() -> None:
    settings = PrinterSettings()
    assert (settings.directory_idle, settings.directory_read_limit) == (DIRECTORY_IDLE_SECONDS, MAX_ANSWER_SECONDS)
    assert (DIRECTORY_IDLE_SECONDS, MAX_ANSWER_SECONDS) == (1.0, 120.0)


@pytest.mark.os_agnostic
def test_by_default_every_print_names_read_ends_on_one_second_of_silence(cli_runner: CliRunner, tmp_path: Path) -> None:
    assert [idle for idle, _ in _print_reads(cli_runner, tmp_path)] == [1.0, 1.0]


@pytest.mark.os_agnostic
@pytest.mark.parametrize("section", ["printer", "printers.lager"])
def test_directory_idle_reaches_every_names_read_of_print(cli_runner: CliRunner, tmp_path: Path, section: str) -> None:
    named = ["--set", f"printers.lager.uri={URI}"] if section != "printer" else []
    printer_arg = URI if section == "printer" else "lager"
    reads = _print_reads(
        cli_runner, tmp_path, *named, "--set", f"{section}.directory_idle=0.4", printer_arg=printer_arg
    )
    assert [idle for idle, _ in reads] == [0.4, 0.4]


@pytest.mark.os_agnostic
def test_the_print_names_reads_keep_their_wait_bound_whatever_directory_read_limit_says(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """A names read ends within stored_names_wait plus the silence; directory_read_limit bounds memory only."""
    reads = _print_reads(
        cli_runner, tmp_path, "--set", "printer.directory_read_limit=250", "--set", "printer.stored_names_wait=7"
    )
    assert all(total <= 7.0 + 1.0 for _, total in reads), reads


@pytest.mark.os_agnostic
def test_memory_reads_with_the_defaults_end_on_one_second_and_are_bounded_by_120_s(cli_runner: CliRunner) -> None:
    assert _memory_reads(cli_runner, "memory") == [(1.0, 120.0)] * 3


@pytest.mark.os_agnostic
def test_memory_takes_directory_idle_and_directory_read_limit_for_every_kind(cli_runner: CliRunner) -> None:
    keys = ["--set", "printer.directory_idle=0.4", "--set", "printer.directory_read_limit=250"]
    assert _memory_reads(cli_runner, *keys, "memory") == [(0.4, 250.0)] * 3


@pytest.mark.os_agnostic
def test_memory_idle_on_the_command_line_wins_over_directory_idle(cli_runner: CliRunner) -> None:
    keys = ["--set", "printer.directory_idle=0.4", "--set", "printer.directory_read_limit=250"]
    assert _memory_reads(cli_runner, *keys, "memory", "--idle", "0.7") == [(0.7, 250.0)] * 3


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", ["directory_idle", "directory_read_limit"])
@pytest.mark.parametrize(
    ("value", "reason"),
    [(0, "greater than 0"), (-1, "greater than 0"), (MAX_TIMEOUT_SECONDS + 1, "less than or equal to 2147483")],
)
def test_a_directory_key_out_of_range_is_refused_naming_it(key: str, value: float, reason: str) -> None:
    with pytest.raises(ConfigurationError, match=rf"^printer\.{key}: Input should be {reason}"):
        _settings({"printer": {key: value}})


@pytest.mark.os_agnostic
@pytest.mark.parametrize("key", ["directory_idle", "directory_read_limit"])
def test_a_directory_key_refuses_a_boolean(key: str) -> None:
    with pytest.raises(ConfigurationError, match=rf"^printer\.{key}: needs a number, got true"):
        _settings({"printer": {key: True}})


# ======================== max_input_bytes: the largest label or data file ========================

#: A label file of 60-odd bytes, padded with whitespace JSON allows to any length.
_SMALL_LABEL = b'{"elements": [{"type": "text", "x": 1, "y": 1, "text": "A"}]}'


def _padded(tmp_path: Path, name: str, content: bytes, size: int) -> str:
    path = tmp_path / name
    path.write_bytes(content + b" " * (size - len(content)))
    return str(path)


def _input_error(result: Result) -> str:
    assert result.exit_code == 2, result.output
    document = json.loads(result.stdout)
    assert document["error"] == "LabelInputError"
    message: str = document["message"]
    return message


@pytest.mark.os_agnostic
def test_max_input_bytes_defaults_to_16_mib_and_a_file_one_past_it_is_refused(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    label = _padded(tmp_path, "label.json", _SMALL_LABEL, MAX_INPUT_BYTES + 1)

    message = _input_error(_cli(cli_runner, None, "validate", label))

    assert PrinterSettings().max_input_bytes == MAX_INPUT_BYTES == 16 * 1024 * 1024
    assert message == f"{label} is larger than {MAX_INPUT_BYTES} bytes; a larger file needs [printer] max_input_bytes"


@pytest.mark.os_agnostic
def test_a_larger_max_input_bytes_reads_a_file_past_16_mib(cli_runner: CliRunner, tmp_path: Path) -> None:
    label = _padded(tmp_path, "label.json", _SMALL_LABEL, MAX_INPUT_BYTES + 1)

    result = _cli(cli_runner, None, "--set", f"printer.max_input_bytes={MAX_INPUT_BYTES + 1}", "validate", label)

    assert result.exit_code == 0, result.output


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("section", "printer"), [("printer", []), ("printers.lager", ["--printer", "lager"])], ids=["printer", "named"]
)
def test_a_smaller_max_input_bytes_refuses_a_label_and_names_the_key_of_the_section_in_use(
    cli_runner: CliRunner, tmp_path: Path, section: str, printer: list[str]
) -> None:
    label = _padded(tmp_path, "label.json", _SMALL_LABEL, 101)
    named = ["--set", f"printers.lager.uri={URI}"]

    message = _input_error(
        _cli(cli_runner, None, *named, "--set", f"{section}.max_input_bytes=100", "validate", label, *printer)
    )
    passed = _cli(cli_runner, None, *named, "--set", f"{section}.max_input_bytes=101", "validate", label, *printer)

    assert message.endswith(f"is larger than 100 bytes; a larger file needs [{section}] max_input_bytes")
    assert passed.exit_code == 0, passed.output


@pytest.mark.os_agnostic
def test_max_input_bytes_bounds_the_data_file_too(cli_runner: CliRunner, tmp_path: Path) -> None:
    label = _padded(tmp_path, "label.json", _SMALL_LABEL, len(_SMALL_LABEL))
    data = _padded(tmp_path, "data.csv", b"n\n1\n2\n", 300)

    message = _input_error(
        _cli(cli_runner, None, "--set", "printer.max_input_bytes=200", "validate", label, "--data", data)
    )

    assert message == f"{data} is larger than 200 bytes; a larger file needs [printer] max_input_bytes"


@pytest.mark.os_agnostic
@pytest.mark.parametrize("command", ["render", "explain", "preview"])
def test_every_command_reading_a_file_takes_max_input_bytes(
    cli_runner: CliRunner, tmp_path: Path, command: str
) -> None:
    source = _padded(tmp_path, "input.json", _SMALL_LABEL, 101)
    extra = ["-o", str(tmp_path / "out.png")] if command == "preview" else []

    message = _input_error(_cli(cli_runner, None, "--set", "printer.max_input_bytes=100", command, source, *extra))

    assert message.endswith("is larger than 100 bytes; a larger file needs [printer] max_input_bytes")


@pytest.mark.os_agnostic
def test_max_input_bytes_takes_its_largest_value_and_no_more() -> None:
    assert _settings({"printer": {"max_input_bytes": MAX_INPUT_BYTES_LIMIT}}).max_input_bytes == MAX_INPUT_BYTES_LIMIT
    with pytest.raises(ConfigurationError, match=r"^printer\.max_input_bytes: Input should be less than or equal to"):
        _settings({"printer": {"max_input_bytes": MAX_INPUT_BYTES_LIMIT + 1}})
    with pytest.raises(ConfigurationError, match=r"^printer\.max_input_bytes: Input should be greater than 0"):
        _settings({"printer": {"max_input_bytes": 0}})


@pytest.mark.os_agnostic
def test_max_batch_records_ceiling_still_means_no_limit_at_the_largest_input(tmp_path: Path) -> None:
    """The smallest CSV record is one character and a line end, so no file within the ceiling holds more records."""
    path = tmp_path / "tiny.csv"
    path.write_bytes(b"n\n" + b"x\n" * 1000)

    smallest_record = (path.stat().st_size - len(b"n\n")) // len(load_records(str(path)))

    assert smallest_record == 2
    assert MAX_BATCH_RECORDS_LIMIT * smallest_record >= MAX_INPUT_BYTES_LIMIT
    assert _settings({"printer": {"max_batch_records": MAX_BATCH_RECORDS_LIMIT}}).max_batch_records == (
        MAX_BATCH_RECORDS_LIMIT
    )


# ======================== inch_unit_dots: the whole dots one 1/100 in prints as ========================


def _options(config: dict[str, Any], printer: str | None = None) -> RenderOptions:
    return render_options(_settings(config, printer))


@pytest.mark.os_agnostic
def test_inch_unit_dots_unset_takes_the_models_value() -> None:
    assert PrinterSettings().inch_unit_dots is None
    assert _options({}).inch_unit_dots is None
    assert job_context(TEXT_LABEL, _options({"printer": {"model": "allegro-2"}})).dots_per_unit == 2.0
    assert job_context(TEXT_LABEL, RenderOptions(model="allegro-2")).dots_per_unit == 2.0


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("config", "printer"),
    [({"printer": {"inch_unit_dots": 3}}, None), ({"printers": {"lager": {"inch_unit_dots": 3}}}, "lager")],
)
def test_a_configured_inch_unit_dots_wins_over_the_models_and_moves_every_distance(
    config: dict[str, Any], printer: str | None
) -> None:
    allegro = {**config, "printer": {**config.get("printer", {}), "model": "allegro-2"}}
    options = _options(allegro, printer)
    measured = _options({"printer": {"model": "allegro-2"}})

    assert options.inch_unit_dots == 3
    assert job_context(TEXT_LABEL, options).dots_per_unit == 3.0
    assert render_label(TEXT_LABEL, options).data != render_label(TEXT_LABEL, measured).data


@pytest.mark.os_agnostic
def test_the_library_sets_inch_unit_dots_on_the_render_options() -> None:
    label = Label(elements=[Text(x=10, y=10, text="A")])
    two, three = (render_label(label, RenderOptions(model="allegro-2", inch_unit_dots=n)).data for n in (2, 3))

    assert two == render_label(label, RenderOptions(model="allegro-2")).data
    # 10 mm is 39.37/100 in: at 2 dots a unit that is 101.5 units per inch, at 3 it is 67.67.
    assert b"0040" in two and b"0027" in three


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("value", "reason"), [(0, "greater than 0"), (-2, "greater than 0"), (True, "needs a number")])
def test_inch_unit_dots_out_of_range_is_refused_naming_it(value: object, reason: str) -> None:
    with pytest.raises(ConfigurationError, match=rf"^printer\.inch_unit_dots: .*{reason}"):
        _settings({"printer": {"inch_unit_dots": value}})
