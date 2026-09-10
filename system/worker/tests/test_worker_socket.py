"""The Backend connection, driven without a Backend.

Nothing here proves the Backend behaves this way. What is proved is that the
Worker sends what ``WorkerWebSocketHandler`` reads and reads what it sends --
the Java was the source for both, including the two things that are easy to get
wrong by writing to a specification instead: the ACK omits ``jobId`` entirely
(``@JsonInclude(NON_NULL)``) and its payload is ``{}`` rather than a workerId.

A connected socket is not a registered Worker, so most of this file is about
the ways the conversation can fail after the socket opens.
"""

from __future__ import annotations

import json
import unittest

from gshared_worker.config import WorkerSettings
from gshared_worker.exit_codes import WorkerExitCode
from gshared_worker.worker_credentials import WorkerCredentials
from gshared_worker.worker_socket import (
    WS_PATH,
    ConnectionWentAway,
    HandshakeRefused,
    SocketOutcome,
    TransportUnavailable,
    connect_to_backend,
    register_message,
    register_over_websocket,
    websocket_url,
)

TOKEN = "gsh_wk_a-secret-nobody-should-see"
CREDENTIALS = WorkerCredentials("worker-001", TOKEN)


def settings(backend_url: str = "wss://gpu.example.com/ws/workers") -> WorkerSettings:
    return WorkerSettings(
        backend_url=backend_url,
        worker_id="worker-001",
        hourly_price=600,
        gpu_index=0,
        supported_workloads=(),
        worker_token=None,
        config_path=None,
    )


def ack(worker_id: str | None = None, kind: str = "REGISTER_ACK") -> str:
    """What the Backend actually sends.

    ``handleRegister`` builds this with ``emptyPayload()`` and a null jobId,
    and the envelope drops null fields, so an ACK is three keys and an empty
    object. ``worker_id`` fills the payload only for the tests that ask what
    happens when a future Backend names the machine.
    """
    message = {
        "type": kind,
        "timestamp": "2026-09-10T12:00:01+09:00",
        "payload": {} if worker_id is None else {"workerId": worker_id},
    }
    return json.dumps(message)


class FakeSocket:
    """A socket that answers with whatever the test lined up."""

    def __init__(self, *replies: str, raises=None, on_send=None) -> None:
        self._replies = list(replies)
        self._raises = raises
        self._on_send = on_send
        self.sent: list[str] = []
        self.closed = False
        self.timeout_used: float | None = None

    def send(self, text: str) -> None:
        if self._on_send is not None:
            raise self._on_send
        self.sent.append(text)

    def recv(self, timeout: float) -> str:
        self.timeout_used = timeout
        if self._raises is not None:
            raise self._raises
        return self._replies.pop(0)

    def close(self) -> None:
        self.closed = True


def opener(socket: FakeSocket | None = None, raises=None):
    """An :data:`OpenSocket` that records what it was asked to connect with."""
    seen: dict = {}

    def open_socket(url: str, token: str):
        seen["url"] = url
        seen["token"] = token
        if raises is not None:
            raise raises
        return socket

    open_socket.seen = seen
    return open_socket


class TestTheAddress(unittest.TestCase):
    """One setting names the service; the scheme is derived, never configured."""

    def test_an_https_origin_becomes_a_wss_worker_socket(self):
        self.assertEqual(
            websocket_url("https://gpu.example.com"),
            "wss://gpu.example.com/ws/workers",
        )

    def test_an_http_origin_becomes_a_ws_worker_socket(self):
        self.assertEqual(
            websocket_url("http://localhost:8080"),
            "ws://localhost:8080/ws/workers",
        )

    def test_a_trailing_slash_does_not_double_the_separator(self):
        self.assertEqual(
            websocket_url("https://gpu.example.com/"),
            "wss://gpu.example.com/ws/workers",
        )

    def test_the_configured_default_shape_survives_a_round_trip(self):
        # The setting already carries the path. It is taken from WS_PATH
        # rather than from the setting, so it is not appended to twice.
        self.assertEqual(
            websocket_url("wss://j15e204.p.ssafy.io/ws/workers"),
            "wss://j15e204.p.ssafy.io/ws/workers",
        )

    def test_a_ws_address_stays_insecure(self):
        self.assertEqual(
            websocket_url("ws://localhost:8080/ws/workers"),
            "ws://localhost:8080/ws/workers",
        )

    def test_the_port_is_kept(self):
        self.assertTrue(websocket_url("https://host:9443").startswith("wss://host:9443"))

    def test_it_is_the_path_the_backend_registered(self):
        # WorkerWebSocketConfig.registerWebSocketHandlers uses this exact one.
        self.assertTrue(websocket_url("https://host").endswith(WS_PATH))

    def test_no_credential_is_ever_in_the_address(self):
        self.assertNotIn(TOKEN, websocket_url("https://gpu.example.com"))


class TestTheRegisterMessage(unittest.TestCase):
    def test_it_is_a_register(self):
        self.assertEqual(register_message("worker-001", "t")["type"], "REGISTER")

    def test_the_job_id_is_null_because_this_is_not_about_a_job(self):
        message = register_message("worker-001", "t")

        self.assertIn("jobId", message)
        self.assertIsNone(message["jobId"])

    def test_the_payload_names_this_machine(self):
        self.assertEqual(
            register_message("worker-001", "t")["payload"], {"workerId": "worker-001"})

    def test_the_envelope_has_exactly_the_four_agreed_fields(self):
        self.assertEqual(
            set(register_message("worker-001", "t")),
            {"type", "jobId", "timestamp", "payload"},
        )

    def test_the_timestamp_is_carried_through(self):
        stamp = "2026-09-10T12:00:00+09:00"

        self.assertEqual(register_message("worker-001", stamp)["timestamp"], stamp)

    def test_the_real_timestamp_is_iso_8601_with_an_offset(self):
        from datetime import datetime

        from gshared_worker.worker_socket import now_in_iso8601

        parsed = datetime.fromisoformat(now_in_iso8601())

        self.assertIsNotNone(parsed.tzinfo)


class TestNoCredentialTravelsInTheMessage(unittest.TestCase):
    """The token goes in the handshake header. Nowhere else."""

    def test_the_register_message_does_not_carry_the_token(self):
        socket = FakeSocket(ack())

        register_over_websocket(settings(), CREDENTIALS, open_socket=opener(socket))

        self.assertNotIn(TOKEN, socket.sent[0])

    def test_it_carries_no_user_credential_either(self):
        socket = FakeSocket(ack())

        register_over_websocket(settings(), CREDENTIALS, open_socket=opener(socket))

        for forbidden in ("password", "accessToken", "refreshToken", "workerToken"):
            self.assertNotIn(forbidden, socket.sent[0])

    def test_the_payload_holds_the_id_and_nothing_else(self):
        socket = FakeSocket(ack())

        register_over_websocket(settings(), CREDENTIALS, open_socket=opener(socket))

        self.assertEqual(json.loads(socket.sent[0])["payload"], {"workerId": "worker-001"})


class TestTheHandshake(unittest.TestCase):
    def test_the_token_goes_to_the_transport_for_the_header(self):
        open_socket = opener(FakeSocket(ack()))

        register_over_websocket(settings(), CREDENTIALS, open_socket=open_socket)

        self.assertEqual(open_socket.seen["token"], TOKEN)

    def test_the_url_it_connects_to_is_the_worker_socket(self):
        open_socket = opener(FakeSocket(ack()))

        register_over_websocket(
            settings("https://gpu.example.com"), CREDENTIALS, open_socket=open_socket)

        self.assertEqual(open_socket.seen["url"], "wss://gpu.example.com/ws/workers")

    def test_the_token_is_not_in_the_url(self):
        open_socket = opener(FakeSocket(ack()))

        register_over_websocket(settings(), CREDENTIALS, open_socket=open_socket)

        self.assertNotIn(TOKEN, open_socket.seen["url"])
        self.assertNotIn("token=", open_socket.seen["url"])

    def test_the_real_transport_puts_it_in_an_authorization_header(self):
        # Reads the one function that knows about the websockets package,
        # without opening a socket. The Backend's interceptor reads exactly
        # this header and this prefix.
        import inspect

        from gshared_worker import worker_socket

        source = inspect.getsource(worker_socket.open_worker_socket)

        self.assertIn("additional_headers", source)
        self.assertIn('"Authorization": f"Bearer {worker_token}"', source)


class TestARegistrationThatWorks(unittest.TestCase):
    def test_the_ack_the_backend_actually_sends_is_accepted(self):
        # payload {} and no jobId at all. This is the real reply.
        result = register_over_websocket(
            settings(), CREDENTIALS, open_socket=opener(FakeSocket(ack())))

        self.assertIs(result.outcome, SocketOutcome.REGISTERED)
        self.assertTrue(result.registered)

    def test_an_ack_that_does_name_this_machine_is_accepted_too(self):
        result = register_over_websocket(
            settings(), CREDENTIALS, open_socket=opener(FakeSocket(ack("worker-001"))))

        self.assertTrue(result.registered)

    def test_register_is_sent_before_anything_is_read(self):
        socket = FakeSocket(ack())

        register_over_websocket(settings(), CREDENTIALS, open_socket=opener(socket))

        self.assertEqual(json.loads(socket.sent[0])["type"], "REGISTER")

    def test_exactly_one_message_is_sent(self):
        # No heartbeat, no second hello.
        socket = FakeSocket(ack())

        register_over_websocket(settings(), CREDENTIALS, open_socket=opener(socket))

        self.assertEqual(len(socket.sent), 1)

    def test_the_socket_is_closed_afterwards(self):
        # Staying attached belongs to the heartbeat stage.
        socket = FakeSocket(ack())

        register_over_websocket(settings(), CREDENTIALS, open_socket=opener(socket))

        self.assertTrue(socket.closed)


class TestARegistrationThatDoesNot(unittest.TestCase):
    def test_an_ack_naming_a_different_machine(self):
        result = register_over_websocket(
            settings(), CREDENTIALS, open_socket=opener(FakeSocket(ack("worker-999"))))

        self.assertIs(result.outcome, SocketOutcome.WRONG_WORKER)
        self.assertFalse(result.registered)

    def test_a_reply_that_is_not_json(self):
        result = register_over_websocket(
            settings(), CREDENTIALS, open_socket=opener(FakeSocket("{ half written")))

        self.assertIs(result.outcome, SocketOutcome.MALFORMED_ACK)

    def test_a_reply_that_is_json_but_not_a_message(self):
        result = register_over_websocket(
            settings(), CREDENTIALS, open_socket=opener(FakeSocket('"hello"')))

        self.assertIs(result.outcome, SocketOutcome.MALFORMED_ACK)

    def test_a_reply_of_a_different_type(self):
        # HEARTBEAT_ACK is a real type. It is still not a confirmation of this.
        result = register_over_websocket(
            settings(), CREDENTIALS,
            open_socket=opener(FakeSocket(ack(kind="HEARTBEAT_ACK"))))

        self.assertIs(result.outcome, SocketOutcome.MALFORMED_ACK)

    def test_a_reply_with_no_type_at_all(self):
        result = register_over_websocket(
            settings(), CREDENTIALS, open_socket=opener(FakeSocket('{"payload": {}}')))

        self.assertIs(result.outcome, SocketOutcome.MALFORMED_ACK)

    def test_nothing_arriving_in_time(self):
        result = register_over_websocket(
            settings(), CREDENTIALS,
            open_socket=opener(FakeSocket(raises=TimeoutError())))

        self.assertIs(result.outcome, SocketOutcome.REGISTRATION_TIMED_OUT)

    def test_the_wait_is_bounded_and_does_not_actually_sleep(self):
        socket = FakeSocket(ack())

        register_over_websocket(
            settings(), CREDENTIALS, open_socket=opener(socket), timeout=2.5)

        self.assertEqual(socket.timeout_used, 2.5)

    def test_the_socket_closing_before_the_ack(self):
        result = register_over_websocket(
            settings(), CREDENTIALS,
            open_socket=opener(FakeSocket(raises=ConnectionWentAway("gone"))))

        self.assertIs(result.outcome, SocketOutcome.CLOSED_BEFORE_ACK)

    def test_the_socket_closing_before_register_could_be_sent(self):
        socket = FakeSocket(ack(), on_send=ConnectionWentAway("gone"))

        result = register_over_websocket(
            settings(), CREDENTIALS, open_socket=opener(socket))

        self.assertIs(result.outcome, SocketOutcome.CLOSED_BEFORE_ACK)

    def test_a_refused_handshake(self):
        result = register_over_websocket(
            settings(), CREDENTIALS, open_socket=opener(raises=HandshakeRefused(401)))

        self.assertIs(result.outcome, SocketOutcome.HANDSHAKE_REFUSED)
        self.assertEqual(result.status, 401)

    def test_a_service_that_is_not_there(self):
        result = register_over_websocket(
            settings(),
            CREDENTIALS,
            open_socket=opener(raises=TransportUnavailable("nothing answered")),
        )

        self.assertIs(result.outcome, SocketOutcome.UNREACHABLE)

    def test_every_failure_still_closes_the_socket(self):
        socket = FakeSocket(ack("worker-999"))

        register_over_websocket(settings(), CREDENTIALS, open_socket=opener(socket))

        self.assertTrue(socket.closed)

    def test_no_failure_leaks_the_token(self):
        for opened in (
            opener(FakeSocket("{ half written")),
            opener(FakeSocket(ack("worker-999"))),
            opener(FakeSocket(raises=TimeoutError())),
            opener(raises=HandshakeRefused(401)),
            opener(raises=TransportUnavailable("nothing answered")),
        ):
            result = register_over_websocket(
                settings(), CREDENTIALS, open_socket=opened)

            self.assertNotIn(TOKEN, result.detail)


class TestTheStage(unittest.TestCase):
    """What the supplier is told, and what the launcher is given."""

    def say(self):
        lines: list[str] = []
        return lines, lambda *parts: lines.append(" ".join(str(p) for p in parts))

    def test_a_registered_machine_exits_ok(self):
        lines, say = self.say()

        code = connect_to_backend(
            settings(), say,
            open_socket=opener(FakeSocket(ack())), credentials=CREDENTIALS)

        self.assertEqual(code, int(WorkerExitCode.OK))
        self.assertIn("WEBSOCKET REGISTERED.", lines)

    def test_a_service_that_is_down_is_worth_running_again(self):
        lines, say = self.say()

        code = connect_to_backend(
            settings(), say,
            open_socket=opener(raises=TransportUnavailable("nothing answered")),
            credentials=CREDENTIALS)

        self.assertEqual(code, int(WorkerExitCode.TRANSPORT_FAILURE))

    def test_a_token_the_service_refuses_is_not(self):
        lines, say = self.say()

        code = connect_to_backend(
            settings(), say,
            open_socket=opener(raises=HandshakeRefused(401)), credentials=CREDENTIALS)

        self.assertEqual(code, int(WorkerExitCode.CREDENTIAL_MISMATCH))

    def test_a_path_that_is_not_there_is_not_a_credential_problem(self):
        # 404 is the Backend not having the endpoint, not the token being wrong.
        lines, say = self.say()

        code = connect_to_backend(
            settings(), say,
            open_socket=opener(raises=HandshakeRefused(404)), credentials=CREDENTIALS)

        self.assertEqual(code, int(WorkerExitCode.EXPECTED_FAILURE))

    def test_an_ack_that_never_came_is_an_expected_failure(self):
        lines, say = self.say()

        code = connect_to_backend(
            settings(), say,
            open_socket=opener(FakeSocket(raises=TimeoutError())),
            credentials=CREDENTIALS)

        self.assertEqual(code, int(WorkerExitCode.EXPECTED_FAILURE))

    def test_the_supplier_is_told_what_to_do_about_a_service_that_is_down(self):
        lines, say = self.say()

        connect_to_backend(
            settings(), say,
            open_socket=opener(raises=TransportUnavailable("nothing answered")),
            credentials=CREDENTIALS)

        self.assertTrue(any("could not be reached" in line for line in lines))

    def test_nothing_it_prints_is_the_token(self):
        for opened in (
            opener(FakeSocket(ack())),
            opener(raises=HandshakeRefused(401)),
            opener(FakeSocket(ack("worker-999"))),
        ):
            lines, say = self.say()

            connect_to_backend(
                settings(), say, open_socket=opened, credentials=CREDENTIALS)

            self.assertNotIn(TOKEN, "\n".join(lines))

    def test_a_failure_never_claims_the_machine_is_registered(self):
        lines, say = self.say()

        connect_to_backend(
            settings(), say,
            open_socket=opener(FakeSocket(ack("worker-999"))), credentials=CREDENTIALS)

        self.assertNotIn("WEBSOCKET REGISTERED.", lines)


class TestItIsWiredIntoStartup(unittest.TestCase):
    def test_the_connection_stage_runs_after_the_auth_stage(self):
        import inspect

        from gshared_worker import main

        source = inspect.getsource(main.start_the_machine)

        self.assertLess(
            source.index("authenticate_worker"), source.index("connect_to_backend"))

    def test_a_machine_that_did_not_authenticate_never_connects(self):
        # The stage returns before it, rather than connecting and failing.
        import inspect

        from gshared_worker import main

        source = inspect.getsource(main.start_the_machine)
        between = source[source.index("authenticate_worker"):source.index("connect_to_backend")]

        self.assertIn("if code != WorkerExitCode.OK:", between)
        self.assertIn("return code", between)


class TestWhatThisBuildDoesNotDo(unittest.TestCase):
    """The next stage's work must not have leaked into this one."""

    def source(self):
        import inspect

        from gshared_worker import worker_socket

        return inspect.getsource(worker_socket)

    def test_there_is_no_heartbeat(self):
        self.assertNotIn("HEARTBEAT", self.source())

    def test_there_is_no_job_handling(self):
        for absent in ("JOB_ASSIGN", "JOB_ACCEPTED", "JOB_PROGRESS", "JOB_RESULT"):
            self.assertNotIn(absent, self.source())

    def test_there_is_no_reconnect_loop(self):
        source = self.source()

        self.assertNotIn("while True", source)
        self.assertNotIn("backoff", source.lower())

    def test_it_does_not_sleep(self):
        self.assertNotIn("time.sleep", self.source())


if __name__ == "__main__":
    unittest.main()
