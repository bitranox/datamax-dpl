"""The configuration sections the printer and camera settings are read from and written to.

The CLI settings read them and ``calibrate set`` writes a printer's values into one, so both name
a section from here, and a refusal names a key under the same spelling the file uses.
"""

from __future__ import annotations

from enum import Enum


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


__all__ = ["ConfigSection"]
