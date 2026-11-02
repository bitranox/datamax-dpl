"""Measure the peak memory of one command over a batch, in a process of its own.

A test runs this as a script: a figure taken inside the suite would hold everything the suite has
imported and left behind.

The environment says what to run: ``PEAK_COMMAND`` is ``validate``, ``render`` (to a file), ``print``
(over TCP to a printer that keeps nothing) or ``print-one-way`` (to a file, a link that reads no
answer), ``PEAK_RECORDS`` how many records, ``PEAK_DIRECTORY`` where its files go.
``PEAK_UNTRACED`` measures nothing and leaves the figure to whatever started it (``ru_maxrss``):
tracing a large batch takes minutes.
"""

from __future__ import annotations

import contextlib
import io
import os
import socket
import sys
import threading
import tracemalloc
from pathlib import Path

from printer_server import ImageModules

from datamax_dpl.adapters.cli import root as cli_mod
from datamax_dpl.composition import build_production
from datamax_dpl.domain.label import Label, Text

#: The fields of the label: a job of about 10 KB a record, and a filled label of about 360 KB.
FIELDS = 399


def _serve(listener: socket.socket) -> None:
    """Play a printer that answers its requests and keeps nothing of the job, connection after connection."""
    while True:
        modules = ImageModules()
        connection, _ = listener.accept()
        with connection, contextlib.suppress(OSError):
            while chunk := connection.recv(65536):
                if reply := modules.feed(chunk):
                    connection.sendall(reply)


def _arguments(command: str, directory: Path, *, port: int) -> list[str]:
    label = str(directory / "label.json")
    data = str(directory / "data.csv")
    common = [label, "--data", data, "--model", "dmx-600"]
    if command == "print":
        return ["--json-bare", "print", *common, "--printer", f"tcp://127.0.0.1:{port}"]
    if command == "print-one-way":
        return ["--json-bare", "print", *common, "--printer", f"file:{directory / 'sent.dpl'}"]
    if command == "render":
        return ["--json-bare", "render", *common, "-o", str(directory / "job.dpl")]
    return ["--json-bare", "validate", *common]


def _invoke(arguments: list[str]) -> int:
    """Run the command in this process; its envelope is not what is measured, so it goes nowhere."""
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            cli_mod.cli.main(arguments, standalone_mode=False, obj=build_production)
        except SystemExit as stop:
            return int(stop.code or 0)
    return 0


def main() -> int:
    """Run one command over a batch and print its peak allocation in bytes."""
    command, records = os.environ["PEAK_COMMAND"], os.environ["PEAK_RECORDS"]
    directory = Path(os.environ["PEAK_DIRECTORY"])
    label = Label(elements=[Text(x=0, y=row, text="{a}") for row in range(FIELDS)])
    (directory / "label.json").write_text(label.model_dump_json(), encoding="utf-8")
    listener = socket.create_server(("127.0.0.1", 0))
    server = threading.Thread(target=_serve, args=(listener,), daemon=True)
    server.start()
    arguments = _arguments(command, directory, port=listener.getsockname()[1])
    rows = "".join(f"r{number}\n" for number in range(int(records)))
    (directory / "data.csv").write_text("a\n" + rows, encoding="utf-8")
    del rows
    if "PEAK_UNTRACED" in os.environ:
        code = _invoke(arguments)
        print(0)
        return code
    tracemalloc.start()
    code = _invoke(arguments)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    print(peak)
    return code


if __name__ == "__main__":
    sys.exit(main())
