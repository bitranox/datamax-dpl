"""A name one module imports from another of this package is part of that module's stated surface."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import datamax_dpl

_ROOT = Path(datamax_dpl.__file__).parent


def _module_of(path: Path) -> tuple[str, ...]:
    parts = path.relative_to(_ROOT).with_suffix("").parts
    return parts[:-1] if parts[-1] == "__init__" else parts


def _source_of(module: tuple[str, ...]) -> Path | None:
    """The file of ``module``, inside the package; None when it is none of ours."""
    plain = _ROOT.joinpath(*module).with_suffix(".py")
    if plain.is_file():
        return plain
    package = _ROOT.joinpath(*module, "__init__.py")
    return package if package.is_file() else None


def _target(node: ast.ImportFrom, importer: Path) -> tuple[str, ...] | None:
    """The module ``node`` imports from, inside the package; None when it is outside."""
    named = tuple(node.module.split(".")) if node.module else ()
    if node.level:
        here = _module_of(importer)
        is_package = importer.name == "__init__.py"
        kept = len(here) - (node.level - (1 if is_package else 0))
        return (*here[:kept], *named)
    return named[1:] if named[:1] == ("datamax_dpl",) else None


def _exports(source: Path) -> set[str] | None:
    """The names ``source`` lists in ``__all__``; None when it states no list."""
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            return {
                item.value
                for item in ast.walk(node.value)
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
    return None


def _imports_across_modules() -> list[str]:
    missing: list[str] = []
    for importer in sorted(_ROOT.rglob("*.py")):
        for node in ast.walk(ast.parse(importer.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = _target(node, importer)
            source = None if target is None else _source_of(target)
            exported = None if source is None else _exports(source)
            if exported is None or source is None:
                continue
            missing.extend(
                f"{importer.relative_to(_ROOT)} takes {alias.name} from {source.relative_to(_ROOT)}"
                for alias in node.names
                if alias.name not in exported
                and not alias.name.startswith("_")
                and _source_of((*(target or ()), alias.name)) is None
            )
    return missing


@pytest.mark.os_agnostic
def test_a_name_imported_across_modules_is_in_the_exporters_all() -> None:
    missing = _imports_across_modules()
    assert missing == [], "\n".join(missing)


@pytest.mark.os_agnostic
def test_the_scan_looks_at_the_whole_package() -> None:
    """A scan that resolved nothing would pass on any tree."""
    node = ast.parse("from ..domain.label import Label").body[0]
    assert isinstance(node, ast.ImportFrom)
    assert _target(node, _ROOT / "application" / "use_cases.py") == ("domain", "label")
    assert len(list(_ROOT.rglob("*.py"))) > 50
