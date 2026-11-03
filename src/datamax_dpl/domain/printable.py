"""Text from outside the program, made safe to show on a terminal.

Label files and data are untrusted, and a printer on the network answers what it likes.
Shown raw, a control sequence in one of them can move the cursor and overwrite the line that said a
label was refused, or set the window title; JSON output escapes them already.
"""

from __future__ import annotations

import re

# Newline and tab stay: the human views are several lines long. The line separators and the bidi
# controls go too: a terminal or log reader breaks a line at the first and reorders what it shows at
# the second, so that a refusal reads as something else.
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028-\u202e\u2066-\u2069]")
_LINE_BREAKS = re.compile(r"[\n\t]")
#: Past this a code point takes the longer escape: two hex digits no longer name it.
_LAST_ONE_BYTE_CODE = 0xFF


def _escaped(found: re.Match[str]) -> str:
    code = ord(found[0])
    return f"\\x{code:02x}" if code <= _LAST_ONE_BYTE_CODE else f"\\u{code:04x}"


def printable(text: str) -> str:
    r"""Return ``text`` with each control that can rewrite what is shown escaped.

    That is each C0 control but newline and tab, DEL and each C1 control as ``\xNN``, and the line
    separators and bidi controls as ``\uNNNN``.

    Example:
        >>> printable("a\x1b]0;x\x07b\n")
        'a\\x1b]0;x\\x07b\n'
        >>> printable("\x9b2J\x7f"), printable("ä")
        ('\\x9b2J\\x7f', 'ä')
        >>> printable("a\u202eb\u2028c")
        'a\\u202eb\\u2028c'
    """
    return _CONTROLS.sub(_escaped, text)


def one_line(text: str) -> str:
    r"""Return ``text`` as :func:`printable` does, with newline and tab escaped too.

    For a sink that promises one line: a label key or a file name can hold a newline, and the
    line after it would read as the program's own.

    Example:
        >>> one_line("bad\nOK: 1 label(s)\tdone")
        'bad\\x0aOK: 1 label(s)\\x09done'
    """
    return _LINE_BREAKS.sub(_escaped, printable(text))


__all__ = ["one_line", "printable"]
