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

from gshared_worker.worker_wallet import WorkerWallet
from gshared_worker.auth_check import _http_origin_of, authenticate_worker
from gshared_worker.config import (
    CONFIG_FILE_NAME,
    DEFAULT_BACKEND_URL,
    WorkerSettings,
)
from gshared_worker.exit_codes import WorkerExitCode
from gshared_worker.logging_setup import DEBUG_LOG_NAME, start_debug_log, stop_debug_log
from gshared_worker.benchmark import (
    BenchmarkOutcome,
    BenchmarkReport,
    BenchmarkResult,
)
from gshared_worker.machine_facts import MachineFacts
from gshared_worker.worker_auth import RegistryAnswer, WorkerAuthState
from gshared_worker.worker_credentials import WorkerCredentials

#: A stub signing identity. Only the address matters to registration, and an
#: address is public -- the field below is a placeholder, not a real key.
A_WALLET = WorkerWallet(
    address="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
    private_key="not-a-real-key")


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

MEASURED_RATE = 1250.0


def measured(rate: float = MEASURED_RATE) -> BenchmarkReport:
    """A GPU that was measured. The real one runs a container; this does not."""
    return BenchmarkReport(
        BenchmarkOutcome.MEASURED, f"{rate:,.0f} tokens/sec over 3 runs",
        BenchmarkResult(
            tokens_per_sec=rate, processed_tokens=122880, elapsed_seconds=98.3,
            measured_steps=60, batch_size=4, sequence_length=512,
            run_rates=(rate, rate, rate)))


def unmeasurable(
    outcome: BenchmarkOutcome = BenchmarkOutcome.GPU_UNAVAILABLE,
    detail: str = "BENCHMARK_CUDA_UNAVAILABLE: no CUDA device in the container",
) -> BenchmarkReport:
    return BenchmarkReport(outcome, detail)


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

    def run_stage(self, registry, sign_in="user-jwt", benchmark=measured,
                  make_wallet=None):
        return authenticate_worker(
            SETTINGS, self.said.append,
            registry=registry,
            ask_to_sign_in=lambda: sign_in,
            describe=lambda: FACTS,
            benchmark=benchmark,
            # Injected: making a real key needs eth_account, which this package
            # deliberately does not require. The address is what registration
            # sends, and it is public.
            make_wallet=make_wallet or (lambda say: A_WALLET),
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
        # Unsealing is faked the same way as sealing: registration now reads the
        # stored identity straight back and authenticates it before going on.
        with patch("gshared_worker.worker_credentials.protect_current_user",
                   lambda secret: "sealed:" + secret),                 patch("gshared_worker.worker_credentials.unprotect_current_user",
                      lambda sealed: sealed.removeprefix("sealed:")):
            self.code = self.run_stage(self.registry)

    def test_it_reaches_auth_ready(self):
        self.assertEqual(self.code, WorkerExitCode.OK)
        self.assertIn("등록 확인 완료", self.output)

    def test_the_person_is_asked_to_sign_in(self):
        self.assertIn("로그인하면 계정에 연결됩니다", self.output)

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
                describe=lambda: FACTS, benchmark=measured,
                make_wallet=lambda say: A_WALLET)

        self.assertEqual(code, WorkerExitCode.OK)
        self.assertEqual(asked, [])
        self.assertIn("등록 확인 완료", self.output)

    def test_a_revoked_machine_stops_with_the_code_for_credentials(self):
        with patch("gshared_worker.worker_credentials.unprotect_current_user",
                   lambda sealed: "the-token"):
            self.enrolled()
            code = self.run_stage(Registry(verifies=RegistryAnswer(
                WorkerAuthState.REVOKED, "removed from the account")))

        self.assertEqual(code, WorkerExitCode.CREDENTIAL_MISMATCH)
        self.assertIn("더 이상 G-shared 계정에 등록되어 있지 않습니다", self.output)

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

        self.assertIn("그 요청에 응답하지 않았습니다", self.output)
        self.assertIn("서비스 쪽 문제", self.output)

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
                    describe=lambda: FACTS, benchmark=measured,
                make_wallet=lambda say: A_WALLET)

            self.assertEqual(asked, [])

    def test_a_worker_the_service_has_no_record_of_is_told_apart(self):
        self.run_404(WorkerAuthState.WORKER_NOT_FOUND)

        self.assertIn("등록 기록이 없습니다", self.output)
        self.assertNotIn("더 이상 G-shared 계정에 등록되어 있지 않습니다", self.output)


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
                describe=lambda: FACTS, benchmark=measured,
                make_wallet=lambda say: A_WALLET)

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
        self.assertIn("아무것도 바뀌지 않았습니다", self.output)

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

        self.assertIn("아직 등록되지 않았습니다", self.output)


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
                describe=lambda: FACTS, benchmark=measured,
                make_wallet=lambda say: A_WALLET)

        self.assertNotIn("a-real-user-jwt", self.logged)

    def test_the_issued_worker_token_is_not_logged(self):
        with patch("gshared_worker.worker_credentials.protect_current_user",
                   lambda secret: "sealed:" + secret):
            authenticate_worker(
                SETTINGS, self.said.append, registry=Registry(),
                ask_to_sign_in=lambda: "jwt",
                describe=lambda: FACTS, benchmark=measured,
                make_wallet=lambda say: A_WALLET)

        self.assertNotIn("issued-token", self.logged)

    def test_neither_reaches_the_screen(self):
        with patch("gshared_worker.worker_credentials.protect_current_user",
                   lambda secret: "sealed:" + secret):
            authenticate_worker(
                SETTINGS, self.said.append, registry=Registry(),
                ask_to_sign_in=lambda: "a-real-user-jwt",
                describe=lambda: FACTS, benchmark=measured,
                make_wallet=lambda say: A_WALLET)

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
            "operatorAddress", "clientVersion", "system", "gpu", "benchmark",
        })

    def test_the_gpu_is_this_machine_s_own(self):
        self.assertEqual(self.sent["gpu"]["gpuModel"], FACTS.gpu_model)
        self.assertEqual(self.sent["gpu"]["vramMb"], 8188)

    def test_the_driver_version_is_no_longer_sent(self):
        # Not part of the Backend contract: it is not an input to scheduling
        # and the Backend does not keep it.
        self.assertNotIn("driverVersion", json.dumps(self.sent))

    def test_the_benchmark_is_the_measured_figure(self):
        self.assertEqual(
            self.sent["benchmark"]["benchmarkTokensPerSec"], MEASURED_RATE)

    def test_the_benchmark_says_which_workload_produced_it(self):
        self.assertEqual(self.sent["benchmark"]["benchmarkVersion"],
                         "gshared-benchmark-v1")

    def test_the_operator_address_is_registered_with_the_machine(self):
        # Required. A Worker with no operator address cannot be approved on
        # chain, so it would be scheduled work it could never accept.
        self.assertEqual(self.sent["operatorAddress"], A_WALLET.address)

    def test_the_signing_key_itself_is_never_registered(self):
        raw = json.dumps(self.sent)

        for forbidden in ("privateKey", "private_key", "not-a-real-key",
                          "mnemonic", "seed"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, raw)

    def test_no_user_id_is_ever_sent(self):
        # The owner is decided by the Backend from the access token. A client
        # that names its own owner can name somebody else's.
        self.assertNotIn("userId", json.dumps(self.sent))

    def test_the_gpu_is_named_on_screen_before_anybody_signs_in(self):
        self.assertIn("RTX 4070", self.output)


class AGpuThatCannotBeMeasuredTest(StageTestCase):
    """No measurement, no registration. There is no fallback figure.

    A machine registered with a number nobody measured is a machine the
    scheduler sends work to on false pretences, so the flow stops instead.
    """

    def stopped_by(self, outcome, detail="it did not work"):
        self.said.clear()
        registry = Registry()
        code = self.run_stage(
            registry, benchmark=lambda: unmeasurable(outcome, detail))
        return code, registry

    def test_it_is_not_a_fault_and_says_what_happened(self):
        code, _ = self.stopped_by(BenchmarkOutcome.GPU_UNAVAILABLE,
                                  "BENCHMARK_CUDA_UNAVAILABLE: no CUDA device")

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)
        self.assertIn("GPU 성능을 측정하지 못해", self.output)
        self.assertIn("no CUDA device", self.output)

    def test_no_failure_registers_the_machine_or_writes_anything(self):
        for outcome in BenchmarkOutcome:
            if outcome is BenchmarkOutcome.MEASURED:
                continue
            _, registry = self.stopped_by(outcome)

            self.assertEqual(registry.registered, [], outcome)
            self.assertFalse(self.path.exists(), outcome)

    def test_no_invented_number_reaches_the_request(self):
        _, registry = self.stopped_by(BenchmarkOutcome.FAILED)

        self.assertEqual(registry.described, [])

    def test_the_person_is_told_nothing_was_changed(self):
        self.stopped_by(BenchmarkOutcome.TIMED_OUT)

        self.assertIn("아무것도 바뀌지 않았습니다", self.output)


class WhenTheGpuIsMeasuredTest(StageTestCase):
    """Only a machine somebody is actually registering is measured."""

    def test_a_machine_that_is_already_registered_is_not_measured(self):
        # Its number is recorded against it already. Spending a minute of
        # somebody GPU on every start to learn that again would be rude.
        measurements = []

        with patch("gshared_worker.worker_credentials.unprotect_current_user",
                   lambda sealed: "the-token"):
            self.enrolled()
            code = authenticate_worker(
                SETTINGS, self.said.append, registry=Registry(),
                ask_to_sign_in=lambda: "jwt", describe=lambda: FACTS,
                benchmark=lambda: measurements.append(1) or measured())

        self.assertEqual(code, WorkerExitCode.OK)
        self.assertEqual(measurements, [])

    def test_nobody_who_closed_the_window_has_their_gpu_measured(self):
        measurements = []

        code = self.run_stage(
            Registry(), sign_in=None,
            benchmark=lambda: measurements.append(1) or measured())

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)
        self.assertEqual(measurements, [])

    def test_it_happens_after_signing_in_not_before(self):
        order = []

        self.run_stage(
            Registry(),
            benchmark=lambda: order.append("measured") or measured())
        # The sign-in line is printed before the measuring line.
        self.assertLess(self.output.index("로그인하면 계정에 연결됩니다"),
                        self.output.index("GPU 성능을 측정하는 중"))
        self.assertEqual(order, ["measured"])

    def test_the_measurement_is_shown_to_the_person(self):
        self.run_stage(Registry())

        self.assertIn("tokens/sec", self.output)


class AMachineThatCannotBeDescribedTest(StageTestCase):
    """A GPU nvidia-smi would not name is nothing to offer."""

    def test_it_stops_before_the_window(self):
        asked = []

        code = authenticate_worker(
            SETTINGS, self.said.append, registry=Registry(),
            ask_to_sign_in=lambda: asked.append(1) or "jwt",
            describe=lambda: None, benchmark=measured)

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)
        self.assertEqual(asked, [])
        self.assertIn("GPU 정보를 읽지 못해", self.output)


if __name__ == "__main__":
    unittest.main()
