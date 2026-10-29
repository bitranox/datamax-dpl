"""Write a printer's values into the user configuration layer, keeping comments and key order.

``calibrate set`` stores measured scale factors here. The user layer is ``config.toml`` followed
by the TOML files of its ``config.d`` directory in name order, and a later file wins over an
earlier one, so a value is written into the LAST file that holds the printer's section: there it
takes effect, and a user reading that file sees the value in force. Without the section in any
file, it goes into ``config.toml``, which is created when missing.

Layers above the user one (``.env``, environment variables, ``--set``) can still win; the
caller re-reads the configuration to find out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import tomlkit
from lib_layered_config import deploy_config_paths
from tomlkit.container import Container
from tomlkit.items import Table

from datamax_dpl import __init__conf__
from datamax_dpl.adapters.config.loader import get_config, get_default_config_path

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from tomlkit.toml_document import TOMLDocument


class _Section(Protocol):
    """The part of a tomlkit document or table used here: tomlkit leaves ``get`` untyped."""

    def get(self, key: str, /) -> object: ...
    def __setitem__(self, key: str, value: object, /) -> None: ...


_DOT_D_SUFFIX = ".d"
_TOML = ".toml"


def user_layer_files(*, profile: str | None = None, platform: str | None = None) -> list[Path]:
    """Return the user layer's ``config.toml`` and its existing TOML drop-ins, in merge order.

    Args:
        profile: The configuration profile, or None for none.
        platform: The platform whose paths to use, or None for the running one.

    Returns:
        The base file (whether or not it exists), then each ``config.d/*.toml`` by name.
    """
    paths = deploy_config_paths(
        get_default_config_path(),
        vendor=__init__conf__.LAYEREDCONF_VENDOR,
        app=__init__conf__.LAYEREDCONF_APP,
        slug=__init__conf__.LAYEREDCONF_SLUG,
        targets=["user"],
        profile=profile,
        platform=platform,
    )
    (base,) = [path for path in paths if path.parent.suffix != _DOT_D_SUFFIX]
    dot_d = base.with_suffix(_DOT_D_SUFFIX)
    drop_ins = sorted(path for path in dot_d.iterdir() if path.suffix == _TOML) if dot_d.is_dir() else []
    return [base, *drop_ins]


def write_printer_values(files: Sequence[Path], *, printer: str, values: Mapping[str, float]) -> Path:
    """Set ``values`` in the printer's section of the last file holding it, else the first file.

    Args:
        files: The layer's files in merge order, the base file first.
        printer: The printer's name for ``[printers.<name>]``, or "" for ``[printer]``.
        values: The keys and values to set.

    Returns:
        The file written.
    """
    section = ("printers", printer) if printer else ("printer",)
    holders = [path for path in files if path.is_file() and _find(_load(path), section) is not None]
    target = holders[-1] if holders else files[0]
    document = _load(target)
    table = _ensure(document, section)
    for key, value in values.items():
        table[key] = value
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(tomlkit.dumps(document), encoding="utf-8")
    return target


def set_user_printer_values(*, printer: str, values: Mapping[str, float], profile: str | None = None) -> Path:
    """Write ``values`` into the user layer and drop the cached configuration so it is read again.

    Args:
        printer: The printer's name, or "" for the ``[printer]`` section.
        values: The keys and values to set.
        profile: The configuration profile, or None for none.

    Returns:
        The file written.
    """
    written = write_printer_values(user_layer_files(profile=profile), printer=printer, values=values)
    get_config.cache_clear()
    return written


def _load(path: Path) -> TOMLDocument:
    return tomlkit.parse(path.read_text(encoding="utf-8")) if path.is_file() else tomlkit.document()


def _find(document: TOMLDocument, keys: Sequence[str]) -> _Section | None:
    current = cast("_Section", document)
    for key in keys:
        child = current.get(key)
        if not isinstance(child, (Container, Table)):
            return None
        current = cast("_Section", child)
    return current


def _ensure(document: TOMLDocument, keys: Sequence[str]) -> _Section:
    current = cast("_Section", document)
    for depth, key in enumerate(keys):
        child = current.get(key)
        if not isinstance(child, (Container, Table)):
            # An outer table that only holds sub-tables prints as [printers.<name>], not [printers].
            child = tomlkit.table(is_super_table=depth < len(keys) - 1)
            current[key] = child
        current = cast("_Section", child)
    return current


__all__ = ["set_user_printer_values", "user_layer_files", "write_printer_values"]
