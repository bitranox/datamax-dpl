"""``render -o FILE`` on a batch: what the target is before, during and after, on every way it can go.

The failure routes run under a real file size limit in a process of their own, since nothing else
fails a write half way through without filling the disk.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import sys
from typing import TYPE_CHECKING

import pytest
from cli_process import run_cli, run_cli_text

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.adapters.cli.interrupt import noting_interrupts
from datamax_dpl.adapters.cli.job_file import write_batch
from datamax_dpl.adapters.cli.job_spool import FileJobSpool
from datamax_dpl.application.use_cases import check_batch
from datamax_dpl.composition import build_production
from datamax_dpl.domain.label import Label, Qr, Text
from datamax_dpl.domain.render_context import RenderOptions

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner, Result

    from datamax_dpl.application.ports import JobSpool
    from datamax_dpl.application.use_cases import BatchEntry, BatchReport

_TEXT = Label(elements=[Text(x=10, y=10, text="{a}")])
_QR = Label(elements=[Qr(x=1, y=1, data="{a}"), Text(x=10, y=10, text="n")])
_DMX = RenderOptions(model="dmx-600")
_TOO_LONG_FOR_A_QR_CODE = "A" * 4000
_TOO_LONG_FOR_A_TEXT_FIELD = "B" * 300
_NOT_POSIX = sys.platform == "win32"
_IS_ROOT = sys.platform != "win32" and os.geteuid() == 0
_BEFORE = b"before"


def _files(tmp_path: Path, label: Label, rows: str) -> tuple[str, str]:
    label_path = tmp_path / "label.json"
    label_path.write_text(label.model_dump_json(), encoding="utf-8")
    data_path = tmp_path / "data.csv"
    data_path.write_bytes(f"a\n{rows}".encode())
    return str(label_path), str(data_path)


def _run(cli_runner: CliRunner, *args: str) -> Result:
    return cli_runner.invoke(cli_mod.cli, ["--json-bare", *args], obj=build_production)


def _render(cli_runner: CliRunner, label: str, data: str | None, output: object) -> Result:
    batch = () if data is None else ("--data", data)
    return _run(cli_runner, "render", label, *batch, "--model", "dmx-600", "-o", str(output))


def _names(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def _make_read_only(path: Path) -> None:
    path.chmod(stat.S_IREAD)


# ======================== the temp file ========================


@pytest.mark.os_posix
@pytest.mark.skipif(_NOT_POSIX, reason="Windows has no group or other bits for a private file to lose")
def test_a_private_target_s_job_is_never_readable_to_others(tmp_path: Path) -> None:
    target = tmp_path / "job.dpl"
    target.write_bytes(_BEFORE)
    target.chmod(0o600)
    seen: list[int] = []

    def check(*, each: Callable[[BatchEntry], None] | None = None, spool: JobSpool | None = None) -> BatchReport:
        assert each is not None
        assert spool is None
        write = each

        def each_record(entry: BatchEntry) -> None:
            write(entry)
            temp = next(path for path in tmp_path.iterdir() if path.name.endswith(".tmp"))
            seen.append(stat.S_IMODE(temp.stat().st_mode))

        return check_batch(_TEXT, [{"a": "r0"}, {"a": "r1"}], _DMX, each=each_record)

    assert write_batch(str(target), check, open_spool=FileJobSpool).ok
    assert seen == [0o600, 0o600]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.os_posix
@pytest.mark.skipif(_NOT_POSIX, reason="Windows has no umask for a new file's mode to follow")
def test_a_new_target_gets_the_mode_the_umask_gives(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _TEXT, "r0\n")
    target = tmp_path / "job.dpl"
    before = os.umask(0o027)
    try:
        assert _render(cli_runner, label, data, target).exit_code == 0
    finally:
        os.umask(before)
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


@pytest.mark.os_posix
@pytest.mark.skipif(_NOT_POSIX, reason="Windows has no umask to narrow a mode")
def test_a_narrow_umask_does_not_narrow_the_mode_the_target_had(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _TEXT, "r0\n")
    target = tmp_path / "job.dpl"
    target.write_bytes(_BEFORE)
    target.chmod(0o664)
    before = os.umask(0o077)
    try:
        assert _render(cli_runner, label, data, target).exit_code == 0
    finally:
        os.umask(before)
    assert stat.S_IMODE(target.stat().st_mode) == 0o664


# ======================== a target that must not be replaced ========================


@pytest.mark.os_agnostic
@pytest.mark.skipif(_IS_ROOT, reason="root may write a read-only file, so nothing refuses it")
@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
def test_a_read_only_target_is_refused_on_both_routes(cli_runner: CliRunner, tmp_path: Path, *, batch: bool) -> None:
    """The same flag gives the same answer, on Windows with no temp left behind read-only either."""
    label, data = _files(tmp_path, _TEXT, "r0\n")
    target = tmp_path / "job.dpl"
    target.write_bytes(_BEFORE)
    _make_read_only(target)
    try:
        result = _render(cli_runner, label, data if batch else None, target)
        assert result.exit_code == 2, result.output
        assert target.read_bytes() == _BEFORE
        assert _names(tmp_path) == ["data.csv", "job.dpl", "label.json"]
    finally:
        target.chmod(stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.os_agnostic
def test_a_hard_linked_target_keeps_its_other_name(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _TEXT, "r0\n")
    target = tmp_path / "job.dpl"
    target.write_bytes(_BEFORE)
    other = tmp_path / "other.dpl"
    try:
        os.link(target, other)
    except OSError as exc:
        pytest.skip(f"this file system refuses hard links: {exc}")
    assert _render(cli_runner, label, data, target).exit_code == 0
    assert target.read_bytes() == other.read_bytes() != _BEFORE
    assert target.stat().st_nlink == 2


@pytest.mark.os_posix
@pytest.mark.skipif(not _IS_ROOT, reason="only root can give a file to another user")
def test_a_target_another_user_owns_stays_theirs(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _TEXT, "r0\n")
    target = tmp_path / "job.dpl"
    target.write_bytes(_BEFORE)
    if sys.platform == "win32":
        pytest.skip("Windows gives no file to another user by id")
    os.chown(target, 1, 1)
    assert _render(cli_runner, label, data, target).exit_code == 0
    assert target.stat().st_uid == 1
    assert target.read_bytes() != _BEFORE


@pytest.mark.os_agnostic
def test_a_device_takes_the_job_with_nothing_left_beside_it(cli_runner: CliRunner, tmp_path: Path) -> None:
    label, data = _files(tmp_path, _TEXT, "r0\n")
    assert _render(cli_runner, label, data, os.devnull).exit_code == 0
    assert _names(tmp_path) == ["data.csv", "label.json"]


# ======================== the path as given ========================


def _piped(args: list[str], *, tmp_path: Path, pass_fds: tuple[int, ...] = ()) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path), "HOME": str(tmp_path)}
    return run_cli(args, env=env, pass_fds=pass_fds)


@pytest.mark.os_posix
@pytest.mark.skipif(_NOT_POSIX, reason="Windows has no /dev/stdout")
def test_dev_stdout_on_a_pipe_takes_a_batch_as_it_takes_a_single_label(tmp_path: Path) -> None:
    label, data = _files(tmp_path, _TEXT, "r0\nr1\n")
    common = ["render", label, "--model", "dmx-600", "-o"]
    single = _piped([*common, "/dev/stdout"], tmp_path=tmp_path)
    assert single.returncode == 0, single.stderr
    batch = _piped([*common, "/dev/stdout", "--data", data], tmp_path=tmp_path)
    assert batch.returncode == 0, batch.stderr
    assert batch.stdout.count(b"r0") == 1
    assert batch.stdout.count(b"r1") == 1


@pytest.mark.os_posix
@pytest.mark.skipif(_NOT_POSIX, reason="Windows has no /dev/fd")
def test_a_descriptor_path_on_a_pipe_takes_a_batch(tmp_path: Path) -> None:
    label, data = _files(tmp_path, _TEXT, "r0\n")
    reader, writer = os.pipe()
    try:
        output = f"/dev/fd/{writer}"
        args = ["render", label, "--model", "dmx-600", "-o", output, "--data", data]
        done = _piped(args, tmp_path=tmp_path, pass_fds=(writer,))
        assert done.returncode == 0, done.stderr
        os.close(writer)
        writer = -1
        with os.fdopen(reader, "rb") as stream:
            reader = -1
            assert b"r0" in stream.read()
    finally:
        for descriptor in (reader, writer):
            if descriptor >= 0:
                os.close(descriptor)


# ======================== failures on the way ========================

#: Bytes one file may reach in the limited process: under what a few records of a job come to.
_FILE_SIZE_LIMIT = 4096
_LIMITED = """
import resource, signal, sys
from datamax_dpl.adapters.cli.main import main
from datamax_dpl.composition import build_production
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, ({limit}, {limit}))
sys.exit(main(sys.argv[1:], services_factory=build_production))
"""


def _limited(args: list[str], *, tmp_path: Path, temp: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the CLI where a file stops growing at the limit, so a write fails as on a full disk.

    ``temp`` is the temp directory it is given, where a batch's spool goes; None keeps this one's.
    """
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path), "HOME": str(tmp_path)}
    if temp is not None:
        env.update(TMPDIR=str(temp), TEMP=str(temp), TMP=str(temp))
    return run_cli_text(args, entry=("-c", _LIMITED.format(limit=_FILE_SIZE_LIMIT)), env=env)


def _good_rows(count: int = 400) -> str:
    return "".join(f"r{number}\n" for number in range(count))


@pytest.mark.os_posix
@pytest.mark.skipif(_NOT_POSIX, reason="RLIMIT_FSIZE is POSIX")
def test_a_write_that_fails_leaves_the_target_and_names_it(tmp_path: Path) -> None:
    label, data = _files(tmp_path, _TEXT, _good_rows())
    target = tmp_path / "job.dpl"
    target.write_bytes(_BEFORE)
    done = _limited(["render", label, "--data", data, "--model", "dmx-600", "-o", str(target)], tmp_path=tmp_path)
    assert done.returncode == 2, done.stderr
    assert target.read_bytes() == _BEFORE
    assert _names(tmp_path) == ["data.csv", "job.dpl", "label.json"]
    assert str(target) in done.stderr
    assert ".tmp" not in done.stderr


@pytest.mark.os_posix
@pytest.mark.skipif(_NOT_POSIX, reason="RLIMIT_FSIZE is POSIX")
@pytest.mark.parametrize(
    "target", [pytest.param(("-o", "-"), id="render-stdout"), pytest.param(("--printer", "file:{out}"), id="print")]
)
def test_a_spool_that_fills_the_disk_is_removed_and_named(tmp_path: Path, target: tuple[str, str]) -> None:
    temp = tmp_path / "temp"
    temp.mkdir()
    label, data = _files(tmp_path, _TEXT, _good_rows())
    out = tmp_path / "job.dpl"
    verb = "print" if target[0] == "--printer" else "render"
    args = [verb, label, "--data", data, "--model", "dmx-600", target[0], target[1].format(out=out)]
    done = _limited(args, tmp_path=tmp_path, temp=temp)
    assert done.returncode == 2, done.stderr
    assert "File too large, writing the batch's spool: " + str(temp) in done.stderr
    assert _names(temp) == []
    assert not out.exists()


@pytest.mark.os_posix
@pytest.mark.skipif(_NOT_POSIX, reason="RLIMIT_FSIZE is POSIX")
def test_a_bad_record_outranks_a_spool_that_fills_the_disk(tmp_path: Path) -> None:
    """The check goes on past the spool's failure, so a refused batch is told why, not that the disk filled."""
    temp = tmp_path / "temp"
    temp.mkdir()
    label, data = _files(tmp_path, _QR, _good_rows() + f"{_TOO_LONG_FOR_A_QR_CODE}\n")
    args = ["render", label, "--data", data, "--model", "allegro-2", "-o", "-"]
    done = _limited(args, tmp_path=tmp_path, temp=temp)
    assert done.returncode == 1, done.stderr
    assert "qr_data_too_long" in done.stderr
    assert "File too large" not in done.stderr
    assert done.stdout == ""
    assert _names(temp) == []


@pytest.mark.os_posix
@pytest.mark.skipif(_NOT_POSIX, reason="RLIMIT_FSIZE is POSIX")
def test_a_bad_record_outranks_a_full_disk(tmp_path: Path) -> None:
    """The bytes that would not go are flushed again at the close, which must not replace the verdict."""
    label, data = _files(tmp_path, _QR, _good_rows() + f"{_TOO_LONG_FOR_A_QR_CODE}\n")
    target = tmp_path / "job.dpl"
    args = ["--json-bare", "render", label, "--data", data, "--model", "allegro-2", "-o", str(target)]
    done = _limited(args, tmp_path=tmp_path)
    assert done.returncode == 1, done.stderr
    assert json.loads(done.stdout)["data"]["findings_by_code"]["qr_data_too_long"] == 1
    assert _names(tmp_path) == ["data.csv", "label.json"]


@pytest.mark.os_posix
@pytest.mark.skipif(_NOT_POSIX, reason="RLIMIT_FSIZE is POSIX")
def test_an_exception_on_its_way_out_outranks_a_full_disk(tmp_path: Path) -> None:
    label, data = _files(tmp_path, _TEXT, _good_rows() + f"{_TOO_LONG_FOR_A_TEXT_FIELD}\n")
    target = tmp_path / "job.dpl"
    done = _limited(["render", label, "--data", data, "--model", "dmx-600", "-o", str(target)], tmp_path=tmp_path)
    assert done.returncode == 2, done.stderr
    assert "at most 255 characters" in done.stderr
    assert "File too large" not in done.stderr
    assert _names(tmp_path) == ["data.csv", "label.json"]


@pytest.mark.os_agnostic
def test_a_ctrl_c_lost_in_the_last_record_replaces_nothing(tmp_path: Path) -> None:
    """The last record is filled after the pass over the records has looked for the last time."""
    target = tmp_path / "job.dpl"
    target.write_bytes(_BEFORE)

    def check(*, each: Callable[[BatchEntry], None] | None = None, spool: JobSpool | None = None) -> BatchReport:
        report = check_batch(_TEXT, [{"a": "r0"}], _DMX, each=each, spool=spool)
        _lose_an_interrupt()
        return report

    with noting_interrupts(), pytest.raises(KeyboardInterrupt):
        write_batch(str(target), check, open_spool=FileJobSpool)
    assert target.read_bytes() == _BEFORE
    assert _names(tmp_path) == ["job.dpl"]


def _lose_an_interrupt() -> None:
    handler = signal.getsignal(signal.SIGINT)
    assert callable(handler)
    try:
        handler(signal.SIGINT, None)
    except KeyboardInterrupt:
        return


# ======================== the direct route ========================


@pytest.mark.os_posix
@pytest.mark.skipif(_NOT_POSIX, reason="Windows has no directory mode that refuses a new file")
@pytest.mark.skipif(_IS_ROOT, reason="root may add a file to a read-only directory")
def test_a_directory_that_takes_no_new_file_still_gets_its_file_checked_first(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    good = tmp_path / "good"
    good.mkdir()
    label, data = _files(good, _TEXT, "r0\n")
    qr_label, bad_data = _files(good, _QR, f"r0\n{_TOO_LONG_FOR_A_QR_CODE}\n")
    ro = tmp_path / "ro"
    ro.mkdir()
    target = ro / "job.dpl"
    target.write_bytes(_BEFORE)
    ro.chmod(0o555)
    try:
        refused = _run(cli_runner, "render", qr_label, "--data", bad_data, "--model", "allegro-2", "-o", str(target))
        assert refused.exit_code == 1, refused.output
        assert target.read_bytes() == _BEFORE
        label, data = _files(good, _TEXT, "r0\n")
        written = _render(cli_runner, label, data, target)
        assert written.exit_code == 0, written.output
        assert target.read_bytes() != _BEFORE
        new_name = _render(cli_runner, label, data, ro / "new.dpl")
        assert new_name.exit_code == 2, new_name.output
        assert "new.dpl" in json.loads(new_name.stdout)["message"]
    finally:
        ro.chmod(0o755)
