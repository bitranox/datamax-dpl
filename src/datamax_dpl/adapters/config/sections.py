"""The configuration sections the printer and camera settings are read from and written to.

The CLI settings read them and ``calibrate set`` writes a printer's values into one, so both name
a section from here, and a refusal names a key under the same spelling the file uses. Every
header (``[printers.lager]``), dotted key (``printer.status_wait``) and key hint
(``[camera] kind``) is rendered by :class:`SectionPath`, so no message spells a section by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class ConfigSection(str, Enum):
    """A configuration section, named as the file and every refusal name it.

    ``PRINTERS`` holds one sub-table per named printer, ``[printers.<name>]``.

    Example:
        >>> ConfigSection.PRINTERS.value
        'printers'
    """

    PRINTER = "printer"
    PRINTERS = "printers"
    CAMERA = "camera"


@dataclass(frozen=True, slots=True)
class SectionPath:
    """One table of the configuration file: a section, or one named printer's table in ``[printers]``.

    Attributes:
        section: The section the table is, or lies in.
        name: The named printer's table under ``[printers]``; "" for the section itself.

    Example:
        >>> lager = SectionPath(ConfigSection.PRINTERS, "lager")
        >>> lager.header, lager.key_path("firmware"), lager.setting("uri")
        ('[printers.lager]', 'printers.lager.firmware', '[printers.lager] uri')
    """

    section: ConfigSection
    name: str = ""

    def __post_init__(self) -> None:
        """Refuse a name under a section that holds no named tables.

        Raises:
            ValueError: ``name`` is set for a section other than ``PRINTERS``.
        """
        # Only [printers] holds sub-tables; a named [camera.x] would render a header no reader uses.
        if self.name and self.section != ConfigSection.PRINTERS:
            named, given = SectionPath(ConfigSection.PRINTERS).header, SectionPath(self.section).header
            msg = f"only {named} holds named tables, not {given}"
            raise ValueError(msg)

    @property
    def keys(self) -> tuple[str, ...]:
        """The table keys leading to this table in the parsed file, outermost first.

        Example:
            >>> SectionPath(ConfigSection.PRINTERS, "lager").keys
            ('printers', 'lager')
        """
        # .value, not the member: a str-mixin member formats as "ConfigSection.PRINTER" on 3.11+.
        return (self.section.value, self.name) if self.name else (self.section.value,)

    @property
    def dotted(self) -> str:
        """The table as a dotted path, as ``--set`` and a refusal name it: ``printers.lager``.

        Example:
            >>> SectionPath(ConfigSection.PRINTER).dotted
            'printer'
        """
        return ".".join(self.keys)

    @property
    def header(self) -> str:
        """The table's header as the file spells it: ``[printers.lager]``.

        Example:
            >>> SectionPath(ConfigSection.PRINTERS).header
            '[printers]'
        """
        return f"[{self.dotted}]"

    def key_path(self, key: str) -> str:
        """A key of this table as a dotted path: ``printer.status_wait``.

        Args:
            key: The key, or a dotted path below the table.

        Example:
            >>> SectionPath(ConfigSection.CAMERA).key_path("kind")
            'camera.kind'
        """
        return f"{self.dotted}.{key}"

    def setting(self, key: str) -> str:
        """A key of this table as a hint names it, header first: ``[printer] uri``.

        Args:
            key: The key.

        Example:
            >>> SectionPath(ConfigSection.CAMERA).setting("kind")
            '[camera] kind'
        """
        return f"{self.header} {key}"


#: The ``[printer]`` section: every printer's settings, and the only ones when no name is chosen.
PRINTER_SECTION: Final = SectionPath(ConfigSection.PRINTER)

#: The ``[camera]`` section.
CAMERA_SECTION: Final = SectionPath(ConfigSection.CAMERA)


def printer_section(name: str) -> SectionPath:
    """The table a printer's own settings are read from and written to.

    Args:
        name: The printer's name, or "" when none was chosen.

    Returns:
        ``[printers.<name>]`` for a named printer, else ``[printer]``.

    Example:
        >>> printer_section("lager").header, printer_section("").header
        ('[printers.lager]', '[printer]')
    """
    return SectionPath(ConfigSection.PRINTERS, name) if name else PRINTER_SECTION


__all__ = ["CAMERA_SECTION", "PRINTER_SECTION", "ConfigSection", "SectionPath", "printer_section"]
