"""Tests for config-deploy permission options.

Verifies that permission parameters are correctly parsed, passed through
the CLI, and delivered to deploy_configuration.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import pytest

from datamax_dpl import __init__conf__
from datamax_dpl.adapters import cli as cli_mod
from datamax_dpl.adapters.config.deploy import deploy_configuration
from datamax_dpl.composition import build_production
from datamax_dpl.domain.enums import DeployTarget

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from click.testing import CliRunner, Result

_LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="XDG_CONFIG_HOME locates the user layer on Linux"
)


@pytest.fixture
def user_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the user layer at ``tmp_path`` for a real deploy; return the directory it deploys to."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / __init__conf__.LAYEREDCONF_SLUG


def _modes(deployed: Path) -> tuple[int, int]:
    """The deployed directory's mode and its ``config.toml``'s."""
    return stat.S_IMODE(deployed.stat().st_mode), stat.S_IMODE((deployed / "config.toml").stat().st_mode)


# ======================== CLI Option Parsing Tests ========================


@dataclass
class CapturedDeployArgs:
    """Container for captured deploy_configuration arguments."""

    targets: tuple[DeployTarget, ...]
    force: bool
    profile: str | None
    set_permissions: bool | None
    dir_mode: int | None
    file_mode: int | None
    permission_overrides: Mapping[str, object] | None


@pytest.fixture
def inject_deploy_with_permission_capture(
    clear_config_cache: None,
) -> Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]]:
    """Return a factory that captures all deploy_configuration arguments."""

    def _inject(deployed_path: Path, captured: list[CapturedDeployArgs]) -> Callable[[], Any]:
        def _capturing_deploy(
            *,
            targets: Any,
            force: bool = False,
            profile: str | None = None,
            set_permissions: bool | None = None,
            dir_mode: int | None = None,
            file_mode: int | None = None,
            permission_overrides: Mapping[str, object] | None = None,
        ) -> list[Path]:
            captured.append(
                CapturedDeployArgs(
                    targets=targets,
                    force=force,
                    profile=profile,
                    set_permissions=set_permissions,
                    dir_mode=dir_mode,
                    file_mode=file_mode,
                    permission_overrides=permission_overrides,
                )
            )
            return [deployed_path]

        services = replace(build_production(), deploy_configuration=_capturing_deploy)
        return lambda: services

    return _inject


@pytest.mark.os_agnostic
def test_cli_deploy_leaves_the_permission_decision_to_the_library_by_default(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """Without an option the command decides nothing: the library follows the configured ``enabled``."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--target", "user"], obj=factory)

    assert result.exit_code == 0
    assert len(captured) == 1
    assert (captured[0].set_permissions, captured[0].dir_mode, captured[0].file_mode) == (None, None, None)
    assert captured[0].permission_overrides is None
    assert "permissions not set" not in result.output


@pytest.mark.os_agnostic
def test_cli_deploy_no_permissions_flag_disables_permissions(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """--no-permissions flag disables permission setting."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--no-permissions"], obj=factory
    )

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].set_permissions is False


@pytest.mark.os_agnostic
def test_cli_deploy_permissions_flag_enables_permissions(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """--permissions flag explicitly enables permission setting."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--target", "user", "--permissions"], obj=factory)

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].set_permissions is True


@pytest.mark.os_agnostic
def test_cli_deploy_dir_mode_parses_octal_without_prefix(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """--dir-mode accepts octal string without 0o prefix (e.g., '750')."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--dir-mode", "750"], obj=factory
    )

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].dir_mode == 0o750


@pytest.mark.os_agnostic
def test_cli_deploy_dir_mode_parses_octal_with_prefix(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """--dir-mode accepts octal string with 0o prefix (e.g., '0o750')."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--dir-mode", "0o750"], obj=factory
    )

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].dir_mode == 0o750


@pytest.mark.os_agnostic
def test_cli_deploy_file_mode_parses_octal(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """--file-mode accepts octal string."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--file-mode", "640"], obj=factory
    )

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].file_mode == 0o640


@pytest.mark.os_agnostic
def test_cli_deploy_invalid_octal_dir_mode_rejected(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """A mode that is not a plain octal literal is rejected with an error."""
    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--dir-mode", "abc"], obj=production_factory
    )

    assert result.exit_code != 0
    assert "is not a plain octal literal" in result.output


@pytest.mark.os_agnostic
def test_cli_deploy_invalid_octal_file_mode_rejected(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """A file mode that is not a plain octal literal is rejected."""
    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--file-mode", "xyz"], obj=production_factory
    )

    assert result.exit_code != 0
    assert "is not a plain octal literal" in result.output


@pytest.mark.os_agnostic
def test_cli_deploy_both_mode_options_passed(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """Both --dir-mode and --file-mode can be specified together."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["config-deploy", "--target", "user", "--dir-mode", "750", "--file-mode", "640"],
        obj=factory,
    )

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].dir_mode == 0o750
    assert captured[0].file_mode == 0o640


@pytest.mark.os_agnostic
def test_cli_deploy_no_permissions_reports_in_output(
    cli_runner: CliRunner,
    tmp_path: Path,
    inject_deploy_with_permission_capture: Callable[[Path, list[CapturedDeployArgs]], Callable[[], Any]],
) -> None:
    """When --no-permissions is used, output mentions permissions not set."""
    deployed_path = tmp_path / "config.toml"
    deployed_path.touch()
    captured: list[CapturedDeployArgs] = []

    factory = inject_deploy_with_permission_capture(deployed_path, captured)

    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config-deploy", "--target", "user", "--no-permissions"], obj=factory
    )

    assert result.exit_code == 0
    assert "permissions not set" in result.output


@pytest.mark.os_agnostic
def test_cli_deploy_help_shows_permission_options(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Help text includes permission-related options."""
    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--help"], obj=production_factory)

    assert result.exit_code == 0
    assert "--permissions" in result.output
    assert "--no-permissions" in result.output
    assert "--dir-mode" in result.output
    assert "--file-mode" in result.output


# ======================== deploy.py Integration Tests ========================


@_LINUX_ONLY
def test_a_deploy_without_permissions_leaves_the_modes_to_the_umask(
    user_layer: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """False keeps the umask's modes; None, the default, takes the library's owner-only ones."""
    previous = os.umask(0o022)
    try:
        deploy_configuration(targets=[DeployTarget.USER], set_permissions=False)
        assert _modes(user_layer) == (0o755, 0o644)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "default"))
        deploy_configuration(targets=[DeployTarget.USER])
    finally:
        os.umask(previous)
    assert _modes(tmp_path / "default" / user_layer.name) == (0o700, 0o600)


@_LINUX_ONLY
def test_a_deploy_applies_its_permission_overrides(user_layer: Path) -> None:
    previous = os.umask(0o022)
    try:
        deploy_configuration(targets=[DeployTarget.USER], permission_overrides={"user_file": "0o640"})
    finally:
        os.umask(previous)
    assert _modes(user_layer)[1] == 0o640


@_LINUX_ONLY
def test_a_deploy_applies_its_directory_and_file_modes(user_layer: Path) -> None:
    deploy_configuration(targets=[DeployTarget.USER], dir_mode=0o750, file_mode=0o640)
    assert _modes(user_layer) == (0o750, 0o640)
