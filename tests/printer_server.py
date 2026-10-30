"""TCP servers on 127.0.0.1 that play a printer, for transport, CLI and script tests.

:class:`PrinterServer` serves one connection; :class:`ConnectionsServer` serves several, one after
another, for a client that connects again and again (``scripts/status_cycles.py``).

Imported by bare module name (``from printer_server import ...``): ``pythonpath`` in pyproject puts
``tests/`` on ``sys.path``, while ``from tests.printer_server`` passes under ``python -m pytest`` and
fails at collection under the bare ``pytest`` CI runs. The ``serve`` fixture in ``tests/conftest.py``
starts these servers and joins every one at teardown.
"""

from __future__ import annotations

import contextlib
import socket
import threading
from typing import TYPE_CHECKING, TypeAlias

from datamax_dpl.adapters.transport.uri import TcpUri
from datamax_dpl.domain.status import STATUS_REQUEST

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["JOIN_SECONDS", "ConnectionsServer", "Play", "PrinterServer", "answering", "silent"]

#: Longest wait for a server thread to end, and for a client to connect to it.
JOIN_SECONDS = 5.0

#: What a test hands :class:`PrinterServer`: the printer's side of one connection.
Play: TypeAlias = "Callable[[PrinterServer, socket.socket], None]"


class PrinterServer:
    """A one-connection server on an ephemeral port; ``play`` acts the printer on the accepted socket.

    It binds 127.0.0.1, never ``localhost``: GitHub's runners resolve ``localhost`` to ``::1`` first,
    where nothing listens.
    """

    def __init__(self, play: Play) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0))
        # Bounded, so a test whose client never connects still ends the thread instead of leaking it.
        self.listener.settimeout(JOIN_SECONDS)
        self.port: int = self.listener.getsockname()[1]
        self.received = bytearray()
        self._play = play
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def uri(self) -> TcpUri:
        """The address :meth:`TcpTransport.connect` takes."""
        return TcpUri(host="127.0.0.1", port=self.port)

    @property
    def address(self) -> str:
        """The same address as the CLI's ``--printer`` takes it."""
        return f"tcp://127.0.0.1:{self.port}"

    @property
    def ended(self) -> bool:
        """True once the server thread has finished."""
        return not self._thread.is_alive()

    def _serve(self) -> None:
        try:
            connection, _ = self.listener.accept()
        except OSError:
            # No client came within JOIN_SECONDS, or join() closed the listener first.
            return
        # Bounded too: a client that never closes would otherwise hold read_to_end, and the thread, forever.
        connection.settimeout(JOIN_SECONDS)
        with connection:
            self._play(self, connection)

    def read_exactly(self, connection: socket.socket, count: int) -> bytes:
        """Receive ``count`` bytes, fewer when the client hangs up or falls silent first, and record them."""
        data = bytearray()
        while len(data) < count:
            try:
                chunk = connection.recv(count - len(data))
            except TimeoutError:
                break
            if not chunk:
                break
            data += chunk
        self.received += data
        return bytes(data)

    def read_to_end(self, connection: socket.socket) -> None:
        """Record everything the client sends until it closes the connection or falls silent."""
        # A client that closes with our answer still unread resets the connection: that is its end.
        # One silent for JOIN_SECONDS is treated the same, so the thread still ends.
        with contextlib.suppress(ConnectionResetError, TimeoutError):
            while chunk := connection.recv(65536):
                self.received += chunk

    def join(self) -> None:
        """Wait up to :data:`JOIN_SECONDS` for the thread to end, then close the listener."""
        self._thread.join(JOIN_SECONDS)
        self.listener.close()


def answering(request: bytes, reply: bytes) -> Play:
    """A printer that waits for ``request``, sends ``reply``, then records the rest until hang-up."""

    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, len(request))
        connection.sendall(reply)
        server.read_to_end(connection)

    return play


def silent(server: PrinterServer, connection: socket.socket) -> None:
    """A printer that records everything it receives and never answers."""
    server.read_to_end(connection)


class ConnectionsServer:
    """A server for ``limit`` connections one after another; then it closes, and connects are refused.

    Each connection is recorded on its own in :attr:`connections`. One that opens with the status
    request gets ``status_reply``; any other is only recorded, until the client closes it. A
    connection made and closed with nothing sent (a bare connect) is recorded as ``b""``.
    """

    def __init__(self, *, limit: int, status_reply: bytes) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.listener.settimeout(JOIN_SECONDS)
        self.port: int = self.listener.getsockname()[1]
        self.connections: list[bytes] = []
        self._limit = limit
        self._status_reply = status_reply
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def address(self) -> str:
        """The address as the CLI's ``--printer`` takes it."""
        return f"tcp://127.0.0.1:{self.port}"

    @property
    def ended(self) -> bool:
        """True once the server thread has finished."""
        return not self._thread.is_alive()

    def _serve(self) -> None:
        try:
            for _ in range(self._limit):
                try:
                    connection, _ = self.listener.accept()
                except OSError:
                    return
                connection.settimeout(JOIN_SECONDS)
                with connection:
                    self.connections.append(self._take(connection))
        finally:
            # Nothing listens from here on, so the next connect is refused: a wedged port.
            self.listener.close()

    def _take(self, connection: socket.socket) -> bytes:
        data = bytearray()
        answered = False
        # A client that closes with our answer unread resets the connection: that is its end too.
        with contextlib.suppress(ConnectionResetError, TimeoutError):
            while chunk := connection.recv(65536):
                data += chunk
                if not answered and data.startswith(STATUS_REQUEST):
                    connection.sendall(self._status_reply)
                    answered = True
        return bytes(data)

    def join(self) -> None:
        """Wait up to :data:`JOIN_SECONDS` for the thread to end, then close the listener."""
        self._thread.join(JOIN_SECONDS)
        self.listener.close()
