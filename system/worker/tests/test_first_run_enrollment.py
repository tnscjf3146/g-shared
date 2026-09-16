"""A machine nobody has set up, from the executable to the Backend connection.

The real order runs here: ``main.run`` -> ``start_the_machine`` ->
``authenticate_worker`` -> ``connect_to_backend``. What is faked is only what
this file cannot own -- the Backend, the sign-in window, the GPU benchmark
container, the signing-key library and DPAPI -- each at the seam the Worker
already uses for it. The environment and runtime checks have their own files
and are passed through as ready.

Before this, a first run stopped at the configuration step for want of a
workerId that only registration can issue, so the executable could never
register a machine.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gshared_worker import auth_check, worker_credentials
from gshared_worker import main as worker_main
from gshared_worker.benchmark import BenchmarkOutcome, BenchmarkReport, BenchmarkResult
from gshared_worker.config import CONFIG_FILE_NAME
from gshared_worker.exit_codes import WorkerExitCode
from gshared_worker.logging_setup import DEBUG_LOG_NAME, stop_debug_log
from gshared_worker.machine_facts import MachineFacts
from gshared_worker.worker_auth import RegistryAnswer, WorkerAuthState
from gshared_worker.worker_credentials import WorkerCredentials, load_credentials
from gshared_worker.worker_wallet import WorkerWallet

PASSWORD_NEVER_SEEN = "typed-into-the-window-only"
ACCESS_TOKEN = "user-access-jwt"
ISSUED = WorkerCredentials("worker-issued-7", "issued-worker-token")

FACTS = MachineFacts(
    device_name="DESKTOP-TEST", os_version="Windows 10.0.26200",
    gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU", vram_mb=8188,
    driver_version="596.36")

#: Only the address is registered. The key field is a placeholder string.
WALLET = WorkerWallet(address="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
                      private_key="not-a-real-key")


def measured() -> BenchmarkReport:
    return BenchmarkReport(
        BenchmarkOutcome.MEASURED, "1,250 tokens/sec over 3 runs",
        BenchmarkResult(tokens_per_sec=1250.0, processed_tokens=122880,
                        elapsed_seconds=98.3, measured_steps=60, batch_size=4,
                        sequence_length=512, run_rates=(1250.0, 1250.0, 1250.0)))


class Backend:
    """The Worker API: registers and verifies as a test says, and counts."""

    def __init__(self, registers=None, verifies=None):
        self.registers = registers or RegistryAnswer(
            WorkerAuthState.REGISTERED, "registered", ISSUED)
        self.verifies = verifies or RegistryAnswer(WorkerAuthState.REGISTERED, "accepted")
        self.register_calls: list[str] = []
        self.verify_calls: list[str] = []

    def register(self, access_token, registration):
        self.register_calls.append(access_token)
        return self.registers

    def verify(self, credentials):
        self.verify_calls.append(credentials.worker_id)
        return self.verifies


class Guard:
    held: set = set()

    def __init__(self, worker_id, gpu_index, *, lock_directory=None):
        self.identity = (worker_id, gpu_index)

    def acquire(self):
        if self.identity in Guard.held:
            return False
        Guard.held.add(self.identity)
        return True

    def release(self):
        Guard.held.discard(self.identity)


class FirstRunTestCase(unittest.TestCase):
    def setUp(self):
        Guard.held = set()
        self._temporary = TemporaryDirectory()
        self.directory = Path(self._temporary.name)
        self.addCleanup(self._clean_up)
        self.said: list[str] = []
        self.backend = Backend()
        self.windows_opened = 0
        self.sign_in_answer: str | None = ACCESS_TOKEN
        self.connected_with: list[WorkerCredentials] = []

        def open_window(settings):
            def ask():
                self.windows_opened += 1
                return self.sign_in_answer
            return ask

        def connect(settings, say):
            loaded = load_credentials()
            self.connected_with.append(loaded.credentials)
            return int(WorkerExitCode.OK)

        for patcher in (
            # Where the Worker reads and writes: a folder of our own.
            patch("gshared_worker.config.worker_directory", lambda: self.directory),
            patch.object(worker_main, "check_environment", lambda s, say: 0),
            patch.object(worker_main, "ensure_runtime", lambda s, say: 0),
            patch.object(worker_main, "connect_to_backend", connect),
            patch.object(auth_check, "_backend_registry", lambda s: self.backend),
            patch.object(auth_check, "_open_sign_in_window", open_window),
            patch.object(auth_check, "describe_machine", lambda: FACTS),
            patch.object(auth_check, "_run_the_benchmark", measured),
            patch.object(auth_check, "prepare_wallet", lambda say: WALLET),
            patch.object(worker_credentials, "protect_current_user",
                         lambda secret: "sealed:" + secret),
            patch.object(worker_credentials, "unprotect_current_user",
                         lambda sealed: sealed.removeprefix("sealed:")),
            # config.py imports this by name to read the stored token back.
            patch("gshared_worker.config.unprotect_current_user",
                  lambda sealed: sealed.removeprefix("sealed:")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _clean_up(self):
        stop_debug_log()
        self._temporary.cleanup()

    def run_worker(self) -> int:
        return worker_main.run(
            out=self.said.append,
            env={"LOCALAPPDATA": str(self.directory / "AppData")},
            directory=self.directory,
            run_worker=worker_main.start_the_machine,
            guard_factory=Guard,
        )

    @property
    def config(self) -> Path:
        return self.directory / CONFIG_FILE_NAME

    @property
    def output(self) -> str:
        return "\n".join(self.said)

    @property
    def logged(self) -> str:
        stop_debug_log()
        return (self.directory / DEBUG_LOG_NAME).read_text(encoding="utf-8")


class AFirstRunTest(FirstRunTestCase):
    def setUp(self):
        super().setUp()
        self.assertFalse(self.config.exists())
        self.code = self.run_worker()

    def test_it_does_not_stop_for_a_missing_worker_id(self):
        self.assertNotIn("not enrolled", self.output)
        self.assertEqual(self.code, WorkerExitCode.OK)

    def test_the_sign_in_window_is_opened_once(self):
        self.assertEqual(self.windows_opened, 1)

    def test_the_machine_is_registered_once_with_the_access_token(self):
        self.assertEqual(self.backend.register_calls, [ACCESS_TOKEN])

    def test_the_issued_identity_is_stored_sealed(self):
        written = json.loads(self.config.read_text(encoding="utf-8"))

        self.assertEqual(written["workerId"], ISSUED.worker_id)
        self.assertEqual(written["workerTokenProtected"], "sealed:" + ISSUED.worker_token)
        self.assertNotIn("workerToken", written)

    def test_what_was_stored_is_authenticated_before_connecting(self):
        # Read back through DPAPI and checked with the Backend, as every later
        # start does.
        self.assertEqual(self.backend.verify_calls, [ISSUED.worker_id])

    def test_it_goes_on_to_connect_as_the_worker_it_became(self):
        self.assertEqual(self.connected_with, [ISSUED])

    def test_neither_token_reaches_the_screen_or_the_log(self):
        for secret in (ACCESS_TOKEN, ISSUED.worker_token):
            self.assertNotIn(secret, self.output)
            self.assertNotIn(secret, self.logged)
        self.assertNotIn(ACCESS_TOKEN, self.config.read_text(encoding="utf-8"))


class TheSameMachineStartedAgainTest(FirstRunTestCase):
    def setUp(self):
        super().setUp()
        self.run_worker()
        self.backend.register_calls.clear()
        self.windows_opened = 0
        self.connected_with.clear()
        self.said.clear()

        self.code = self.run_worker()

    def test_nobody_is_asked_to_sign_in_again(self):
        self.assertEqual(self.code, WorkerExitCode.OK)
        self.assertEqual(self.windows_opened, 0)

    def test_it_is_not_registered_again(self):
        self.assertEqual(self.connected_with, [ISSUED])
        self.assertEqual(self.backend.register_calls, [])

    def test_it_authenticates_with_what_was_stored(self):
        self.assertEqual(self.backend.verify_calls[-1], ISSUED.worker_id)
        self.assertEqual(self.connected_with, [ISSUED])
        self.assertEqual(self.code, WorkerExitCode.OK)

    def test_it_names_the_worker_it_is(self):
        self.assertIn(f"Worker {ISSUED.worker_id}", self.output)


class WhenAFirstRunDoesNotFinishTest(FirstRunTestCase):
    def test_a_closed_window_changes_nothing_and_is_not_a_fault(self):
        self.sign_in_answer = None

        code = self.run_worker()

        self.assertNotIn(code, (WorkerExitCode.OK, WorkerExitCode.UNEXPECTED_FAULT))
        self.assertEqual(self.backend.register_calls, [])
        self.assertFalse(self.config.exists())
        self.assertEqual(self.connected_with, [])

    def test_a_refused_registration_stores_nothing_and_does_not_connect(self):
        self.backend.registers = RegistryAnswer(
            WorkerAuthState.AUTH_FAILED, "the access token was not accepted")

        code = self.run_worker()

        self.assertNotIn(code, (WorkerExitCode.OK, WorkerExitCode.UNEXPECTED_FAULT))
        self.assertFalse(self.config.exists())
        self.assertEqual(self.connected_with, [])

    def test_a_token_the_backend_refuses_right_away_does_not_connect(self):
        self.backend.verifies = RegistryAnswer(WorkerAuthState.AUTH_FAILED, "refused")

        code = self.run_worker()

        self.assertEqual(code, WorkerExitCode.CREDENTIAL_MISMATCH)
        self.assertEqual(self.connected_with, [])

    def test_a_backend_that_is_down_is_not_a_crash(self):
        self.backend.registers = RegistryAnswer(
            WorkerAuthState.BACKEND_UNREACHABLE, "the service could not be reached")

        code = self.run_worker()

        self.assertNotIn(code, (WorkerExitCode.OK, WorkerExitCode.UNEXPECTED_FAULT))
        self.assertFalse(self.config.exists())

    def test_a_wallet_creation_exception_is_reported_instead_of_exiting_silently(self):
        with patch.object(
            auth_check,
            "prepare_wallet",
            side_effect=OSError("wallet storage unavailable"),
        ):
            code = self.run_worker()

        self.assertEqual(code, WorkerExitCode.UNEXPECTED_FAULT)
        self.assertEqual(self.backend.register_calls, [])
        self.assertFalse(self.config.exists())
        self.assertIn("예상하지 못한 오류", self.output)
        self.assertIn("wallet storage unavailable", self.logged)

    def test_a_payload_exception_is_reported_before_any_request_is_sent(self):
        with patch.object(
            auth_check.WorkerRegistrationRequest,
            "describing",
            side_effect=ValueError("registration payload unavailable"),
        ):
            code = self.run_worker()

        self.assertEqual(code, WorkerExitCode.UNEXPECTED_FAULT)
        self.assertEqual(self.backend.register_calls, [])
        self.assertFalse(self.config.exists())
        self.assertIn("예상하지 못한 오류", self.output)
        self.assertIn("registration payload unavailable", self.logged)

    def test_it_can_be_finished_on_the_next_run(self):
        self.sign_in_answer = None
        self.run_worker()
        self.sign_in_answer = ACCESS_TOKEN

        self.assertEqual(self.run_worker(), WorkerExitCode.OK)
        self.assertEqual(self.backend.register_calls, [ACCESS_TOKEN])


class TwoFirstRunsTest(FirstRunTestCase):
    def test_the_second_is_turned_away_before_anything_is_registered(self):
        Guard.held.add((worker_main.UNREGISTERED_IDENTITY, 0))

        self.assertEqual(self.run_worker(), WorkerExitCode.ALREADY_RUNNING)
        self.assertEqual(self.windows_opened, 0)
        self.assertEqual(self.backend.register_calls, [])


class TheSignInButtonTest(unittest.TestCase):
    """A double click, or a click queued while the request is out, signs in once."""

    def window(self, presenter):
        from gshared_worker.gui.window import AuthWindow

        window = AuthWindow(presenter)
        window.rendered = []
        window._render = lambda shown: (window.rendered.append(shown),
                                        setattr(window, "_token", shown.access_token)
                                        if shown.signed_in else None)
        return window

    def test_a_click_arriving_while_the_first_is_out_is_ignored(self):
        from gshared_worker.gui.presenter import Screen, Shown

        calls = []

        class Presenter:
            def log_in(self, email, password):
                calls.append(email)
                # Tk delivering the queued second click inside the first.
                window._submit_login(email, password)
                return Shown(Screen.LOGIN, access_token=ACCESS_TOKEN)

        window = self.window(Presenter())
        window._submit_login("a@b.com", PASSWORD_NEVER_SEEN)

        self.assertEqual(calls, ["a@b.com"])
        self.assertEqual(len(window.rendered), 1)

    def test_nothing_is_sent_again_once_signed_in(self):
        from gshared_worker.gui.presenter import Screen, Shown

        calls = []

        class Presenter:
            def log_in(self, email, password):
                calls.append(email)
                return Shown(Screen.LOGIN, access_token=ACCESS_TOKEN)

        window = self.window(Presenter())
        window._submit_login("a@b.com", PASSWORD_NEVER_SEEN)
        window._submit_login("a@b.com", PASSWORD_NEVER_SEEN)

        self.assertEqual(calls, ["a@b.com"])

    def test_a_wrong_password_can_be_tried_again(self):
        from gshared_worker.gui.presenter import Screen, Shown

        calls = []

        class Presenter:
            def log_in(self, email, password):
                calls.append(email)
                return Shown(Screen.LOGIN, error="wrong password")

        window = self.window(Presenter())
        window._submit_login("a@b.com", "first")
        window._submit_login("a@b.com", "second")

        self.assertEqual(calls, ["a@b.com", "a@b.com"])
        self.assertTrue(all(shown.error for shown in window.rendered))


if __name__ == "__main__":
    unittest.main()
