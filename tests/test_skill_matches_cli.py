"""The shipped skill only names commands and options the CLI really has."""

from __future__ import annotations

import re
from pathlib import Path

import click
import pytest

from datamax_dpl.adapters.cli import root as cli_mod

SKILL = Path(__file__).resolve().parent.parent / "skills" / "datamax-dpl" / "SKILL.md"
_INVOCATION = re.compile(r"(?<![/\w])datamax-dpl((?: --json(?:-bare)?)?) ([a-z][a-z-]*)([^`]*)")
_OPTION = re.compile(r"(?<![\w-])(--[a-z][a-z-]*|-o)\b")
_GROUP_OPTIONS = {"--json", "--json-bare", "--help", "--version", "--traceback", "--profile", "--set", "--env-file"}


_SUBCOMMAND = re.compile(r"\s*([a-z][a-z-]*)")


def _invocations() -> list[tuple[tuple[str, ...], list[str]]]:
    """Each command path the skill invokes (a group's subcommand included) with the options after it."""
    text = SKILL.read_text(encoding="utf-8")
    found: list[tuple[tuple[str, ...], list[str]]] = []
    for match in _INVOCATION.finditer(text):
        command, rest = match[2], match[3]
        if command == "help":
            continue
        sub = _SUBCOMMAND.match(rest) if isinstance(cli_mod.cli.commands.get(command), click.Group) else None
        path = (command, sub[1]) if sub else (command,)
        found.append((path, _OPTION.findall(rest[sub.end() :] if sub else rest)))
    return found


def _command(path: tuple[str, ...]) -> click.Command | None:
    current: click.Command | None = cli_mod.cli
    for name in path:
        current = current.commands.get(name) if isinstance(current, click.Group) else None
    return current


def _option_names(path: tuple[str, ...]) -> set[str]:
    command = _command(path)
    assert command is not None, path
    return {name for param in command.params for name in getattr(param, "opts", [])} | {"--help"}


@pytest.mark.os_agnostic
def test_the_skill_names_commands_at_all() -> None:
    assert len(_invocations()) >= 8


@pytest.mark.os_agnostic
def test_every_command_in_the_skill_exists() -> None:
    missing = {path for path, _ in _invocations() if _command(path) is None}
    assert missing == set()


@pytest.mark.os_agnostic
def test_every_option_in_the_skill_belongs_to_its_command() -> None:
    wrong = [
        (path, option)
        for path, options in _invocations()
        for option in options
        if option not in _option_names(path) | _GROUP_OPTIONS
    ]
    assert wrong == []
