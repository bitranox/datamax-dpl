"""How deep the two JSON readers behind a JSON array ``--data`` file go, for the tests that pin it."""

from __future__ import annotations

import sys

#: orjson's nesting limit, the outer array counted: it refuses a 1025th level of arrays and objects
#: together (measured, orjson 3.13). The walk refuses the same, as a whole parse did.
ORJSON_DEPTH = 1024

#: Python 3.10 and 3.11 read JSON only as deep as the interpreter's recursion limit lets them
#: (sys.getrecursionlimit, 1000 by default, less the frames already on the stack): about 995 levels,
#: short of orjson's. The element finder then stops with a RecursionError, and the file is refused
#: by name. 3.12 and later read 2000 levels and more (measured 2026-11-04, 3.10 to 3.15, Linux and
#: Windows).
READER_STOPS_SHORT_OF_ORJSON = sys.version_info < (3, 12)

__all__ = ["ORJSON_DEPTH", "READER_STOPS_SHORT_OF_ORJSON"]
