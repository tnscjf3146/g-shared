"""Tests for the current-user Named Pipe control surface."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import io
import json
import os
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import threading
import unittest

from gpu_worker import local_control
from gpu_worker.local_control import (
    ALLOWED_COMMANDS,
    MAX_REQUEST_BYTES,
    LocalControlClient,
    LocalControlRequest,
    LocalControlServer,
    WindowsLocalControlPipe,
    owner_only_security_descriptor,
    worker_control_pipe_name,
)
from gpu_worker.instance_guard import worker_instance_mutex_name
from uuid import uuid4


OWNER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
OTHER_SID = "S-1-5-21-0000000000-0000000000-0000000000-2002"
SECRET = "f2d5eec7a47e46c4a9616289832f2434-not-a-real-token"


class _ShutdownFlag:
    def __init__(self) -> None:
        self.event = threading.Event()

    def set(self) -> None:
        self.event.set()

    def is_set(self) -> bool:
        return self.event.is_set()


def _server(status: str = "IDLE") -> tuple[LocalControlServer, _ShutdownFlag]:
    shutdown = _ShutdownFlag()
    return (
        LocalControlServer(lambda: status, shutdown, owner_sid=OWNER_SID),
        shutdown,
    )


class LocalControlPolicyTests(unittest.TestCase):
    def test_only_status_and_graceful_shutdown_are_allowed(self) -> None:
        self.assertEqual(ALLOWED_COMMANDS, frozenset({"STATUS", "GRACEFUL_SHUTDOWN"}))

    def test_status_reports_the_current_lifecycle_status(self) -> None:
        for lifecycle in ("STARTING", "IDLE", "BUSY", "OFFLINE"):
            with self.subTest(lifecycle=lifecycle):
                server, _ = _server(lifecycle)

                response = server.handle(
                    LocalControlRequest(command="STATUS", peer_sid=OWNER_SID)
                )

                self.assertEqual(response.code, "OK")
                self.assertEqual(response.status, lifecycle)

    def test_graceful_shutdown_sets_the_shutdown_flag(self) -> None:
        server, shutdown = _server()

        response = server.handle(
            LocalControlRequest(command="GRACEFUL_SHUTDOWN", peer_sid=OWNER_SID)
        )

        self.assertEqual(response.code, "OK")
        self.assertTrue(shutdown.is_set())

    def test_status_never_sets_the_shutdown_flag(self) -> None:
        server, shutdown = _server()

        server.handle(LocalControlRequest(command="STATUS", peer_sid=OWNER_SID))

        self.assertFalse(shutdown.is_set())

    def test_unknown_commands_are_rejected(self) -> None:
        server, shutdown = _server()
        rejected = (
            "RUN",
            "EXECUTE",
            "SHUTDOWN",
            "status",
            "",
            "STATUS ",
            "STATUS;RUN",
            "GRACEFUL_SHUTDOWN\n",
        )

        for command in rejected:
            with self.subTest(command=command):
                response = server.handle(
                    LocalControlRequest(command=command, peer_sid=OWNER_SID)
                )

                self.assertEqual(response.code, "COMMAND_NOT_ALLOWED")
                self.assertIsNone(response.status)

        self.assertFalse(shutdown.is_set())

    def test_another_sid_is_denied_before_the_command_is_considered(self) -> None:
        server, shutdown = _server()

        for peer_sid in (OTHER_SID, None, "", OWNER_SID.lower()):
            with self.subTest(peer_sid=peer_sid):
                response = server.handle(
                    LocalControlRequest(
                        command="GRACEFUL_SHUTDOWN", peer_sid=peer_sid
                    )
                )

                self.assertEqual(response.code, "ACCESS_DENIED")

        self.assertFalse(shutdown.is_set())

    def test_a_denied_peer_cannot_learn_which_commands_exist(self) -> None:
        server, _ = _server()

        known = server.handle(
            LocalControlRequest(command="STATUS", peer_sid=OTHER_SID)
        )
        unknown = server.handle(
            LocalControlRequest(command="RUN", peer_sid=OTHER_SID)
        )

        self.assertEqual(known, unknown)

    def test_a_denied_peer_gets_one_answer_for_every_payload_shape(self) -> None:
        server, shutdown = _server()
        payloads = (
            b'{"command":"STATUS"}',
            b'{"command":"RUN"}',
            b'{"command":"STATUS","path":"cmd.exe"}',
            b"not-json",
            b"",
            b"a" * (MAX_REQUEST_BYTES + 1),
        )

        answers = {server.handle_payload(payload, OTHER_SID) for payload in payloads}

        self.assertEqual(answers, {b'{"code":"ACCESS_DENIED"}'})
        self.assertFalse(shutdown.is_set())

    def test_an_unreadable_status_never_looks_like_a_healthy_worker(self) -> None:
        def failing_provider() -> str:
            raise RuntimeError("nvml is gone")

        server = LocalControlServer(failing_provider, _ShutdownFlag(), owner_sid=OWNER_SID)

        response = server.handle(
            LocalControlRequest(command="STATUS", peer_sid=OWNER_SID)
        )

        self.assertEqual(response.status, "UNKNOWN")
        self.assertNotIn(response.status, ("IDLE", "BUSY"))


class LocalControlDecodingTests(unittest.TestCase):
    def test_an_exact_command_object_is_accepted(self) -> None:
        server, _ = _server()

        response = server.handle_payload(b'{"command":"STATUS"}', OWNER_SID)

        self.assertEqual(json.loads(response)["code"], "OK")

    def test_payloads_over_the_cap_are_refused_without_parsing(self) -> None:
        server, shutdown = _server()
        oversized = b'{"command":"GRACEFUL_SHUTDOWN","pad":"' + b"a" * MAX_REQUEST_BYTES

        response = json.loads(server.handle_payload(oversized, OWNER_SID))

        self.assertEqual(MAX_REQUEST_BYTES, 4096)
        self.assertEqual(response["code"], "REQUEST_TOO_LARGE")
        self.assertFalse(shutdown.is_set())

    def test_malformed_payloads_are_rejected(self) -> None:
        server, shutdown = _server()
        malformed = (
            b"",
            b"   ",
            b"not-json",
            b"[]",
            b'"STATUS"',
            b"null",
            b"123",
            b"{}",
            b'{"command":null}',
            b'{"command":123}',
            b'{"command":["STATUS"]}',
            b'{"Command":"STATUS"}',
            b"\xff\xfe\x00",
        )

        for payload in malformed:
            with self.subTest(payload=payload):
                response = json.loads(server.handle_payload(payload, OWNER_SID))

                self.assertEqual(response["code"], "MALFORMED_REQUEST")
                self.assertNotIn("status", response)

        self.assertFalse(shutdown.is_set())

    def test_no_extra_field_can_smuggle_a_path_or_shell_argument(self) -> None:
        server, shutdown = _server()
        smuggled = (
            b'{"command":"STATUS","path":"C:\\\\Windows\\\\System32\\\\cmd.exe"}',
            b'{"command":"STATUS","args":["-c","calc"]}',
            b'{"command":"GRACEFUL_SHUTDOWN","script":"Remove-Item C:\\\\"}',
            b'{"command":"STATUS","shell":true}',
            b'{"command":"STATUS","executable":"powershell"}',
        )

        for payload in smuggled:
            with self.subTest(payload=payload):
                response = json.loads(server.handle_payload(payload, OWNER_SID))

                self.assertEqual(response["code"], "MALFORMED_REQUEST")

        self.assertFalse(shutdown.is_set())

    def test_a_response_carries_only_a_code_and_an_optional_status(self) -> None:
        server, _ = _server("BUSY")

        answered = json.loads(server.handle_payload(b'{"command":"STATUS"}', OWNER_SID))
        refused = json.loads(server.handle_payload(b'{"command":"RUN"}', OWNER_SID))

        self.assertEqual(set(answered), {"code", "status"})
        self.assertEqual(set(refused), {"code"})


class LocalControlExposureTests(unittest.TestCase):
    def test_the_pipe_name_hides_the_raw_worker_id_and_is_local(self) -> None:
        name = worker_control_pipe_name(OWNER_SID, "worker-001", 0)

        self.assertTrue(name.startswith("\\\\.\\pipe\\GPUComputeWorkerControl-"))
        self.assertNotIn("worker-001", name)
        self.assertNotIn(OWNER_SID, name)
        self.assertEqual(name, worker_control_pipe_name(OWNER_SID, "worker-001", 0))
        self.assertNotEqual(name, worker_control_pipe_name(OTHER_SID, "worker-001", 0))
        self.assertNotEqual(
            worker_control_pipe_name(OWNER_SID, "worker-1", 10),
            worker_control_pipe_name(OWNER_SID, "worker-11", 0),
        )

    def test_the_enumerable_pipe_name_does_not_reveal_the_mutex_name(self) -> None:
        pipe = worker_control_pipe_name(OWNER_SID, "worker-001", 0)
        mutex = worker_instance_mutex_name(OWNER_SID, "worker-001", 0)

        pipe_digest = pipe.rsplit("-", 1)[1]
        mutex_digest = mutex.rsplit("-", 1)[1]

        self.assertNotEqual(pipe_digest, mutex_digest)
        self.assertNotIn(pipe_digest, mutex)

    def test_the_pipe_acl_and_the_accepted_peer_must_be_one_identity(self) -> None:
        server = LocalControlServer(lambda: "IDLE", _ShutdownFlag(), owner_sid=OWNER_SID)

        with self.assertRaises(ValueError):
            WindowsLocalControlPipe(
                server,
                worker_control_pipe_name(OWNER_SID, "worker-001", 0),
                owner_sid=OTHER_SID,
            )

    def test_the_security_descriptor_grants_only_the_owner(self) -> None:
        descriptor = owner_only_security_descriptor(OWNER_SID)

        self.assertEqual(descriptor, f"D:P(A;;GRGW;;;{OWNER_SID})")
        self.assertNotIn("WD", descriptor)
        self.assertNotIn("AU", descriptor)
        self.assertNotIn("BU", descriptor)

    def test_a_malformed_sid_never_reaches_the_security_descriptor(self) -> None:
        for value in ("", "not-a-sid", "S-1-5-21-1;WD", "S-1-5-21-1)(A;;GA;;;WD", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                owner_only_security_descriptor(value)

    def test_remote_pipe_clients_are_refused_by_the_pipe_mode(self) -> None:
        self.assertTrue(WindowsLocalControlPipe.PIPE_MODE & 0x8)
        self.assertEqual(WindowsLocalControlPipe.MAX_INSTANCES, 1)

    def test_local_control_never_opens_a_network_socket(self) -> None:
        source = Path(local_control.__file__).read_text(encoding="utf-8")

        for forbidden in ("socket", "AF_INET", "bind(", "listen(", "0.0.0.0", "127.0.0.1"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_no_token_or_backend_credential_can_reach_local_control(self) -> None:
        source = Path(local_control.__file__).read_text(encoding="utf-8").lower()

        for forbidden in ("worker_token", "workertoken", "bearer", "authorization",
                          "backend", "credential", "password", "worker-config"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_a_refused_request_logs_nothing_about_the_caller_or_secrets(self) -> None:
        server, _ = _server()
        out, err = io.StringIO(), io.StringIO()

        with redirect_stdout(out), redirect_stderr(err):
            server.handle_payload(
                json.dumps({"command": "RUN", "token": SECRET}).encode("utf-8"),
                OTHER_SID,
            )
            server.handle_payload(b"not-json " + SECRET.encode("utf-8"), OWNER_SID)

        written = out.getvalue() + err.getvalue()
        self.assertEqual(written, "")

    def test_a_refusal_never_echoes_the_request_back(self) -> None:
        server, _ = _server()

        response = server.handle_payload(
            json.dumps({"command": SECRET}).encode("utf-8"), OWNER_SID
        )

        self.assertNotIn(SECRET.encode("utf-8"), response)


@unittest.skipUnless(os.name == "nt", "local control is a Windows Named Pipe")
class WindowsLocalControlPipeTests(unittest.TestCase):
    def _running_pipe(self, status: str = "IDLE", *, timeout: float = 5.0):
        shutdown = _ShutdownFlag()
        sid = local_control.current_user_sid()
        server = LocalControlServer(lambda: status, shutdown, owner_sid=sid)
        pipe = WindowsLocalControlPipe(
            server,
            worker_control_pipe_name(sid, f"worker-test-{uuid4().hex[:12]}", 3),
            owner_sid=sid,
            connection_timeout_seconds=timeout,
        )
        pipe.start()
        self.addCleanup(pipe.close)
        return pipe, shutdown

    def _raw_client(self, pipe_name: str):
        """A client that speaks the wire directly, so it can misbehave."""
        import ctypes

        api = local_control._PipeApi()
        handle = api.kernel32.CreateFileW(
            pipe_name, 0x80000000 | 0x40000000, 0, None, 3, 0, None
        )
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise OSError("could not open the control pipe")
        self.addCleanup(api.close, handle)
        return api, handle

    def test_status_round_trips_over_a_real_pipe(self) -> None:
        pipe, _ = self._running_pipe("BUSY")

        response = LocalControlClient(pipe.pipe_name).status()

        self.assertEqual(response.code, "OK")
        self.assertEqual(response.status, "BUSY")

    def test_graceful_shutdown_round_trips_over_a_real_pipe(self) -> None:
        pipe, shutdown = self._running_pipe()

        response = LocalControlClient(pipe.pipe_name).graceful_shutdown()

        self.assertEqual(response.code, "OK")
        self.assertTrue(shutdown.event.wait(timeout=5))

    def test_an_unknown_command_is_refused_over_a_real_pipe(self) -> None:
        pipe, shutdown = self._running_pipe()

        response = LocalControlClient(pipe.pipe_name).send("RUN")

        self.assertEqual(response.code, "COMMAND_NOT_ALLOWED")
        self.assertFalse(shutdown.is_set())

    def test_the_pipe_handle_is_released_on_close(self) -> None:
        pipe, _ = self._running_pipe()
        name = pipe.pipe_name

        pipe.close()

        with self.assertRaises(OSError):
            LocalControlClient(name, connect_timeout_seconds=0.5).status()

    def test_a_client_that_never_ends_its_request_cannot_wedge_the_pipe(self) -> None:
        pipe, _ = self._running_pipe(timeout=1.0)
        api, handle = self._raw_client(pipe.pipe_name)
        written = ctypes.wintypes.DWORD()
        api.kernel32.WriteFile(handle, b'{"command":"STATUS"', 19, ctypes.byref(written), None)

        # The stalled caller is dropped at its deadline, so a later caller is served.
        response = LocalControlClient(
            pipe.pipe_name, connect_timeout_seconds=15.0
        ).status()

        self.assertEqual(response.code, "OK")

    def test_a_client_that_never_reads_its_answer_cannot_wedge_the_pipe(self) -> None:
        pipe, _ = self._running_pipe(timeout=1.0)
        api, handle = self._raw_client(pipe.pipe_name)
        written = ctypes.wintypes.DWORD()
        api.kernel32.WriteFile(
            handle, b'{"command":"STATUS"}\n', 21, ctypes.byref(written), None
        )

        response = LocalControlClient(
            pipe.pipe_name, connect_timeout_seconds=15.0
        ).status()

        self.assertEqual(response.code, "OK")

    def test_close_returns_promptly_even_while_a_client_stalls(self) -> None:
        pipe, _ = self._running_pipe(timeout=30.0)
        api, handle = self._raw_client(pipe.pipe_name)
        written = ctypes.wintypes.DWORD()
        api.kernel32.WriteFile(handle, b"{", 1, ctypes.byref(written), None)

        started = time.monotonic()
        pipe.close()
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 10.0)

    def test_the_client_reports_the_serving_process(self) -> None:
        pipe, _ = self._running_pipe()

        self.assertEqual(
            LocalControlClient(pipe.pipe_name).server_process_id(), os.getpid()
        )

    def test_a_closed_pipe_frees_the_name_for_a_later_worker(self) -> None:
        pipe, _ = self._running_pipe()
        name = pipe.pipe_name
        pipe.close()

        shutdown = _ShutdownFlag()
        sid = local_control.current_user_sid()
        server = LocalControlServer(lambda: "IDLE", shutdown, owner_sid=sid)
        again = WindowsLocalControlPipe(server, name, owner_sid=sid)
        again.start()
        self.addCleanup(again.close)

        self.assertEqual(LocalControlClient(name).status().code, "OK")


if __name__ == "__main__":
    unittest.main()
