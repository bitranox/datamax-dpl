"""TCP servers on 127.0.0.1 that play a printer, for transport, CLI and script tests.

:class:`PrinterServer` serves one connection per play it is given, in order, and can refuse every
connect for a while first; :class:`ConnectionsServer` serves several, one after another, for a
client that connects again and again (``scripts/status_cycles.py``).

Imported by bare module name (``from printer_server import ...``): ``pythonpath`` in pyproject puts
``tests/`` on ``sys.path``, while ``from tests.printer_server`` passes under ``python -m pytest`` and
fails at collection under the bare ``pytest`` CI runs. The ``serve`` fixture in ``tests/conftest.py``
starts these servers and joins every one at teardown.
"""

from __future__ import annotations

import contextlib
import socket
import struct
import sys
import threading
import time
from typing import TYPE_CHECKING, Protocol, TypeAlias

from datamax_dpl.adapters.transport.uri import TcpUri
from datamax_dpl.domain.status import STATUS_REQUEST

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "JOIN_SECONDS",
    "ConnectionsServer",
    "Play",
    "PrinterServer",
    "Serve",
    "answering",
    "hanging_up",
    "resetting",
    "silent",
]

#: Longest wait for a server thread to end, and for a client to connect to it.
JOIN_SECONDS = 5.0

#: ``struct linger`` switched on with zero seconds: two ints on POSIX, two unsigned shorts on Windows.
LINGER_ZERO = struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0)

#: What a test hands :class:`PrinterServer`: the printer's side of one connection.
Play: TypeAlias = "Callable[[PrinterServer, socket.socket], None]"


class PrinterServer:
    """A server on an ephemeral port; each play acts the printer on one accepted socket, in order.

    It binds 127.0.0.1, never ``localhost``: GitHub's runners resolve ``localhost`` to ``::1`` first,
    where nothing listens. With ``refuse_for`` nothing is bound to the port for that many seconds,
    so every connect is refused, and only then does it listen. With ``queue_full_for`` it listens
    with a backlog of one and fills it with a connection of its own for that many seconds: Linux
    and macOS then drop a client's SYN, so its connect times out (Windows refuses it instead).
    ``next_connection_within`` bounds the wait for every connection after the first, so a play
    kept for a client that should NOT come back ends the thread soon instead of after
    :data:`JOIN_SECONDS`.
    """

    def __init__(
        self,
        *plays: Play,
        refuse_for: float = 0.0,
        queue_full_for: float = 0.0,
        next_connection_within: float = JOIN_SECONDS,
    ) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0), backlog=0 if queue_full_for else None)
        self.port: int = self.listener.getsockname()[1]
        # Made before any client can connect, so the client finds the queue already full.
        self._filler = socket.create_connection(("127.0.0.1", self.port)) if queue_full_for else None
        if refuse_for:
            # Nothing bound, not bound without listening: macOS ignores a SYN to a bound port that
            # does not listen, so the client would wait out its timeout instead of being refused.
            self.listener.close()
        else:
            # Bounded, so a test whose client never connects still ends the thread instead of leaking it.
            self.listener.settimeout(JOIN_SECONDS)
        self.received = bytearray()
        #: The bytes each connection received, one entry per accepted connection.
        self.connections: list[bytearray] = []
        self._plays = plays
        self._refuse_for = refuse_for
        self._queue_full_for = queue_full_for
        self._next_connection_within = next_connection_within
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
        if self._refuse_for:
            time.sleep(self._refuse_for)
            self.listener = socket.create_server(("127.0.0.1", self.port))
            self.listener.settimeout(JOIN_SECONDS)
        if self._filler is not None:
            time.sleep(self._queue_full_for)
            self._empty_the_queue(self._filler)
        for index, play in enumerate(self._plays):
            if index:
                self.listener.settimeout(self._next_connection_within)
            try:
                connection, _ = self.listener.accept()
            except OSError:
                # No client came within JOIN_SECONDS, or join() closed the listener first.
                return
            self.connections.append(bytearray())
            # Bounded too: a client that never closes would otherwise hold read_to_end, and the thread, forever.
            connection.settimeout(JOIN_SECONDS)
            with connection:
                play(self, connection)

    def _empty_the_queue(self, filler: socket.socket) -> None:
        """Accept and close the server's own queued connection, so the next client gets in."""
        queued, _ = self.listener.accept()
        queued.close()
        filler.close()

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
        self._record(data)
        return bytes(data)

    def read_to_end(self, connection: socket.socket) -> None:
        """Record everything the client sends until it closes the connection or falls silent."""
        # A client that closes with our answer still unread resets the connection: that is its end.
        # One silent for JOIN_SECONDS is treated the same, so the thread still ends.
        with contextlib.suppress(ConnectionResetError, TimeoutError):
            while chunk := connection.recv(65536):
                self._record(chunk)

    def _record(self, data: bytes | bytearray) -> None:
        self.received += data
        self.connections[-1] += data

    def join(self) -> None:
        """Wait up to :data:`JOIN_SECONDS` past any refusing time for the thread to end, then close the listener."""
        self._thread.join(self._refuse_for + self._queue_full_for + JOIN_SECONDS)
        self.listener.close()


class Serve(Protocol):
    """The ``serve`` fixture: start a :class:`PrinterServer` that the fixture joins at teardown."""

    def __call__(
        self, *plays: Play, refuse_for: float = ..., queue_full_for: float = ..., next_connection_within: float = ...
    ) -> PrinterServer: ...


def answering(request: bytes, reply: bytes) -> Play:
    """A printer that waits for ``request``, sends ``reply``, then records the rest until hang-up."""

    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, len(request))
        connection.sendall(reply)
        server.read_to_end(connection)

    return play


def hanging_up(request: bytes) -> Play:
    """A print server that takes ``request`` and closes the connection without a byte of answer."""

    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, len(request))

    return play


def resetting(server: PrinterServer, connection: socket.socket) -> None:
    """A print server that resets the connection on accept, before reading a byte of it."""
    # Linger on, zero seconds: the close that follows sends RST instead of FIN.
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, LINGER_ZERO)


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
