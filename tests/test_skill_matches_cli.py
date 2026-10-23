"""The shipped skill only names commands and options the CLI really has."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from datamax_dpl.adapters.cli import root as cli_mod

SKILL = Path(__file__).resolve().parent.parent / "skills" / "datamax-dpl" / "SKILL.md"
_INVOCATION = re.compile(r"(?<![/\w])datamax-dpl((?: --json(?:-bare)?)?) ([a-z][a-z-]*)([^`]*)")
_OPTION = re.compile(r"(?<![\w-])(--[a-z][a-z-]*|-o)\b")
_GROUP_OPTIONS = {"--json", "--json-bare", "--help", "--version", "--traceback", "--profile", "--set", "--env-file"}


def _invocations() -> list[tuple[str, list[str]]]:
    text = SKILL.read_text(encoding="utf-8")
    found = [(m[2], _OPTION.findall(m[3])) for m in _INVOCATION.finditer(text)]
    return [(command, options) for command, options in found if command != "help"]


def _option_names(command: str) -> set[str]:
    params = cli_mod.cli.commands[command].params
    return {name for param in params for name in getattr(param, "opts", [])} | {"--help"}


@pytest.mark.os_agnostic
def test_the_skill_names_commands_at_all() -> None:
    assert len(_invocations()) >= 8


@pytest.mark.os_agnostic
def test_every_command_in_the_skill_exists() -> None:
    missing = {command for command, _ in _invocations()} - set(cli_mod.cli.commands)
    assert missing == set()


@pytest.mark.os_agnostic
def test_every_option_in_the_skill_belongs_to_its_command() -> None:
    wrong = [
        (command, option)
        for command, options in _invocations()
        for option in options
        if option not in _option_names(command) | _GROUP_OPTIONS
    ]
    assert wrong == []
