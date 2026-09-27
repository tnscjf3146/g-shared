"""A Worker deleted from the account, started again on the same PC.

The Backend drops a deleted Worker's token, so the identity left on disk is
answered with 401 ``INVALID_WORKER_TOKEN``. Before this, that ended the run with
"유효하지 않은 Worker 토큰입니다." and no way forward. Now the dead identity is
forgotten -- only ``workerId`` and the sealed token -- and the machine enrols
again, once, with the same signing key.

What must not change is just as much the subject: an outage, a 5xx, a timeout
or a 401 that does not say ``INVALID_WORKER_TOKEN`` still leaves the identity
exactly where it is.
"""

from __future__ import annotations

import io
import json
import socket
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gshared_worker import auth_check, worker_credentials
from gshared_worker import main as worker_main
from gshared_worker.auth_check import authenticate_worker
from gshared_worker.backend_client import BackendClient
from gshared_worker.benchmark import BenchmarkOutcome, BenchmarkReport, BenchmarkResult
from gshared_worker.config import CONFIG_FILE_NAME, WorkerSettings
from gshared_worker.exit_codes import WorkerExitCode
from gshared_worker.logging_setup import DEBUG_LOG_NAME, start_debug_log, stop_debug_log
from gshared_worker.machine_facts import MachineFacts
from gshared_worker.worker_auth import RegistryAnswer, WorkerAuthState
from gshared_worker.worker_credentials import (
    CredentialState,
    WorkerCredentials,
    clear_credentials,
    load_credentials,
)
from gshared_worker.worker_registry import (
    INVALID_WORKER_TOKEN,
    HttpWorkerRegistry,
    WorkerRegistrationRequest,
)
from gshared_worker.worker_wallet import WALLET_FILE_NAME, WorkerWallet

DEAD_TOKEN = "the-dead-worker-token"
ACCESS_TOKEN = "user-access-jwt"
ISSUED = WorkerCredentials("worker-reissued-9", "freshly-issued-token")

#: The machine's signing identity. Only the address is ever sent anywhere.
WALLET = WorkerWallet(address="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
                      private_key="not-a-real-key")
#: What is on disk for it. A stand-in for the sealed file; its bytes must not move.
WALLET_FILE = json.dumps({"address": WALLET.address,
                          "privateKeyProtected": "sealed-key-material"})

SETTINGS = WorkerSettings(
    backend_url="ws://localhost:8080/ws/workers", worker_id="worker-001",
    hourly_price=600, gpu_index=0, supported_workloads=(), worker_token=None,
    config_path=None)

FACTS = MachineFacts(
    device_name="DESKTOP-TEST", os_version="Windows 10.0.26200",
    gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU", vram_mb=8188,
    driver_version="596.36")

INVALIDATED = RegistryAnswer(WorkerAuthState.CREDENTIALS_INVALIDATED,
                             "the service no longer accepts the stored Worker token")
ACCEPTED = RegistryAnswer(WorkerAuthState.REGISTERED, "accepted")


def measured() -> BenchmarkReport:
    return BenchmarkReport(
        BenchmarkOutcome.MEASURED, "1,250 tokens/sec over 3 runs",
        BenchmarkResult(tokens_per_sec=1250.0, processed_tokens=122880,
                        elapsed_seconds=98.3, measured_steps=60, batch_size=4,
                        sequence_length=512, run_rates=(1250.0, 1250.0, 1250.0)))


def seal(secret: str) -> str:
    return "sealed:" + secret


def unseal(sealed: str) -> str:
    return sealed.removeprefix("sealed:")


# --------------------------------------------------------------------------
# The HTTP answer, read into a state
# --------------------------------------------------------------------------

def http_registry(status=200, body=None, *, raises=None):
    def opener(request, timeout=None):
        if raises is not None:
            raise raises
        payload = b"" if body is None else json.dumps(body).encode("utf-8")
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, status, "refused", {}, io.BytesIO(payload))
        response = io.BytesIO(payload)
        response.status = status
        response.__enter__ = lambda: response
        response.__exit__ = lambda *_: False
        return response
    return HttpWorkerRegistry(BackendClient("http://localhost:8080", opener=opener))


STORED = WorkerCredentials("worker-001", DEAD_TOKEN)

OFFERED = WorkerRegistrationRequest(
    operator_address=WALLET.address, client_version="0.1.0",
    os_version="Windows 10.0.26200", wsl_available=True,
    gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU", vram_mb=8188,
    benchmark_tokens_per_sec=1250.0, benchmark_version="gshared-benchmark-v1")


class WhatTheBackendSaidTest(unittest.TestCase):
    def test_401_invalid_worker_token_means_the_identity_is_dead(self):
        answered = http_registry(401, {"code": INVALID_WORKER_TOKEN,
                                       "message": "유효하지 않은 Worker 토큰입니다."}).verify(STORED)

        self.assertIs(answered.state, WorkerAuthState.CREDENTIALS_INVALIDATED)

    def test_the_code_is_the_one_the_backend_sends(self):
        self.assertEqual(INVALID_WORKER_TOKEN, "INVALID_WORKER_TOKEN")

    def test_a_401_with_any_other_code_is_not_that(self):
        for body in ({"code": "INVALID_TOKEN", "message": "refused"}, None):
            with self.subTest(body=body):
                self.assertIs(http_registry(401, body).verify(STORED).state,
                              WorkerAuthState.AUTH_FAILED)

    def test_a_401_on_registration_is_about_the_person_not_the_worker(self):
        answered = http_registry(401, {"code": INVALID_WORKER_TOKEN,
                                       "message": "x"}).register("jwt", OFFERED)

        self.assertIs(answered.state, WorkerAuthState.AUTH_FAILED)

    def test_a_5xx_is_an_outage(self):
        for status in (500, 502, 503):
            with self.subTest(status=status):
                self.assertIs(http_registry(status, {"code": "INTERNAL_SERVER_ERROR"})
                              .verify(STORED).state, WorkerAuthState.BACKEND_UNREACHABLE)

    def test_a_timeout_or_a_refused_connection_is_an_outage(self):
        for raised in (socket.timeout("timed out"),
                       urllib.error.URLError(ConnectionRefusedError("refused"))):
            with self.subTest(raised=type(raised).__name__):
                self.assertIs(http_registry(raises=raised).verify(STORED).state,
                              WorkerAuthState.BACKEND_UNREACHABLE)

    def test_a_403_is_still_a_revocation(self):
        self.assertIs(http_registry(403, {"code": "WORKER_FORBIDDEN"}).verify(STORED).state,
                      WorkerAuthState.REVOKED)

    def test_the_token_is_not_in_what_is_reported(self):
        answered = http_registry(401, {"code": INVALID_WORKER_TOKEN}).verify(STORED)

        self.assertNotIn(DEAD_TOKEN, answered.detail)


# --------------------------------------------------------------------------
# Forgetting the identity, and only the identity
# --------------------------------------------------------------------------

class ClearingTheIdentityTest(unittest.TestCase):
    def setUp(self):
        self._temporary = TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.directory = Path(self._temporary.name)
        self.config = self.directory / CONFIG_FILE_NAME
        self.wallet = self.directory / WALLET_FILE_NAME

    def write(self, values):
        self.config.write_text(json.dumps(values), encoding="utf-8")

    def test_only_the_worker_id_and_the_sealed_token_go(self):
        self.write({"workerId": "worker-001", "workerTokenProtected": seal(DEAD_TOKEN),
                    "backendUrl": "wss://j15e204.p.ssafy.io/ws/workers",
                    "hourlyPrice": 900, "gpuIndex": 1,
                    "escrowAddress": "0x66178DF08F5938d2ABC17496b6A98F7d776D3c56"})

        self.assertTrue(clear_credentials(self.directory))

        left = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(left, {"backendUrl": "wss://j15e204.p.ssafy.io/ws/workers",
                                "hourlyPrice": 900, "gpuIndex": 1,
                                "escrowAddress": "0x66178DF08F5938d2ABC17496b6A98F7d776D3c56"})

    def test_what_is_left_reads_as_a_machine_nobody_registered(self):
        self.write({"workerId": "worker-001", "workerTokenProtected": seal(DEAD_TOKEN)})

        clear_credentials(self.directory)

        self.assertIs(load_credentials(self.directory, unprotector=unseal).state,
                      CredentialState.ABSENT)

    def test_a_plaintext_token_from_an_earlier_version_goes_too(self):
        self.write({"workerId": "worker-001", "workerTokenProtected": seal(DEAD_TOKEN),
                    "workerToken": DEAD_TOKEN})

        clear_credentials(self.directory)

        self.assertNotIn(DEAD_TOKEN, self.config.read_text(encoding="utf-8"))

    def test_the_signing_key_file_is_never_opened(self):
        self.write({"workerId": "worker-001", "workerTokenProtected": seal(DEAD_TOKEN)})
        self.wallet.write_text(WALLET_FILE, encoding="utf-8")

        clear_credentials(self.directory)

        self.assertEqual(self.wallet.read_text(encoding="utf-8"), WALLET_FILE)

    def test_it_is_moved_into_place_and_leaves_no_temporary_file(self):
        self.write({"workerId": "worker-001", "workerTokenProtected": seal(DEAD_TOKEN)})

        clear_credentials(self.directory)

        self.assertFalse(list(self.directory.glob("*.new")))

    def test_nothing_to_clear_is_not_an_error(self):
        self.assertFalse(clear_credentials(self.directory))
        self.write({"hourlyPrice": 900})
        self.assertFalse(clear_credentials(self.directory))
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8")),
                         {"hourlyPrice": 900})

    def test_an_unreadable_file_is_left_exactly_as_it_was(self):
        self.config.write_text("{ not json", encoding="utf-8")

        self.assertFalse(clear_credentials(self.directory))
        self.assertEqual(self.config.read_text(encoding="utf-8"), "{ not json")


# --------------------------------------------------------------------------
# The auth stage
# --------------------------------------------------------------------------

class Registry:
    """Answers verify() from a script, in order, and counts both calls."""

    def __init__(self, verifies, registers=None):
        self._verifies = list(verifies)
        self.registers = registers or RegistryAnswer(
            WorkerAuthState.REGISTERED, "registered", ISSUED)
        self.verified: list[WorkerCredentials] = []
        self.registered: list = []

    def verify(self, credentials):
        self.verified.append(credentials)
        return self._verifies.pop(0) if len(self._verifies) > 1 else self._verifies[0]

    def register(self, access_token, registration):
        self.registered.append((access_token, registration))
        return self.registers


class StageTestCase(unittest.TestCase):
    def setUp(self):
        self._temporary = TemporaryDirectory()
        self.directory = Path(self._temporary.name)
        self.said: list[str] = []
        self.wallets_made = 0
        start_debug_log(self.directory)
        for patcher in (
            patch("gshared_worker.config.worker_directory", lambda: self.directory),
            patch.object(worker_credentials, "protect_current_user", seal),
            patch.object(worker_credentials, "unprotect_current_user", unseal),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._clean_up)
        # A machine that was registered before its Worker was deleted.
        self.config.write_text(json.dumps({
            "workerId": "worker-001", "workerTokenProtected": seal(DEAD_TOKEN),
            "hourlyPrice": 900, "gpuIndex": 0,
        }), encoding="utf-8")
        self.wallet_file.write_text(WALLET_FILE, encoding="utf-8")

    def _clean_up(self):
        stop_debug_log()
        self._temporary.cleanup()

    @property
    def config(self) -> Path:
        return self.directory / CONFIG_FILE_NAME

    @property
    def wallet_file(self) -> Path:
        return self.directory / WALLET_FILE_NAME

    def written(self) -> dict:
        return json.loads(self.config.read_text(encoding="utf-8"))

    def make_wallet(self, say):
        # The existing key, read back -- never a new one.
        self.wallets_made += 1
        return WALLET

    def run_stage(self, registry, sign_in=ACCESS_TOKEN):
        self.registry = registry
        return authenticate_worker(
            SETTINGS, self.said.append, registry=registry,
            ask_to_sign_in=lambda: sign_in, describe=lambda: FACTS,
            benchmark=measured, make_wallet=self.make_wallet)

    @property
    def output(self) -> str:
        return "\n".join(self.said)

    @property
    def logged(self) -> str:
        stop_debug_log()
        return (self.directory / DEBUG_LOG_NAME).read_text(encoding="utf-8")


class Case1AnIdentityTheBackendStillAcceptsTest(StageTestCase):
    def setUp(self):
        super().setUp()
        self.code = self.run_stage(Registry([ACCEPTED]))

    def test_it_goes_on_without_registering(self):
        self.assertEqual(self.code, WorkerExitCode.OK)
        self.assertEqual(self.registry.registered, [])

    def test_the_identity_is_kept(self):
        self.assertEqual(self.written()["workerId"], "worker-001")
        self.assertEqual(self.written()["workerTokenProtected"], seal(DEAD_TOKEN))

    def test_nothing_about_expiry_is_said(self):
        self.assertNotIn("만료", self.output)


class Case2AnIdentityTheBackendNoLongerAcceptsTest(StageTestCase):
    """The deleted Worker, with the person closing the window this time."""

    def setUp(self):
        super().setUp()
        self.code = self.run_stage(Registry([INVALIDATED]), sign_in=None)

    def test_the_worker_id_and_the_token_are_forgotten(self):
        self.assertNotIn("workerId", self.written())
        self.assertNotIn("workerTokenProtected", self.written())

    def test_every_other_setting_is_kept(self):
        self.assertEqual(self.written(), {"hourlyPrice": 900, "gpuIndex": 0})

    def test_the_signing_key_is_untouched(self):
        self.assertEqual(self.wallet_file.read_text(encoding="utf-8"), WALLET_FILE)

    def test_it_is_not_a_fault(self):
        self.assertNotIn(self.code, (WorkerExitCode.OK, WorkerExitCode.UNEXPECTED_FAULT))
        self.assertEqual(self.code, WorkerExitCode.EXPECTED_FAILURE)

    def test_the_person_is_told_in_plain_words(self):
        self.assertIn("기존 Worker 등록 정보가 만료되었습니다", self.output)
        self.assertIn("이 PC를 다시 등록합니다", self.output)
        self.assertNotIn("유효하지 않은 Worker 토큰", self.output)
        self.assertNotIn(INVALID_WORKER_TOKEN, self.output)

    def test_it_is_offered_the_way_to_register_again(self):
        self.assertIn("로그인하면 계정에 연결됩니다", self.output)

    def test_the_log_says_what_happened_without_the_token(self):
        logged = self.logged
        self.assertIn(INVALID_WORKER_TOKEN, logged)
        self.assertIn("stale worker credential cleared", logged)
        self.assertIn("re-registration required", logged)
        self.assertNotIn(DEAD_TOKEN, logged)

    def test_the_next_start_goes_straight_to_registering(self):
        self.said.clear()
        again = Registry([ACCEPTED])

        self.assertEqual(self.run_stage(again), WorkerExitCode.OK)
        self.assertEqual(len(again.registered), 1)


class Case3And4AnOutageLeavesTheIdentityAloneTest(StageTestCase):
    def test_a_5xx_or_a_timeout_changes_nothing(self):
        for answer in (
            RegistryAnswer(WorkerAuthState.BACKEND_UNREACHABLE,
                           "G-shared 서비스에 문제가 생겨 응답하지 못했습니다."),
            RegistryAnswer(WorkerAuthState.BACKEND_UNREACHABLE, "timed out"),
        ):
            with self.subTest(detail=answer.detail):
                code = self.run_stage(Registry([answer]))

                self.assertEqual(code, WorkerExitCode.TRANSPORT_FAILURE)
                self.assertEqual(self.written()["workerId"], "worker-001")
                self.assertEqual(self.written()["workerTokenProtected"], seal(DEAD_TOKEN))
                self.assertEqual(self.registry.registered, [])

    def test_any_other_refusal_changes_nothing_either(self):
        for state in (WorkerAuthState.AUTH_FAILED, WorkerAuthState.REVOKED,
                      WorkerAuthState.WORKER_NOT_FOUND,
                      WorkerAuthState.BACKEND_CONTRACT_ERROR):
            with self.subTest(state=state):
                self.run_stage(Registry([RegistryAnswer(state, "no")]))

                self.assertEqual(self.written()["workerId"], "worker-001")
                self.assertEqual(self.registry.registered, [])


class Case5RegisteringAgainTest(StageTestCase):
    def setUp(self):
        super().setUp()
        self.code = self.run_stage(Registry([INVALIDATED, ACCEPTED]))

    def test_it_ends_registered_as_the_new_worker(self):
        self.assertEqual(self.code, WorkerExitCode.OK)
        self.assertIn(ISSUED.worker_id, self.output)

    def test_the_new_identity_is_stored_sealed(self):
        self.assertEqual(self.written()["workerId"], ISSUED.worker_id)
        self.assertEqual(self.written()["workerTokenProtected"], seal(ISSUED.worker_token))
        self.assertEqual(self.written()["hourlyPrice"], 900)

    def test_it_is_registered_once_with_the_person_s_access_token(self):
        self.assertEqual([token for token, _ in self.registry.registered], [ACCESS_TOKEN])

    def test_the_new_identity_is_what_gets_authenticated(self):
        self.assertEqual([c.worker_id for c in self.registry.verified],
                         ["worker-001", ISSUED.worker_id])

    def test_it_registers_under_the_same_operator_address(self):
        _, registration = self.registry.registered[0]

        self.assertEqual(registration.as_payload()["operatorAddress"], WALLET.address)

    def test_the_signing_key_is_read_back_and_not_made_again(self):
        self.assertEqual(self.wallets_made, 1)
        self.assertEqual(self.wallet_file.read_text(encoding="utf-8"), WALLET_FILE)

    def test_no_token_reaches_the_screen_or_the_log(self):
        logged = self.logged
        for secret in (DEAD_TOKEN, ISSUED.worker_token, ACCESS_TOKEN):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, self.output)
                self.assertNotIn(secret, logged)


class Case6RegisteringAgainFailsTest(StageTestCase):
    def setUp(self):
        super().setUp()
        self.code = self.run_stage(Registry(
            [INVALIDATED],
            registers=RegistryAnswer(WorkerAuthState.BACKEND_UNREACHABLE,
                                     "the service could not be reached")))

    def test_it_is_tried_once_and_not_again(self):
        self.assertEqual(len(self.registry.registered), 1)
        self.assertEqual(len(self.registry.verified), 1)

    def test_it_stops_with_a_code_worth_retrying(self):
        self.assertEqual(self.code, WorkerExitCode.TRANSPORT_FAILURE)

    def test_nothing_half_registered_is_stored(self):
        self.assertEqual(self.written(), {"hourlyPrice": 900, "gpuIndex": 0})

    def test_the_next_start_can_still_register(self):
        self.said.clear()
        again = Registry([ACCEPTED])

        self.assertEqual(self.run_stage(again), WorkerExitCode.OK)
        self.assertEqual(self.written()["workerId"], ISSUED.worker_id)

    def test_no_token_reaches_the_log(self):
        self.assertNotIn(DEAD_TOKEN, self.logged)


class ABackendThatRefusesTheNewTokenTooTest(StageTestCase):
    """No loop: recovery happens once, and what enrolment ends in is reported."""

    def setUp(self):
        super().setUp()
        self.code = self.run_stage(Registry([INVALIDATED, INVALIDATED]))

    def test_it_registers_exactly_once(self):
        self.assertEqual(len(self.registry.registered), 1)
        self.assertEqual(len(self.registry.verified), 2)

    def test_it_stops_as_a_credential_problem(self):
        self.assertEqual(self.code, WorkerExitCode.CREDENTIAL_MISMATCH)
        self.assertIn("이 PC를 다시 등록해 주세요", self.output)


class WhenTheIdentityCannotBeForgottenTest(StageTestCase):
    def test_it_does_not_register_a_machine_that_would_still_read_the_dead_one(self):
        def cannot_write(directory=None):
            raise PermissionError("the file is read-only")

        with patch.object(auth_check, "clear_credentials", cannot_write):
            code = self.run_stage(Registry([INVALIDATED]))

        self.assertEqual(code, WorkerExitCode.CREDENTIAL_MISMATCH)
        self.assertEqual(self.registry.registered, [])


# --------------------------------------------------------------------------
# From the executable: stale identity to a connected Worker
# --------------------------------------------------------------------------

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


class TheExecutableStartedAfterTheWorkerWasDeletedTest(unittest.TestCase):
    def setUp(self):
        Guard.held = set()
        self._temporary = TemporaryDirectory()
        self.directory = Path(self._temporary.name)
        self.addCleanup(self._clean_up)
        self.said: list[str] = []
        self.windows_opened = 0
        self.connected_with: list[WorkerCredentials] = []
        self.backend = Registry([INVALIDATED, ACCEPTED])

        def open_window(settings):
            def ask():
                self.windows_opened += 1
                return ACCESS_TOKEN
            return ask

        def connect(settings, say):
            self.connected_with.append(load_credentials().credentials)
            return int(WorkerExitCode.OK)

        for patcher in (
            patch("gshared_worker.config.worker_directory", lambda: self.directory),
            patch.object(worker_main, "check_environment", lambda s, say: 0),
            patch.object(worker_main, "ensure_runtime", lambda s, say: 0),
            patch.object(worker_main, "connect_to_backend", connect),
            patch.object(auth_check, "_backend_registry", lambda s: self.backend),
            patch.object(auth_check, "_open_sign_in_window", open_window),
            patch.object(auth_check, "describe_machine", lambda: FACTS),
            patch.object(auth_check, "_run_the_benchmark", measured),
            patch.object(auth_check, "prepare_wallet", lambda say: WALLET),
            patch.object(worker_credentials, "protect_current_user", seal),
            patch.object(worker_credentials, "unprotect_current_user", unseal),
            patch("gshared_worker.config.unprotect_current_user", unseal),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        (self.directory / CONFIG_FILE_NAME).write_text(json.dumps({
            "workerId": "worker-001", "workerTokenProtected": seal(DEAD_TOKEN),
        }), encoding="utf-8")
        self.code = worker_main.run(
            out=self.said.append,
            env={"LOCALAPPDATA": str(self.directory / "AppData")},
            directory=self.directory,
            run_worker=worker_main.start_the_machine,
            guard_factory=Guard,
        )

    def _clean_up(self):
        stop_debug_log()
        self._temporary.cleanup()

    def test_it_reaches_the_connection_stage(self):
        self.assertEqual(self.code, WorkerExitCode.OK)

    def test_it_connects_as_the_newly_issued_worker(self):
        self.assertEqual(self.connected_with, [ISSUED])

    def test_the_person_signs_in_once(self):
        self.assertEqual(self.windows_opened, 1)
        self.assertEqual(len(self.backend.registered), 1)

    def test_no_token_reaches_the_screen_or_the_log(self):
        stop_debug_log()
        logged = (self.directory / DEBUG_LOG_NAME).read_text(encoding="utf-8")
        for secret in (DEAD_TOKEN, ISSUED.worker_token, ACCESS_TOKEN):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, "\n".join(self.said))
                self.assertNotIn(secret, logged)


if __name__ == "__main__":
    unittest.main()
