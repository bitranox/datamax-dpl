"""Text from outside the program, made safe to show on a terminal.

Label files and data are untrusted, and a printer or camera on the network answers what it likes.
Shown raw, a control sequence in one of them can move the cursor and overwrite the line that said a
label was refused, or set the window title; JSON output escapes them already.
"""

from __future__ import annotations

import re

# Newline and tab stay: the human views are several lines long, and neither can rewrite what is shown.
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def printable(text: str) -> str:
    r"""Return ``text`` with each C0 control but newline and tab, DEL and each C1 control as ``\xNN``.

    Example:
        >>> printable("a\x1b]0;x\x07b\n")
        'a\\x1b]0;x\\x07b\n'
        >>> printable("\x9b2J\x7f"), printable("ä")
        ('\\x9b2J\\x7f', 'ä')
    """
    return _CONTROLS.sub(lambda found: f"\\x{ord(found[0]):02x}", text)


__all__ = ["printable"]
