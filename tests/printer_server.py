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

from datamax_dpl.adapters.memory import RecordingPrinter
from datamax_dpl.adapters.transport.uri import TcpUri
from datamax_dpl.domain.errors import PrinterTimeoutError
from datamax_dpl.domain.image_budget import image_data_bytes
from datamax_dpl.domain.status import MEMORY_DIRECTORY_REQUESTS, STATUS_REQUEST

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

__all__ = [
    "JOIN_SECONDS",
    "ConnectionsServer",
    "ImageModules",
    "ModulePrinter",
    "Play",
    "PrinterServer",
    "Serve",
    "answering",
    "answering_over",
    "hanging_up",
    "module_printer",
    "reading_slowly",
    "resetting",
    "silent",
    "trickling",
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
    :data:`JOIN_SECONDS`. ``receive_buffer`` shrinks the receive buffer of every accepted socket
    (``SO_RCVBUF``, set on the listener before any client connects, so the window the handshake
    offers is the small one), so a printer that reads slowly holds back a client after a few KB
    rather than after the hundreds of KB a loopback socket buffers by default.
    """

    def __init__(
        self,
        *plays: Play,
        refuse_for: float = 0.0,
        queue_full_for: float = 0.0,
        next_connection_within: float = JOIN_SECONDS,
        receive_buffer: int = 0,
    ) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0), backlog=0 if queue_full_for else None)
        if receive_buffer:
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
        self.port: int = self.listener.getsockname()[1]
        # Made before any client can connect, so the client finds the queue already full. Bounded,
        # so an OS that admits nothing at backlog 0 fails here at once instead of blocking for the
        # system's own connect timeout (21-75 s).
        self._filler = (
            socket.create_connection(("127.0.0.1", self.port), timeout=JOIN_SECONDS) if queue_full_for else None
        )
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
        self.record(data)
        return bytes(data)

    def read_to_end(self, connection: socket.socket) -> None:
        """Record everything the client sends until it closes the connection or falls silent."""
        # A client that closes with our answer still unread resets the connection: that is its end.
        # One silent for JOIN_SECONDS is treated the same, so the thread still ends.
        with contextlib.suppress(ConnectionResetError, TimeoutError):
            while chunk := connection.recv(65536):
                self.record(chunk)

    def read_paced(self, connection: socket.socket, *, step: int, every: float) -> None:
        """Record at most ``step`` bytes every ``every`` seconds until the client hangs up or falls silent.

        It paces itself by the clock rather than sleeping a fixed ``every`` after each read, so a
        thread the scheduler held back catches up instead of falling ever further behind its rate.
        """
        due = time.monotonic()
        # A client that gives up with bytes still unsent resets the connection: that is its end too.
        with contextlib.suppress(ConnectionResetError, TimeoutError):
            while chunk := connection.recv(step):
                self.record(chunk)
                due += every
                time.sleep(max(0.0, due - time.monotonic()))

    def record(self, data: bytes | bytearray) -> None:
        """Record ``data`` as received on the current connection, for a play that reads by itself."""
        self.received += data
        self.connections[-1] += data

    def join(self) -> None:
        """Wait up to :data:`JOIN_SECONDS` past any refusing time for the thread to end, then close the listener."""
        self._thread.join(self._refuse_for + self._queue_full_for + JOIN_SECONDS)
        self.listener.close()


class Serve(Protocol):
    """The ``serve`` fixture: start a :class:`PrinterServer` that the fixture joins at teardown."""

    def __call__(
        self,
        *plays: Play,
        refuse_for: float = ...,
        queue_full_for: float = ...,
        next_connection_within: float = ...,
        receive_buffer: int = ...,
    ) -> PrinterServer: ...


def answering(request: bytes, reply: bytes) -> Play:
    """A printer that waits for ``request``, sends ``reply``, then records the rest until hang-up."""

    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, len(request))
        connection.sendall(reply)
        server.read_to_end(connection)

    return play


def answering_over(request: bytes, reply: bytes, *, seconds: float) -> Play:
    """A printer that waits for ``request``, sends ``reply`` spread evenly over ``seconds``, then falls silent.

    The last byte goes out ``seconds`` after the request, by the clock, so a thread the scheduler
    held back catches up; the connection then stays open until the client hangs up, so only the
    client's own silence ends the answer.
    """

    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, len(request))
        start = time.monotonic()
        step = seconds / max(1, len(reply) - 1)
        with contextlib.suppress(OSError):
            for index, byte in enumerate(reply):
                time.sleep(max(0.0, start + index * step - time.monotonic()))
                connection.sendall(bytes([byte]))
            server.read_to_end(connection)

    return play


def trickling(request: bytes, reply: bytes, *, every: float, for_seconds: float) -> Play:
    """A printer that waits for ``request``, then sends ``reply`` one byte every ``every`` seconds.

    Its answer never falls silent long enough to end: it stops after ``for_seconds`` or when the
    client hangs up, then closes the connection.
    """

    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, len(request))
        end = time.monotonic() + for_seconds
        with contextlib.suppress(OSError):
            for byte in reply * (1 + int(for_seconds / every / max(1, len(reply)))):
                if time.monotonic() >= end:
                    break
                connection.sendall(bytes([byte]))
                time.sleep(every)

    return play


def hanging_up(request: bytes) -> Play:
    """A print server that takes ``request`` and closes the connection without a byte of answer."""

    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_exactly(connection, len(request))

    return play


def reading_slowly(*, step: int, every: float) -> Play:
    """A printer that takes ``step`` bytes every ``every`` seconds until the client hangs up."""

    def play(server: PrinterServer, connection: socket.socket) -> None:
        server.read_paced(connection, step=step, every=every)

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


#: The graphic directory request, the one request :class:`ImageModules` answers.
_GRAPHIC_DIRECTORY = MEMORY_DIRECTORY_REQUESTS["graphic"]
_STX = b"\x02"
_SOH = b"\x01"
#: Free bytes of an empty module A, as one Allegro 2 reported it (2026-10-31).
EMPTY_MODULE_BYTES = 49088


class ImageModules:
    """A printer's image modules as the fakes keep them: what a job's bytes store, clear and remove.

    It reads the stream a printer would, each command up to the next ``<STX>`` or ``<SOH>``, and
    answers ``<STX>WG`` with a directory of every module it has seen. A download (``<STX>I``) is
    stored while its module has room, each name costing its dot data (``image_data_bytes``); one
    that does not fit is lost with no error, as on one Allegro 2 (2026-10-31). ``lose`` names
    downloads (0 the first one seen) lost whatever the room, for a printer that drops one upload.
    ``<STX>q<m>`` empties module ``m``; ``<STX>R<name>`` removes the name from every module.
    ``answers`` False leaves every directory request unanswered.

    Attributes:
        stored: Each module's names, each with what it costs.
        capacity: Bytes of dot data one module holds.
        downloads: The name of every download seen, in order, stored or lost.
        requests: Directory requests seen.
    """

    def __init__(
        self,
        *,
        capacity: int = EMPTY_MODULE_BYTES,
        held: Iterable[tuple[str, str, int]] = (),
        lose: Iterable[int] = (),
        answers: bool = True,
    ) -> None:
        self.capacity = capacity
        self.stored: dict[str, dict[str, int]] = {}
        for module, name, cost in held:
            self.stored.setdefault(module, {})[name] = cost
        self.downloads: list[str] = []
        self.requests = 0
        self._lose = frozenset(lose)
        self._answers = answers
        self._pending = b""
        self._lock = threading.Lock()

    def feed(self, data: bytes) -> bytes:
        """Take ``data`` as the printer's input; return the answers it owes for it (empty when none)."""
        with self._lock:
            self._pending += data
            return b"".join(self._run(command) for command in self._complete_commands())

    def names(self, module: str) -> list[str]:
        """The names ``module`` holds, in the order they were stored."""
        return list(self.stored.get(module, {}))

    def _complete_commands(self) -> list[bytes]:
        """Take every command from the pending input that is known to be complete."""
        commands: list[bytes] = []
        while self._pending:
            following = [i for i in (self._pending.find(_STX, 1), self._pending.find(_SOH, 1)) if i > 0]
            if following:
                end = min(following)
            elif _ends_complete(self._pending):
                end = len(self._pending)
            else:
                break
            commands.append(self._pending[:end])
            self._pending = self._pending[end:]
        return commands

    def _run(self, command: bytes) -> bytes:
        """Do what ``command`` does to the modules; return its answer."""
        kind = command[1:2]
        if command == _GRAPHIC_DIRECTORY:
            self.requests += 1
            return self._directory() if self._answers else b""
        if command[:1] != _STX:
            return b""
        if kind == b"q":
            self.stored.pop(command[2:3].decode("ascii"), None)
        elif kind == b"R":
            name = command[2:].rstrip(b"\r").decode("ascii")
            for names in self.stored.values():
                names.pop(name, None)
        elif kind == b"I":
            self._download(command)
        return b""

    def _download(self, command: bytes) -> None:
        header, _, records = command.partition(b"\r")
        # <STX>I, the module letter, AF (7-bit ASCII image), the name.
        module, name = header[2:3].decode("ascii"), header[5:].decode("ascii")
        lost = len(self.downloads) in self._lose
        self.downloads.append(name)
        names = self.stored.setdefault(module, {})
        cost = image_data_bytes(records)
        if not lost and sum(names.values()) + cost <= self.capacity:
            names[name] = cost

    def _directory(self) -> bytes:
        parts: list[str] = []
        for module in sorted(self.stored) or ["A"]:
            names = self.stored.get(module, {})
            parts.append(f"MODULE: {module}\r")
            parts += [f"{name}\r" for name in names]
            parts.append(f"AVAILABLE BYTES IN MODULE : {self.capacity - sum(names.values()):08d}\r")
        return "".join(parts).encode("ascii")


def _ends_complete(pending: bytes) -> bool:
    """True when ``pending``, one command, is complete although no command follows it yet."""
    if pending == _GRAPHIC_DIRECTORY:
        return True
    kind, body = pending[1:2], pending[2:]
    if pending[:1] != _STX:
        return False
    complete: dict[bytes, bool] = {
        b"q": len(body) == 1,
        b"I": body.endswith(b"FFFF\r"),
        b"R": body.endswith(b"\r"),
        b"L": body.endswith(b"E\r"),
        b"o": True,
    }
    return complete.get(kind, False)


def module_printer(modules: ImageModules) -> Play:
    """A printer that runs its input through ``modules`` and answers their directory requests.

    Give one play per connection a test expects: ``modules`` is shared, so what one connection
    stored stays for the next, as in a printer.
    """

    def play(server: PrinterServer, connection: socket.socket) -> None:
        # A client that closes with an answer unread resets the connection: that is its end too.
        with contextlib.suppress(ConnectionResetError, TimeoutError):
            while chunk := connection.recv(65536):
                server.record(chunk)
                if reply := modules.feed(chunk):
                    connection.sendall(reply)

    return play


class ModulePrinter(RecordingPrinter):
    """A :class:`RecordingPrinter` whose image modules (:class:`ImageModules`) answer directory requests.

    Every byte sent, request or job, goes through ``modules`` as well as into the record, and a
    request the modules leave unanswered times out at once, as a silent printer would.
    """

    def __init__(self, modules: ImageModules, *, answers: Mapping[bytes, bytes] | None = None) -> None:
        super().__init__(answers=answers)
        self.modules = modules
        self._reply = b""

    def send(self, data: bytes) -> None:
        """Record ``data`` and run it through the modules, keeping what they answer."""
        super().send(data)
        self._reply += self.modules.feed(data)

    def read_answer(self, request: bytes, *, timeout: float, idle: float, total: float = 120.0) -> bytes:
        """Send ``request`` and return what the modules answer to it."""
        self._reply = b""
        self.send(request)
        self.queries.append(bytes(request))
        answer, self._reply = self._reply, b""
        if not answer:
            raise PrinterTimeoutError(f"module printer: no answer to {request!r} within {timeout} s")
        return answer
