"""Exit code integration tests."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from datamax_dpl.adapters import cli as cli_mod
from datamax_dpl.adapters.cli.exit_codes import ExitCode
from datamax_dpl.application.ports import CONFIGURED_PERMISSIONS, DeployPermissionRequest

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import CliRunner, Result


@pytest.mark.os_agnostic
def test_when_config_section_is_invalid_it_exits_with_code_22(
    cli_runner: CliRunner,
    production_factory: Callable[[], Any],
) -> None:
    """Config --section with nonexistent section must exit with INVALID_ARGUMENT (22)."""
    result: Result = cli_runner.invoke(
        cli_mod.cli, ["config", "--section", "nonexistent_section_that_does_not_exist"], obj=production_factory
    )

    assert result.exit_code == 22
    assert "not found" in result.stderr


@pytest.mark.os_agnostic
def test_when_config_deploy_has_permission_error_it_exits_with_code_13(
    cli_runner: CliRunner,
    inject_deploy_configuration: Callable[[Callable[..., list[Path]]], Callable[[], Any]],
) -> None:
    """Config-deploy PermissionError must exit with PERMISSION_DENIED (13)."""

    def mock_deploy(
        *,
        targets: Any,
        force: bool = False,
        profile: str | None = None,
        permissions: DeployPermissionRequest = CONFIGURED_PERMISSIONS,
    ) -> list[Any]:
        raise PermissionError("Permission denied")

    factory = inject_deploy_configuration(mock_deploy)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--target", "app"], obj=factory)

    assert result.exit_code == 13
    assert "Permission denied" in result.stderr


@pytest.mark.os_agnostic
def test_when_config_deploy_has_generic_error_it_exits_with_code_1(
    cli_runner: CliRunner,
    inject_deploy_configuration: Callable[[Callable[..., list[Path]]], Callable[[], Any]],
) -> None:
    """Config-deploy generic Exception must exit with GENERAL_ERROR (1)."""

    def mock_deploy(
        *,
        targets: Any,
        force: bool = False,
        profile: str | None = None,
        permissions: DeployPermissionRequest = CONFIGURED_PERMISSIONS,
    ) -> list[Any]:
        raise OSError("Disk full")

    factory = inject_deploy_configuration(mock_deploy)

    result: Result = cli_runner.invoke(cli_mod.cli, ["config-deploy", "--target", "user"], obj=factory)

    assert result.exit_code == 1
    assert "Disk full" in result.stderr


def _exit_codes_named_in_src() -> set[str]:
    """Every ``ExitCode.<NAME>`` the package's source names outside the enum's own module."""
    src = Path(__file__).resolve().parents[1] / "src" / "datamax_dpl"
    named: set[str] = set()
    for module in src.rglob("*.py"):
        if module.name == "exit_codes.py":
            continue
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "ExitCode":
                named.add(node.attr)
    return named


@pytest.mark.os_agnostic
def test_every_exit_code_is_one_a_command_exits_with() -> None:
    """A member nothing exits with reads, in the enum and its docstring, as a code a script can get."""
    named = _exit_codes_named_in_src()
    assert len(named) >= 5, f"the scan found only {sorted(named)}, too few to mean anything"
    assert sorted(member.name for member in ExitCode if member.name not in named) == []
