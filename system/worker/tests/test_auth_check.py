"""The auth stage: the whole flow, end to end, with the Backend faked.

The Worker's side of registration is complete and is exercised here from an
empty machine to a sealed identity. What is faked is the Backend itself; the
HTTP that talks to it is covered in ``test_worker_registry``.

What is genuinely missing is the benchmark. Registration has to state what the
machine can do and nothing measures that yet, so the stage stops before the
sign-in window rather than sending a placeholder. That is the subject of the
last class.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gshared_worker.auth_check import _http_origin_of, authenticate_worker
from gshared_worker.config import (
    CONFIG_FILE_NAME,
    DEFAULT_BACKEND_URL,
    WorkerSettings,
)
from gshared_worker.exit_codes import WorkerExitCode
from gshared_worker.logging_setup import DEBUG_LOG_NAME, start_debug_log, stop_debug_log
from gshared_worker.machine_facts import MachineFacts
from gshared_worker.worker_auth import RegistryAnswer, WorkerAuthState
from gshared_worker.worker_credentials import WorkerCredentials

SETTINGS = WorkerSettings(
    backend_url="ws://localhost:8080/ws/workers",
    worker_id="worker-001",
    hourly_price=600,
    gpu_index=0,
    supported_workloads=(),
    worker_token=None,
    config_path=None,
)

ISSUED = WorkerCredentials("worker-new", "issued-token")

FACTS = MachineFacts(
    device_name="DESKTOP-TEST",
    os_version="Windows 10.0.26200",
    gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU",
    vram_mb=8188,
    driver_version="596.36",
)

#: Stands in for the measurement this build cannot make yet.
MEASURED = 41.5


class Registry:
    def __init__(self, verifies=None, registers=None):
        self._verifies = verifies or RegistryAnswer(
            WorkerAuthState.REGISTERED, "accepted")
        self._registers = registers or RegistryAnswer(
            WorkerAuthState.REGISTERED, "registered", ISSUED)
        self.registered: list[str] = []
        self.described: list = []

    def verify(self, credentials):
        return self._verifies

    def register(self, access_token, registration):
        self.registered.append(access_token)
        self.described.append(registration)
        return self._registers


class StageTestCase(unittest.TestCase):
    def setUp(self):
        self._temporary = TemporaryDirectory()
        self.directory = Path(self._temporary.name)
        self.said = []
        start_debug_log(self.directory)
        # The stage reads and writes beside the Worker, which in a test is a
        # directory of our own rather than the developer's checkout.
        patcher = patch("gshared_worker.config.worker_directory",
                        lambda: self.directory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._clean_up)

    def _clean_up(self):
        stop_debug_log()
        self._temporary.cleanup()

    @property
    def path(self) -> Path:
        return self.directory / CONFIG_FILE_NAME

    def enrolled(self, worker_id="worker-001", sealed="sealed:the-token"):
        self.path.write_text(json.dumps({
            "workerId": worker_id, "workerTokenProtected": sealed,
        }), encoding="utf-8")

    def run_stage(self, registry, sign_in="user-jwt", benchmark=lambda: MEASURED):
        return authenticate_worker(
            SETTINGS, self.said.append,
            registry=registry,
            ask_to_sign_in=lambda: sign_in,
            describe=lambda: FACTS,
            benchmark=benchmark,
        )

    @property
    def output(self):
        return "\n".join(self.said)

    @property
    def logged(self):
        stop_debug_log()
        return (self.directory / DEBUG_LOG_NAME).read_text(encoding="utf-8")


class ANewMachineFromEmptyToRegisteredTest(StageTestCase):
    """The happy path this branch exists for."""

    def setUp(self):
        super().setUp()
        self.registry = Registry()
        with patch("gshared_worker.worker_credentials.protect_current_user",
                   lambda secret: "sealed:" + secret):
            self.code = self.run_stage(self.registry)

    def test_it_reaches_auth_ready(self):
        self.assertEqual(self.code, WorkerExitCode.OK)
        self.assertIn("AUTH READY", self.output)

    def test_the_person_is_asked_to_sign_in(self):
        self.assertIn("Sign in to attach it to your account", self.output)

    def test_the_access_token_is_what_registers_the_machine(self):
        self.assertEqual(self.registry.registered, ["user-jwt"])

    def test_the_issued_identity_is_written_sealed(self):
        written = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertEqual(written["workerId"], "worker-new")
        self.assertEqual(written["workerTokenProtected"], "sealed:issued-token")

    def test_the_access_token_is_never_written_to_the_credential_file(self):
        self.assertNotIn("user-jwt", self.path.read_text(encoding="utf-8"))

    def test_the_worker_it_became_is_named_on_screen(self):
        self.assertIn("worker-new", self.output)


class AnExistingMachineTest(StageTestCase):
    def test_it_is_authenticated_without_anybody_signing_in(self):
        asked = []

        with patch("gshared_worker.worker_credentials.unprotect_current_user",
                   lambda sealed: "the-token"):
            self.enrolled()
            code = authenticate_worker(
                SETTINGS, self.said.append, registry=Registry(),
                ask_to_sign_in=lambda: asked.append(1) or "jwt",
                describe=lambda: FACTS, benchmark=lambda: MEASURED)

        self.assertEqual(code, WorkerExitCode.OK)
        self.assertEqual(asked, [])
        self.assertIn("AUTH READY", self.output)

    def test_a_revoked_machine_stops_with_the_code_for_credentials(self):
        with patch("gshared_worker.worker_credentials.unprotect_current_user",
                   lambda sealed: "the-token"):
            self.enrolled()
            code = self.run_stage(Registry(verifies=RegistryAnswer(
                WorkerAuthState.REVOKED, "removed from the account")))

        self.assertEqual(code, WorkerExitCode.CREDENTIAL_MISMATCH)
        self.assertIn("no longer registered", self.output)

    def test_a_detail_that_says_something_new_is_still_shown(self):
        # Only the repetition is dropped, never the information.
        with patch("gshared_worker.worker_credentials.unprotect_current_user",
                   lambda sealed: "the-token"):
            self.enrolled()
            self.run_stage(Registry(verifies=RegistryAnswer(
                WorkerAuthState.REVOKED, "the service does not know this Worker")))

        self.assertIn("does not know this Worker", self.output)

    def test_a_refused_token_stops_the_same_way(self):
        with patch("gshared_worker.worker_credentials.unprotect_current_user",
                   lambda sealed: "the-token"):
            self.enrolled()
            code = self.run_stage(Registry(verifies=RegistryAnswer(
                WorkerAuthState.AUTH_FAILED, "refused")))

        self.assertEqual(code, WorkerExitCode.CREDENTIAL_MISMATCH)


class A404IsNotARevocationTest(StageTestCase):
    """A 404 must never cost a supplier their registration.

    It is what a missing Worker record answers, and equally what an endpoint
    that does not exist yet answers. Only the Backend saying WORKER_NOT_FOUND
    means the first one.
    """

    def run_404(self, state):
        with patch("gshared_worker.worker_credentials.unprotect_current_user",
                   lambda sealed: "the-token"):
            self.enrolled()
            self.registry = Registry(verifies=RegistryAnswer(
                state, "the service answered 404 at that address"))
            return self.run_stage(self.registry)

    def test_a_contract_error_is_not_a_credential_problem(self):
        code = self.run_404(WorkerAuthState.BACKEND_CONTRACT_ERROR)

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)
        self.assertNotEqual(code, WorkerExitCode.CREDENTIAL_MISMATCH)

    def test_it_says_the_machine_is_not_the_problem(self):
        self.run_404(WorkerAuthState.BACKEND_CONTRACT_ERROR)

        self.assertIn("did not answer", self.output)
        self.assertIn("problem with the service", self.output)

    def test_nothing_is_registered_again_and_nothing_is_written(self):
        for state in (WorkerAuthState.BACKEND_CONTRACT_ERROR,
                      WorkerAuthState.WORKER_NOT_FOUND):
            self.said.clear()
            self.run_404(state)

            self.assertEqual(self.registry.registered, [])
            written = json.loads(self.path.read_text(encoding="utf-8"))
            self.assertEqual(written["workerId"], "worker-001")

    def test_nobody_is_asked_to_sign_in(self):
        for state in (WorkerAuthState.BACKEND_CONTRACT_ERROR,
                      WorkerAuthState.WORKER_NOT_FOUND):
            asked = []
            with patch("gshared_worker.worker_credentials.unprotect_current_user",
                       lambda sealed: "the-token"):
                self.enrolled()
                authenticate_worker(
                    SETTINGS, self.said.append,
                    registry=Registry(verifies=RegistryAnswer(state, "404")),
                    ask_to_sign_in=lambda: asked.append(1) or "jwt",
                    describe=lambda: FACTS, benchmark=lambda: MEASURED)

            self.assertEqual(asked, [])

    def test_a_worker_the_service_has_no_record_of_is_told_apart(self):
        self.run_404(WorkerAuthState.WORKER_NOT_FOUND)

        self.assertIn("no record of this machine", self.output)
        self.assertNotIn("no longer registered", self.output)


class CredentialsThatCannotBeReadTest(StageTestCase):
    def setUp(self):
        super().setUp()
        self.enrolled(sealed="protected-for-another-account")
        self.registry = Registry()
        self.code = self.run_stage(self.registry)

    def test_it_stops_rather_than_registering_again(self):
        self.assertEqual(self.code, WorkerExitCode.CREDENTIAL_MISMATCH)
        self.assertEqual(self.registry.registered, [])

    def test_the_file_is_left_exactly_as_it_was(self):
        # Somebody put that there deliberately.
        written = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertEqual(written["workerTokenProtected"], "protected-for-another-account")

    def test_the_person_is_told_what_to_do_about_it(self):
        self.assertIn("Windows account", self.output)


class AServiceThatIsDownTest(StageTestCase):
    """The distinction that must never collapse."""

    def setUp(self):
        super().setUp()
        with patch("gshared_worker.worker_credentials.unprotect_current_user",
                   lambda sealed: "the-token"):
            self.enrolled()
            self.registry = Registry(verifies=RegistryAnswer(
                WorkerAuthState.BACKEND_UNREACHABLE, "could not be reached"))
            self.asked = []
            self.code = authenticate_worker(
                SETTINGS, self.said.append, registry=self.registry,
                ask_to_sign_in=lambda: self.asked.append(1) or "jwt",
                describe=lambda: FACTS, benchmark=lambda: MEASURED)

    def test_it_is_a_transport_failure_not_a_credential_one(self):
        # A launcher reads these differently: one is worth retrying.
        self.assertEqual(self.code, WorkerExitCode.TRANSPORT_FAILURE)

    def test_nobody_is_asked_to_sign_in_again(self):
        self.assertEqual(self.asked, [])
        self.assertEqual(self.registry.registered, [])

    def test_the_stored_identity_is_untouched(self):
        written = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertEqual(written["workerId"], "worker-001")

    def test_the_person_is_told_nothing_was_changed(self):
        self.assertIn("Nothing was changed", self.output)

    def test_the_same_sentence_is_not_printed_twice(self):
        # Two lines saying the same thing read as two separate problems.
        self.assertEqual(
            self.output.count("could not be reached"), 1)


class SomebodyWhoClosedTheWindowTest(StageTestCase):
    def test_it_is_not_a_fault_and_nothing_is_written(self):
        code = self.run_stage(Registry(), sign_in=None)

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)
        self.assertFalse(self.path.exists())

    def test_they_are_told_the_machine_is_still_unregistered(self):
        self.run_stage(Registry(), sign_in=None)

        self.assertIn("still unregistered", self.output)


class ARegistrationThatFailedTest(StageTestCase):
    def test_nothing_is_stored(self):
        # A half-registered machine is worse than an unregistered one.
        code = self.run_stage(Registry(registers=RegistryAnswer(
            WorkerAuthState.AUTH_FAILED, "that account cannot add Workers")))

        self.assertNotEqual(code, WorkerExitCode.OK)
        self.assertFalse(self.path.exists())


class NothingSensitiveReachesTheLogTest(StageTestCase):
    def test_the_access_token_is_not_logged(self):
        with patch("gshared_worker.worker_credentials.protect_current_user",
                   lambda secret: "sealed:" + secret):
            authenticate_worker(
                SETTINGS, self.said.append, registry=Registry(),
                ask_to_sign_in=lambda: "a-real-user-jwt",
                describe=lambda: FACTS, benchmark=lambda: MEASURED)

        self.assertNotIn("a-real-user-jwt", self.logged)

    def test_the_issued_worker_token_is_not_logged(self):
        with patch("gshared_worker.worker_credentials.protect_current_user",
                   lambda secret: "sealed:" + secret):
            authenticate_worker(
                SETTINGS, self.said.append, registry=Registry(),
                ask_to_sign_in=lambda: "jwt",
                describe=lambda: FACTS, benchmark=lambda: MEASURED)

        self.assertNotIn("issued-token", self.logged)

    def test_neither_reaches_the_screen(self):
        with patch("gshared_worker.worker_credentials.protect_current_user",
                   lambda secret: "sealed:" + secret):
            authenticate_worker(
                SETTINGS, self.said.append, registry=Registry(),
                ask_to_sign_in=lambda: "a-real-user-jwt",
                describe=lambda: FACTS, benchmark=lambda: MEASURED)

        self.assertNotIn("a-real-user-jwt", self.output)
        self.assertNotIn("issued-token", self.output)


class WhereTheBackendIsAskedTest(unittest.TestCase):
    def test_the_deployed_default_signs_in_over_https(self):
        # A password typed on a supplier's PC crosses the internet to reach
        # the service. The default address decides whether it is encrypted,
        # so the default is the thing worth pinning.
        origin = _http_origin_of(DEFAULT_BACKEND_URL)

        self.assertTrue(origin.startswith("https://"), origin)
        self.assertNotIn("localhost", origin)

    def test_the_socket_and_the_sign_in_reach_the_same_host(self):
        from urllib.parse import urlparse

        self.assertEqual(
            urlparse(DEFAULT_BACKEND_URL).netloc,
            urlparse(_http_origin_of(DEFAULT_BACKEND_URL)).netloc)

    def test_the_http_origin_comes_from_the_one_configured_address(self):
        # One setting, so the socket and the sign-in cannot point at two
        # different services.
        self.assertEqual(
            _http_origin_of("ws://localhost:8080/ws/workers"), "http://localhost:8080")
        self.assertEqual(
            _http_origin_of("wss://gshared.example/ws/workers"),
            "https://gshared.example")


class WhatTheMachineIsRegisteredAsTest(StageTestCase):
    """The registration request is built from this machine, not from defaults."""

    def setUp(self):
        super().setUp()
        self.registry = Registry()
        with patch("gshared_worker.worker_credentials.protect_current_user",
                   lambda secret: "sealed:" + secret):
            self.run_stage(self.registry)
        self.sent = self.registry.described[0].as_payload()

    def test_it_carries_the_fields_the_worker_api_asks_for(self):
        self.assertEqual(set(self.sent), {
            "deviceName", "osVersion", "gpuModel", "vramMb", "driverVersion",
            "benchmarkTokensPerSec",
        })

    def test_the_gpu_is_this_machine_s_own(self):
        self.assertEqual(self.sent["gpuModel"], FACTS.gpu_model)
        self.assertEqual(self.sent["vramMb"], 8188)
        self.assertEqual(self.sent["driverVersion"], "596.36")

    def test_the_benchmark_is_the_measured_figure(self):
        self.assertEqual(self.sent["benchmarkTokensPerSec"], MEASURED)

    def test_no_user_id_is_ever_sent(self):
        # The owner is decided by the Backend from the access token. A client
        # that names its own owner can name somebody else's.
        self.assertNotIn("userId", self.sent)

    def test_the_gpu_is_named_on_screen_before_anybody_signs_in(self):
        self.assertIn("RTX 4070", self.output)


class WithoutABenchmarkYetTest(StageTestCase):
    """What happens today: nothing measures what this GPU can do.

    Registering states ``benchmarkTokensPerSec``. Sending a placeholder would
    put a number the scheduler believes in front of a machine nobody measured,
    so the stage stops instead.
    """

    def test_it_stops_and_says_so(self):
        code = self.run_stage(Registry(), benchmark=None)

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)
        self.assertIn("cannot register this machine yet", self.output)

    def test_nobody_is_asked_for_a_password_that_could_not_be_used(self):
        asked = []

        authenticate_worker(
            SETTINGS, self.said.append, registry=Registry(),
            ask_to_sign_in=lambda: asked.append(1) or "jwt",
            describe=lambda: FACTS)

        self.assertEqual(asked, [])

    def test_nothing_is_registered_and_nothing_is_written(self):
        registry = Registry()

        self.run_stage(registry, benchmark=None)

        self.assertEqual(registry.registered, [])
        self.assertFalse(self.path.exists())

    def test_no_placeholder_figure_appears_anywhere(self):
        self.run_stage(Registry(), benchmark=None)

        self.assertNotIn("benchmark", self.logged.lower().replace(
            "no benchmark to state", ""))


class AMachineThatCannotBeDescribedTest(StageTestCase):
    """A GPU nvidia-smi would not name is nothing to offer."""

    def test_it_stops_before_the_window(self):
        asked = []

        code = authenticate_worker(
            SETTINGS, self.said.append, registry=Registry(),
            ask_to_sign_in=lambda: asked.append(1) or "jwt",
            describe=lambda: None, benchmark=lambda: MEASURED)

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)
        self.assertEqual(asked, [])
        self.assertIn("GPU could not be described", self.output)


if __name__ == "__main__":
    unittest.main()
