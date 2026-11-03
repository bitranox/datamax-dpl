r"""Read one JSON array record whose value is a run of escaped backslashes, in a process of its own.

A test runs this as a script and reads what it prints: the file's size and how far the resident
peak rose while its records were read, in bytes. Only the resident figure shows the cost it looks
for: the regular expression engine keeps its backtracking state in memory tracemalloc does not see.
``ESCAPES`` in the environment says how many ``\\`` pairs the value holds.
"""

from __future__ import annotations

import os
import sys

from datamax_dpl.adapters.cli.label_io import Records


def main() -> int:
    """Read the records once and print the file's size and the rise of the resident peak."""
    if sys.platform == "win32":
        print("the resource module, which reads the resident peak, is POSIX-only", file=sys.stderr)
        return 2
    # Imported here: the module exists on POSIX only, and the suite imports this file on Windows too.
    import resource

    data = b'[{"a":"' + b"\\\\" * int(os.environ["ESCAPES"]) + b'"}]'
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    for _ in Records(data, "d.json"):
        pass
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss counts bytes on macOS and kilobytes on Linux.
    print(len(data), (after - before) * (1 if sys.platform == "darwin" else 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
