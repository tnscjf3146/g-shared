"""Opening a Backend connection and getting this machine registered on it.

Reached only after AUTH READY, because the only credential this connection
accepts is the machine's own token, and until the stage before it has finished
there either is no token or nobody has checked that the Backend still accepts
it. The stage itself lives in :mod:`gshared_worker.worker_connection`.

Written against the Backend's **actual** implementation, read from
``back/src/main/java/com/gshared/back/worker/websocket``, not from a
specification:

* ``WorkerWebSocketConfig`` registers the handler at ``/ws/workers``.
* ``WorkerHandshakeInterceptor`` reads ``Authorization: Bearer <token>``,
  resolves the workerId **from the token**, and answers **401** when the header
  is missing or the token is not one it knows. The handshake is where a wrong
  credential is refused, so a refused handshake is a credential answer rather
  than a network one.
* ``WorkerWebSocketHandler.handleRegister`` marks the machine ONLINE and replies
  with ``REGISTER_ACK``.

Two details of that reply decide how this module reads it, and both are visible
in the Java rather than assumed:

* The envelope is annotated ``@JsonInclude(NON_NULL)`` and the reply is built
  with a null jobId, so **``jobId`` is absent from the ACK** rather than null.
* The ACK is built with ``emptyPayload()`` -- literally ``{}``. **It does not
  carry a workerId.** So a workerId in the ACK is checked when it is there and
  not demanded when it is not; see :func:`_ack_is_for_us`.

A connected socket is not a registered Worker. The Backend marks the machine
ONLINE when it receives REGISTER, not when the socket opens, so this module is
finished only once the ACK has been read.

**This module opens the connection; it does not keep it.** What comes back from
:func:`open_registered_socket` is a live :class:`RegisteredSocket` that the
caller owns and must close. Staying attached -- heartbeats, noticing the
connection has gone, reconnecting -- is :mod:`gshared_worker.worker_connection`.

The split is the same one as :mod:`gshared_worker.worker_auth` and
:mod:`gshared_worker.auth_check`: one module knows the protocol, the other
decides what to do about it over time.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

from .config import WorkerSettings
from .logging_setup import LOGGER_NAME
from .worker_credentials import WorkerCredentials

_debug = logging.getLogger(LOGGER_NAME)

#: Where the Backend listens. ``WorkerWebSocketConfig.registerWebSocketHandlers``
#: registers exactly this path, so it is written once here rather than joined
#: onto an address at each call site.
WS_PATH = "/ws/workers"

#: How long to wait for the socket itself to open.
CONNECT_TIMEOUT_SECONDS = 15

#: How long to wait for REGISTER_ACK after REGISTER has gone out.
#:
#: Short on purpose. The Backend answers this in the same method that receives
#: the REGISTER, so the only thing being waited on is a round trip. Waiting
#: minutes would leave a supplier looking at a program that appears to have
#: stopped, and the answer to a Backend that never replies is the same after
#: ten seconds as after ten minutes.
REGISTRATION_TIMEOUT_SECONDS = 15


class SocketOutcome(Enum):
    """How far the connection got. Only the first one is a working Worker."""

    #: Connected, REGISTER sent, REGISTER_ACK read. The Backend has this
    #: machine ONLINE.
    REGISTERED = "REGISTERED"

    #: The server answered the handshake and refused it. The Backend answers
    #: 401 for a token it does not accept, so this is about the credential --
    #: **not** about the network, and not worth retrying unchanged.
    HANDSHAKE_REFUSED = "HANDSHAKE_REFUSED"

    #: Nothing answered. A wrong address, a service that is down, a network
    #: that is not there. Says nothing about the credential.
    UNREACHABLE = "UNREACHABLE"

    #: Connected, REGISTER sent, and no ACK arrived in time.
    REGISTRATION_TIMED_OUT = "REGISTRATION_TIMED_OUT"

    #: Something arrived that is not a message this contract describes.
    MALFORMED_ACK = "MALFORMED_ACK"

    #: An ACK that names a different machine. Registering on the strength of it
    #: would have this Worker act as somebody else's.
    WRONG_WORKER = "WRONG_WORKER"

    #: The socket closed before the ACK. Distinct from a timeout: the server
    #: went away rather than stayed silent.
    CLOSED_BEFORE_ACK = "CLOSED_BEFORE_ACK"


@dataclass(frozen=True)
class SocketResult:
    outcome: SocketOutcome
    detail: str
    #: The handshake status, when the server answered with one. Kept because
    #: 401 and 404 mean different things to whoever is reading the log: one is
    #: a token this Backend does not know, the other is a path that is not
    #: there. Never carries a credential.
    status: int | None = None

    @property
    def registered(self) -> bool:
        return self.outcome is SocketOutcome.REGISTERED


class HandshakeRefused(Exception):
    """The server answered the HTTP handshake with something that is not 101.

    Carries the status and nothing else. In particular it does not carry the
    credential that was refused, which is the one thing a caller might be
    tempted to put in a message about a refused credential.
    """

    def __init__(self, status: int) -> None:
        super().__init__(f"the service refused the connection with HTTP {status}")
        self.status = status


class TransportUnavailable(Exception):
    """Nothing answered at all. The detail names the kind of failure, not a URL."""


class ConnectionWentAway(Exception):
    """The socket closed while we were still waiting to hear something."""


class ProtocolViolation(Exception):
    """Something arrived on the socket that is not a message this contract has.

    Broken JSON, a JSON value that is not an object, an envelope with no
    ``type``. Told apart from a message that is well formed and simply not one
    this build handles: that one is somebody else's to deal with, this one
    means the connection is not carrying the protocol any more.
    """


class WorkerSocketTransport(Protocol):
    """The whole of what registration needs from a WebSocket.

    A Protocol so the flow below can be driven without a server, and so the one
    place that knows about the ``websockets`` package is
    :func:`open_worker_socket`. Everything above this line is plain text in and
    plain text out.
    """

    def send(self, text: str) -> None: ...

    def recv(self, timeout: float) -> str:
        """The next message. Raises :class:`TimeoutError` when none arrives in
        time and :class:`ConnectionWentAway` when the socket closed instead."""

    def close(self) -> None: ...


#: Opens a socket to a URL with a token. The real one is
#: :func:`open_worker_socket`; tests supply their own.
OpenSocket = Callable[[str, str], WorkerSocketTransport]

#: What time it is, in ISO 8601. Injected so a test can pin it.
Clock = Callable[[], str]


def websocket_url(backend_url: str) -> str:
    """The address of the Worker socket, from whatever address is configured.

    The twin of ``_http_origin_of`` in :mod:`gshared_worker.auth_check`: one
    setting names the service and each side derives the scheme it needs, so a
    supplier cannot configure two addresses and get them out of step.

    Every form of the setting lands in the same place. ``https://host`` and
    ``wss://host`` are the same service, a trailing slash is not a different
    host, and a path that is already ``/ws/workers`` is not appended to twice --
    the path is taken from :data:`WS_PATH` rather than from the setting, so
    whatever was configured, exactly one of them comes out.
    """
    parsed = urlparse(backend_url)
    scheme = "wss" if parsed.scheme in ("wss", "https") else "ws"
    return f"{scheme}://{parsed.netloc}{WS_PATH}"


def register_message(worker_id: str, timestamp: str) -> dict[str, Any]:
    """The REGISTER envelope, in the shape ``WorkerMessage`` deserialises.

    ``jobId`` is null because this message is not about a job. The Backend's
    record has the field, so it is sent rather than left out.

    The workerId in the payload is what this machine believes it is. The
    Backend does not take its word for it: ``WorkerHandshakeInterceptor``
    resolved the workerId from the token before the socket existed, and
    ``handleRegister`` uses that one. A client that named its own identity and
    was believed could name somebody else's.

    **The token is not in here.** It travelled in the handshake header, which
    is the whole reason for putting it there.
    """
    return {
        "type": "REGISTER",
        "jobId": None,
        "timestamp": timestamp,
        "payload": {"workerId": worker_id},
    }


def now_in_iso8601() -> str:
    """Local time with its offset, matching the Backend's ``OffsetDateTime``."""
    return datetime.now().astimezone().isoformat()


class RegisteredSocket:
    """A connection the Backend has accepted this machine on.

    Only :func:`open_registered_socket` makes one, and it only makes one after
    REGISTER_ACK, so holding an instance means registration happened. That is
    the point of the class: "connected" and "registered" were two states of one
    variable before, and the Backend only marks a machine ONLINE on the second.

    **The caller owns it and closes it.** Whoever holds this holds a socket, and
    that is written down here rather than left to a ``finally`` somewhere.

    ``send`` and ``receive`` speak envelopes rather than text so the protocol is
    parsed in one place. ``receive`` hands back any well-formed message,
    including ones this build does not handle -- deciding what a message means
    is the caller's, refusing what is not a message at all is this class's.
    """

    def __init__(self, transport: WorkerSocketTransport, worker_id: str) -> None:
        self._transport = transport
        self.worker_id = worker_id

    def send(self, message: dict[str, Any]) -> None:
        """Put one envelope on the wire. Raises :class:`ConnectionWentAway`."""
        self._transport.send(json.dumps(message))

    def receive(self, timeout: float) -> dict[str, Any]:
        """The next envelope.

        Raises :class:`TimeoutError` when nothing arrives in time,
        :class:`ConnectionWentAway` when the socket closed instead, and
        :class:`ProtocolViolation` when what arrived is not a message.
        """
        return _as_envelope(self._transport.recv(timeout))

    def close(self) -> None:
        _close_quietly(self._transport)


def _as_envelope(raw: str) -> dict[str, Any]:
    """Text off the wire as a message, or a refusal to call it one."""
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        raise ProtocolViolation(
            "the service sent something that is not JSON") from None
    if not isinstance(message, dict):
        raise ProtocolViolation("the service sent a JSON value that is not a message")
    if not isinstance(message.get("type"), str) or not message["type"]:
        raise ProtocolViolation("the service sent a message with no type")
    return message


def open_registered_socket(
    settings: WorkerSettings,
    credentials: WorkerCredentials,
    *,
    open_socket: OpenSocket | None = None,
    clock: Clock | None = None,
    timeout: float = REGISTRATION_TIMEOUT_SECONDS,
) -> tuple[SocketResult, "RegisteredSocket | None"]:
    """Connect, say REGISTER, wait to be told the Backend heard it.

    On success the live socket comes back with the result and **the caller owns
    it from then on**. On every failure nothing is left open -- there is no path
    out of here that hands back a socket nobody was told about.

    Two return values rather than one because the two answers are different
    kinds of thing: the result is a fact to report and log, the socket is a
    resource to hold and close. Putting a live socket inside the frozen record
    the caller logs would blur that.
    """
    url = websocket_url(settings.backend_url)
    _debug.info("websocket: connecting to %s", url)

    try:
        transport = (open_socket or open_worker_socket)(url, credentials.worker_token)
    except HandshakeRefused as refused:
        _debug.info("websocket: handshake refused with HTTP %s", refused.status)
        return SocketResult(
            SocketOutcome.HANDSHAKE_REFUSED, str(refused), status=refused.status), None
    except TransportUnavailable as unavailable:
        _debug.info("websocket: could not be reached (%s)", unavailable)
        return SocketResult(SocketOutcome.UNREACHABLE, str(unavailable)), None

    result = _register_on(transport, credentials.worker_id, clock, timeout)
    if not result.registered:
        _close_quietly(transport)
        return result, None
    return result, RegisteredSocket(transport, credentials.worker_id)


def _register_on(
    socket: WorkerSocketTransport,
    worker_id: str,
    clock: Clock | None,
    timeout: float,
) -> SocketResult:
    """The conversation, once there is a socket to have it on."""
    try:
        socket.send(json.dumps(register_message(worker_id, (clock or now_in_iso8601)())))
    except ConnectionWentAway as gone:
        return SocketResult(SocketOutcome.CLOSED_BEFORE_ACK, str(gone))
    _debug.info("websocket: REGISTER sent for %s", worker_id)

    try:
        raw = socket.recv(timeout)
    except TimeoutError:
        return SocketResult(
            SocketOutcome.REGISTRATION_TIMED_OUT,
            f"the service did not confirm this machine within {timeout:g} seconds",
        )
    except ConnectionWentAway as gone:
        return SocketResult(SocketOutcome.CLOSED_BEFORE_ACK, str(gone))

    return _read_ack(raw, worker_id)


def _read_ack(raw: str, worker_id: str) -> SocketResult:
    """What arrived, and whether it means this machine is registered."""
    try:
        message = _as_envelope(raw)
    except ProtocolViolation as violation:
        return SocketResult(SocketOutcome.MALFORMED_ACK, str(violation))

    kind = message["type"]
    if kind != "REGISTER_ACK":
        # Anything else, named or not, leaves this machine unregistered. It is
        # not read as a confirmation just because a message arrived.
        return SocketResult(
            SocketOutcome.MALFORMED_ACK,
            f"the service replied with {kind!r} instead of confirming the registration",
        )

    if not _ack_is_for_us(message, worker_id):
        return SocketResult(
            SocketOutcome.WRONG_WORKER,
            "the service confirmed a different machine than this one",
        )

    _debug.info("websocket: REGISTER_ACK accepted for %s", worker_id)
    return SocketResult(SocketOutcome.REGISTERED, "this machine is registered with the service")


def _ack_is_for_us(message: dict[str, Any], worker_id: str) -> bool:
    """Whether an ACK that names a machine names this one.

    The Backend builds this reply with an empty payload, so **usually there is
    no name to check** and an ACK without one is taken at its word -- the
    handshake already proved which machine this is, because the token is what
    the workerId was resolved from.

    It is checked when it is there. If the Backend ever starts naming the
    machine it confirmed, a reply meant for a different one must not be read as
    this one's, and that check should already be in place on the day it starts.
    """
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return True
    named = payload.get("workerId")
    if named is None:
        return True
    return named == worker_id


def _close_quietly(socket: WorkerSocketTransport) -> None:
    """Give the socket back. Failing to close one is not worth failing over."""
    try:
        socket.close()
    except Exception:
        _debug.info("websocket: the connection could not be closed cleanly", exc_info=True)


def open_worker_socket(url: str, worker_token: str) -> WorkerSocketTransport:
    """The real connection, with the credential in the header where it belongs.

    ``websockets`` is imported here rather than at the top of the module so the
    rest of this file -- and every test of it -- runs without the package.

    **The token never reaches the URL.** A query parameter would be written to
    the Backend's access log, to any proxy in between, and to this Worker's own
    log line above. A header is not.
    """
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect

    try:
        connection = connect(
            url,
            additional_headers={"Authorization": f"Bearer {worker_token}"},
            open_timeout=CONNECT_TIMEOUT_SECONDS,
        )
    except InvalidStatus as refused:
        raise HandshakeRefused(refused.response.status_code) from None
    except Exception as error:
        # A wrong address, a service that is down, TLS that would not agree.
        # The type is enough to tell them apart in the log; the message can
        # carry the URL and there is no reason to repeat it.
        raise TransportUnavailable(
            f"the G-shared service could not be reached ({type(error).__name__})"
        ) from None
    return _WebsocketsTransport(connection)


class _WebsocketsTransport:
    """:class:`WorkerSocketTransport` over a ``websockets`` sync connection.

    Its exceptions are translated here so nothing above this class imports the
    package, and so a closed connection is one thing to catch rather than four.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def send(self, text: str) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            self._connection.send(text)
        except ConnectionClosed as closed:
            raise ConnectionWentAway(_why_it_closed(closed)) from None

    def recv(self, timeout: float) -> str:
        from websockets.exceptions import ConnectionClosed

        try:
            received = self._connection.recv(timeout=timeout)
        except ConnectionClosed as closed:
            raise ConnectionWentAway(_why_it_closed(closed)) from None
        # A binary frame is not something this contract sends, and decoding one
        # as text would turn a contract problem into a mojibake problem.
        if isinstance(received, bytes):
            return received.decode("utf-8", errors="replace")
        return received

    def close(self) -> None:
        self._connection.close()


def _why_it_closed(closed: Exception) -> str:
    """A sentence about a closed connection, without the library's repr."""
    return f"the connection to the G-shared service closed ({type(closed).__name__})"
