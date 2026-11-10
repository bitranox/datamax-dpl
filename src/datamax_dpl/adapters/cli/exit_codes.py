"""POSIX-conventional exit codes for CLI error paths.

Provides a single :class:`ExitCode` enum so every ``ctx.exit()`` call in a CLI
command carries a meaningful, grep-friendly integer instead of a bare ``1``.
Commands exit through click's ``Exit`` (what ``ctx.exit()`` raises), never a bare
``SystemExit``; ``adapters/cli/main.py`` turns it into the process exit code.

``adapters/cli/main.py`` exits 130 for Ctrl-C (and Ctrl-Break on Windows) and 141 when the reader of
the output went away. No other signal has a handler: SIGTERM and SIGHUP end the process by that
signal, with no envelope, as closing the console window ends it on Windows.

The label and printer commands exit through ``envelope.Outcome`` instead: 0 done, 1 refused,
2 an error, a printer that cannot be reached included.

Contents:
    * :class:`ExitCode` - IntEnum of the exit codes the config commands and ``main.py`` use.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """POSIX-conventional exit codes for CLI error paths.

    Values follow sysexits.h and errno conventions where applicable:

    * 0-1: generic success / failure
    * 13: EACCES
    * 22: EINVAL
    * 78: EX_CONFIG (sysexits.h)
    * 128+N: signal N

    Example:
        >>> ExitCode.SUCCESS
        <ExitCode.SUCCESS: 0>
        >>> int(ExitCode.CONFIG_ERROR)
        78
    """

    SUCCESS = 0
    GENERAL_ERROR = 1
    PERMISSION_DENIED = 13
    INVALID_ARGUMENT = 22
    CONFIG_ERROR = 78
    SIGNAL_INT = 130
    BROKEN_PIPE = 141


__all__ = ["ExitCode"]
