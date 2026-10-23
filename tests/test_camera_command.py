"""Command camera: run a real capture program (a tiny Python script) and collect its image."""

from __future__ import annotations

import sys
import time
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from datamax_dpl.adapters.camera import capture_image
from datamax_dpl.adapters.camera.command import STDERR_TAIL_CHARS, capture_with_command
from datamax_dpl.domain.camera import CameraKind, CameraSettings
from datamax_dpl.domain.errors import CameraError

if TYPE_CHECKING:
    from pathlib import Path

WRITE_IMAGE = "import sys; open(sys.argv[1], 'wb').write(b'\\xff\\xd8\\xff-image')"


def _settings(*argv: str, timeout: float = 10.0) -> CameraSettings:
    return CameraSettings(kind=CameraKind.COMMAND, command=list(argv), timeout=timeout)


@pytest.mark.os_agnostic
def test_capture_image_runs_the_program_and_returns_a_jpeg(tmp_path: Path) -> None:
    """The placeholder becomes a .jpg path in output_dir that the program writes to."""
    image = capture_image(_settings(sys.executable, "-c", WRITE_IMAGE, "{output}"), output_dir=tmp_path / "out")

    assert image.path.parent == tmp_path / "out"
    assert image.path.name.startswith("capture-")
    assert image.path.suffix == ".jpg"
    assert image.media_type == "image/jpeg"
    assert image.path.read_bytes() == b"\xff\xd8\xff-image"
    assert image.captured_at.utcoffset() == timedelta(0)


@pytest.mark.os_agnostic
def test_a_png_suffix_after_the_placeholder_makes_a_png(tmp_path: Path) -> None:
    """``{output}.png`` names the extension and is replaced by the whole path, suffix included."""
    image = capture_image(_settings(sys.executable, "-c", WRITE_IMAGE, "{output}.PNG"), output_dir=tmp_path)

    assert image.path.suffix == ".png"
    assert image.media_type == "image/png"
    assert image.path.is_file()


@pytest.mark.os_agnostic
def test_the_placeholder_is_replaced_inside_a_larger_argument(tmp_path: Path) -> None:
    """``--out={output}`` hands the program the path after the equals sign."""
    script = "import sys; open(sys.argv[1].split('=', 1)[1], 'wb').write(b'x')"
    path = capture_with_command(_settings(sys.executable, "-c", script, "--out={output}"), stem=tmp_path / "shot")

    assert path == tmp_path / "shot.jpg"
    assert path.read_bytes() == b"x"


@pytest.mark.os_agnostic
def test_a_non_zero_exit_carries_the_stderr_tail(tmp_path: Path) -> None:
    """The error names the exit status and ends with the last part of stderr."""
    script = "import sys; sys.stderr.write('x' * 2000 + 'camera busy'); sys.exit(3)"

    with pytest.raises(CameraError) as caught:
        capture_with_command(_settings(sys.executable, "-c", script, "{output}"), stem=tmp_path / "shot")

    message = str(caught.value)
    assert "status 3" in message
    assert message.endswith("camera busy")
    assert len(message) < STDERR_TAIL_CHARS + 200


@pytest.mark.os_agnostic
def test_undecodable_stderr_is_replaced_not_raised(tmp_path: Path) -> None:
    """Stderr that is not UTF-8 still produces a CameraError, with replacement characters."""
    script = "import sys; sys.stderr.buffer.write(b'bad \\xff byte'); sys.exit(1)"

    with pytest.raises(CameraError, match=f"bad {chr(0xFFFD)} byte"):
        capture_with_command(_settings(sys.executable, "-c", script, "{output}"), stem=tmp_path / "shot")


@pytest.mark.os_agnostic
def test_a_program_that_runs_too_long_times_out(tmp_path: Path) -> None:
    """The timeout from the settings ends the run with a CameraError, well before the sleep ends."""
    started = time.monotonic()

    with pytest.raises(CameraError, match="timed out"):
        capture_with_command(
            _settings(sys.executable, "-c", "import time; time.sleep(30)", "{output}", timeout=0.3),
            stem=tmp_path / "shot",
        )

    assert time.monotonic() - started < 5


@pytest.mark.os_agnostic
def test_a_missing_program_is_named(tmp_path: Path) -> None:
    """A program not on PATH is reported by name."""
    with pytest.raises(CameraError, match="not found: 'no-such-capture-program-xyz'"):
        capture_with_command(_settings("no-such-capture-program-xyz", "{output}"), stem=tmp_path / "shot")


@pytest.mark.os_agnostic
def test_a_program_that_writes_nothing_is_refused(tmp_path: Path) -> None:
    """Exit 0 without an image file is a failure."""
    with pytest.raises(CameraError, match="wrote no image"):
        capture_with_command(_settings(sys.executable, "-c", "pass", "{output}"), stem=tmp_path / "shot")


@pytest.mark.os_agnostic
def test_a_program_that_writes_an_empty_file_is_refused(tmp_path: Path) -> None:
    """Exit 0 with a zero-byte image is a failure."""
    script = "import sys; open(sys.argv[1], 'wb').close()"

    with pytest.raises(CameraError, match="empty image"):
        capture_with_command(_settings(sys.executable, "-c", script, "{output}"), stem=tmp_path / "shot")


@pytest.mark.os_agnostic
def test_an_empty_command_is_refused(tmp_path: Path) -> None:
    """kind = command with no argv is a configuration error."""
    with pytest.raises(CameraError, match=r"set \[camera\] command"):
        capture_with_command(_settings(), stem=tmp_path / "shot")


@pytest.mark.os_agnostic
def test_a_command_without_the_placeholder_is_refused(tmp_path: Path) -> None:
    """Without {output} the program cannot know where to write, so it is not run."""
    marker = tmp_path / "ran"
    script = f"open({str(marker)!r}, 'w').close()"

    with pytest.raises(CameraError, match="no \\{output\\} argument"):
        capture_with_command(_settings(sys.executable, "-c", script), stem=tmp_path / "shot")
    assert not marker.exists()


@pytest.mark.os_agnostic
def test_conflicting_suffixes_are_refused(tmp_path: Path) -> None:
    """Two different image suffixes in one argv cannot both be the collected file."""
    with pytest.raises(CameraError, match="conflicting"):
        capture_with_command(
            _settings(sys.executable, "-c", WRITE_IMAGE, "{output}.png", "{output}.jpg"), stem=tmp_path / "shot"
        )


@pytest.mark.os_agnostic
def test_a_path_with_backslashes_is_substituted_literally(tmp_path: Path) -> None:
    """A backslash in the output path is not read as a regex replacement escape."""
    directory = tmp_path / "a\\1b" if sys.platform != "win32" else tmp_path / "a1b"
    directory.mkdir()

    path = capture_with_command(_settings(sys.executable, "-c", WRITE_IMAGE, "{output}"), stem=directory / "shot")

    assert path.read_bytes() == b"\xff\xd8\xff-image"
