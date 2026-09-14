"""The Job pipeline is really built into the running Worker.

Every component of a Job -- escrow decision, runtime, progress, settlement,
cancellation -- has its own tests. None of them notices if the connection
stage stops *using* them: a bare ``JobAcceptance()`` refuses every assignment
quietly, and every one of those tests still passes. That is exactly how the
packaged Worker shipped unable to run a Job.

So the first test here drives the production connection loop with no
acceptance handed in, and requires a JOB_ASSIGN to come back as JOB_ACCEPTED,
JOB_PROGRESS and JOB_RESULT. Only what the builder reaches outside the process
is replaced -- the signing key file, the node, the Docker runtime, the upload.
``escrow_decider`` and ``JobAcceptance`` are the real ones. If the connection
stage goes back to an unwired acceptance, or the builder stops injecting the
runtime or the settlement, this fails.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from gshared_worker import job_wiring
from gshared_worker.config import WorkerSettings
from gshared_worker.escrow import EscrowJob, EscrowSettings, FundedEvent, JobState
from gshared_worker.job_acceptance import refuse_without_escrow
from gshared_worker.job_cancellation import Cancellation
from gshared_worker.job_runtime import (
    STEP,
    TRAINING_STARTED,
    RuntimeEvent,
    RuntimeOutcome,
    RuntimeState,
)
from gshared_worker.job_settlement import JobResultReady
from gshared_worker.worker_connection import stay_connected
from gshared_worker.worker_credentials import WorkerCredentials
from gshared_worker.worker_wallet import WalletLoad, WalletState, WorkerWallet

OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
PROVIDER = "0x2222222222222222222222222222222222222222"
CLIENT = "0x1111111111111111111111111111111111111111"
EXTERNAL_REF = "0x" + "ab" * 32
PRICE_WEI = 9 * 10 ** 18
ESCROW_ADDRESS = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
RESULT_HASH = "0x" + "5e" * 32

CREDENTIALS = WorkerCredentials("worker-001", "gsh_wk_not-a-real-token")
#: Only the address is used for anything a test can see. Not a real key.
WALLET = WorkerWallet(address=OPERATOR, private_key="not-a-real-key")


def configured(**changes) -> WorkerSettings:
    values = dict(
        backend_url="wss://gpu.example.com/ws/workers", worker_id="worker-001",
        hourly_price=600, gpu_index=0, supported_workloads=(),
        worker_token=None, config_path=None,
        escrow_rpc_url="http://127.0.0.1:8545", escrow_address=ESCROW_ADDRESS,
        chain_id=31221)
    values.update(changes)
    return WorkerSettings(**values)


def present() -> WalletLoad:
    return WalletLoad(WalletState.PRESENT, WALLET)


def escrow_job(**changes) -> EscrowJob:
    values = {
        "client": CLIENT, "state": JobState.FUNDED, "deadline": 2_000_000_000,
        "provider": PROVIDER, "operator": "0x" + "00" * 20,
        "price_wei": PRICE_WEI, "result_hash": "0x" + "00" * 32,
    }
    values.update(changes)
    return EscrowJob(**values)


class FakeChain:
    """A node that agrees with the assignment and counts what was signed."""

    def __init__(self):
        self.sent: list = []
        self.reads = 0

    def chain_id(self):
        return 31221

    def has_contract_code(self):
        return True

    def read_job(self, escrow_job_id):
        self.reads += 1
        if self.sent:
            return escrow_job(state=JobState.ASSIGNED, operator=OPERATOR)
        return escrow_job()

    def find_funded_event(self, escrow_job_id):
        return FundedEvent(
            escrow_job_id=17, external_ref=EXTERNAL_REF, provider=PROVIDER,
            client=CLIENT, price_wei=PRICE_WEI, deadline=2_000_000_000)

    def is_operator(self, provider, operator):
        return True

    def now(self):
        return 1_000_000

    def send_accept(self, escrow_job_id):
        self.sent.append(("accept", escrow_job_id))
        return "0x" + "cd" * 32

    def send_abort(self, escrow_job_id):
        self.sent.append(("abort", escrow_job_id))
        return "0x" + "ef" * 32

    def receipt_status(self, transaction_hash, timeout):
        return 1


class FakePipeline:
    """Stands in for the Docker runtime. Reports two steps, then finishes."""

    built: list["FakePipeline"] = []

    def __init__(self, client, worker_token, worker_id, runner, settings, **kwargs):
        self.settings = settings
        self.runs: list[str] = []
        FakePipeline.built.append(self)

    def run(self, job, *, progress=None, terminal=None):
        self.runs.append(job.job_id)
        if progress is not None:
            progress.observe(RuntimeEvent(TRAINING_STARTED, {"totalSteps": 2}))
            progress.observe(RuntimeEvent(STEP, {"step": 1, "totalSteps": 2}))
        return RuntimeOutcome(RuntimeState.FINISHED, None, "the run finished",
                              actual_runtime_sec=12.5)


class FakeSettlement:
    """Stands in for packaging, upload and submitResult."""

    built: list["FakeSettlement"] = []

    def __init__(self, client, worker_token, chain, escrow_settings, operator_address, **kwargs):
        self.chain = chain
        self.operator_address = operator_address
        self.settled: list[str] = []
        FakeSettlement.built.append(self)

    def settle(self, job, outcome, terminal=None):
        self.settled.append(job.job_id)
        return JobResultReady(
            job_id=job.job_id, object_key=f"jobs/{job.job_id}/result.tar",
            result_hash=RESULT_HASH, submit_tx_hash="0x" + "ab" * 32,
            actual_runtime_sec=outcome.actual_runtime_sec)


def assignment(job_id="123") -> str:
    return json.dumps({
        "type": "JOB_ASSIGN", "jobId": job_id,
        "timestamp": "2026-09-10T14:00:00+09:00",
        "payload": {
            "dockerImageId": 1, "modelId": 3, "datasetId": 42,
            "trainingMode": "AUTO",
            "trainingConfig": {"epochs": 1, "batchSize": 4,
                               "learningRate": 0.0002, "maxLength": 512},
            "escrowJobId": "17", "externalRef": EXTERNAL_REF,
            "price": str(PRICE_WEI), "clientWallet": CLIENT,
            "providerWallet": PROVIDER,
        },
    })


class ScriptedBackend:
    """A Backend that registers, sends one JOB_ASSIGN, and answers heartbeats.

    Real time, short reads: the background threads the Worker starts are real,
    so the loop has to give them a moment rather than race a fake clock.
    """

    def __init__(self, job_json):
        self._pending = [job_json]
        self.sent: list[dict] = []
        self.lock = threading.Lock()

    def send(self, text):
        message = json.loads(text)
        with self.lock:
            self.sent.append(message)
        if message["type"] == "REGISTER":
            self._pending.insert(0, json.dumps({
                "type": "REGISTER_ACK", "timestamp": "2026-09-10T14:00:00+09:00",
                "payload": {"workerId": "worker-001"}}))
        elif message["type"] == "HEARTBEAT":
            self._pending.append(json.dumps({
                "type": "HEARTBEAT_ACK", "timestamp": "2026-09-10T14:00:00+09:00",
                "payload": {}}))

    def recv(self, timeout):
        if self._pending:
            return self._pending.pop(0)
        time.sleep(min(timeout, 0.02))
        # What the real socket raises when nothing arrived in time.
        raise TimeoutError

    def close(self):
        pass

    def types(self):
        with self.lock:
            return [m["type"] for m in self.sent]


class UntilResult:
    """Stops the connection loop once a terminal answer went out, or after 20s."""

    def __init__(self, backend):
        self._backend = backend
        self._deadline = time.monotonic() + 20

    def is_set(self):
        done = any(t in ("JOB_RESULT", "JOB_FAILED") for t in self._backend.types())
        return done or time.monotonic() > self._deadline

    def wait(self, timeout=None):
        time.sleep(min(timeout or 0, 0.05))
        return self.is_set()

    def set(self):
        pass


class TheRunningWorkerHandsAJobToThePipelineTest(unittest.TestCase):
    """JOB_ASSIGN -> escrow -> runtime -> progress -> settlement -> JOB_RESULT."""

    def setUp(self):
        FakePipeline.built.clear()
        FakeSettlement.built.clear()
        self.chain = FakeChain()
        self.folder = tempfile.mkdtemp()
        for patcher in (
            patch.object(job_wiring, "load_wallet", present),
            patch.object(job_wiring, "Web3Escrow", lambda settings, wallet: self.chain),
            patch.object(job_wiring, "RuntimePipeline", FakePipeline),
            patch.object(job_wiring, "Settlement", FakeSettlement),
            patch.object(job_wiring, "WslCommandRunner", lambda: object()),
            patch.object(job_wiring, "BackendClient", lambda origin: object()),
            patch.object(job_wiring, "data_directory", lambda: Path(self.folder)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.backend = ScriptedBackend(assignment())
        # No acceptance is passed in: the connection stage must build it.
        self.code = stay_connected(
            configured(), CREDENTIALS, lambda *parts: None, UntilResult(self.backend),
            open_socket=lambda url, token: self.backend,
            interval=0.2, ack_timeout=5)

    def sent(self, kind):
        return [m for m in self.backend.sent if m["type"] == kind]

    def test_the_escrow_decider_signed_the_acceptance(self):
        self.assertEqual(self.chain.sent, [("accept", 17)])

    def test_the_job_is_accepted_with_this_machines_operator_address(self):
        accepted = self.sent("JOB_ACCEPTED")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["jobId"], "123")
        self.assertIn(OPERATOR.lower(), json.dumps(accepted[0]).lower())

    def test_the_runtime_pipeline_ran_the_job(self):
        self.assertEqual(len(FakePipeline.built), 1)
        self.assertEqual(FakePipeline.built[0].runs, ["123"])

    def test_the_runtime_uses_this_workers_chain_and_contract(self):
        runtime = FakePipeline.built[0].settings
        self.assertEqual(runtime.chain_id, 31221)
        self.assertEqual(runtime.escrow_address, ESCROW_ADDRESS)
        self.assertEqual(runtime.workspace_root, Path(self.folder) / "work")
        self.assertEqual(runtime.cache_root, Path(self.folder) / "cache")

    def test_progress_reached_the_backend_through_the_progress_channel(self):
        progress = self.sent("JOB_PROGRESS")
        self.assertGreaterEqual(len(progress), 1)
        self.assertTrue(all(m["jobId"] == "123" for m in progress))

    def test_progress_went_out_before_the_result(self):
        kinds = self.backend.types()
        self.assertLess(kinds.index("JOB_PROGRESS"), kinds.index("JOB_RESULT"))

    def test_the_settlement_answered_through_the_terminal_channel_once(self):
        self.assertEqual(FakeSettlement.built[0].settled, ["123"])
        results = self.sent("JOB_RESULT")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["jobId"], "123")
        self.assertEqual(self.sent("JOB_FAILED"), [])

    def test_the_settlement_signs_with_the_same_chain_and_operator(self):
        self.assertIs(FakeSettlement.built[0].chain, self.chain)
        self.assertEqual(FakeSettlement.built[0].operator_address, OPERATOR)


class WhatTheBuilderInjectsTest(unittest.TestCase):
    def build(self, settings=None, **seams):
        options = dict(
            directory=Path(tempfile.mkdtemp()), load=present,
            make_chain=lambda settings, wallet: FakeChain(),
            runner=object(), client=object())
        options.update(seams)
        acceptance = job_wiring.build_job_acceptance(
            settings or configured(), CREDENTIALS, **options)
        self.addCleanup(acceptance.close)
        return acceptance

    def test_a_configured_worker_gets_every_part_of_the_pipeline(self):
        acceptance = self.build()

        self.assertIsNot(acceptance._decide, refuse_without_escrow)
        self.assertIsNotNone(acceptance._clean_up)
        self.assertIsNotNone(acceptance._run_job)
        self.assertIsNotNone(acceptance._settle_job)
        self.assertIsNotNone(acceptance._cancel_job)
        self.assertEqual(acceptance.operator_address, OPERATOR)

    def test_the_runtime_is_given_progress_and_the_terminal(self):
        acceptance = self.build()

        self.assertTrue(acceptance._run_job_takes_progress)
        self.assertTrue(acceptance._run_job_takes_terminal)
        self.assertTrue(acceptance._settle_takes_terminal)

    def test_a_worker_with_no_contract_refuses_everything(self):
        made = []
        acceptance = self.build(
            configured(escrow_address=None),
            make_chain=lambda settings, wallet: made.append(1))

        self.assertIs(acceptance._decide, refuse_without_escrow)
        self.assertIsNone(acceptance._run_job)
        self.assertEqual(made, [], "no chain client for a Worker with no contract")

    def test_a_worker_with_no_usable_key_refuses_everything(self):
        acceptance = self.build(load=lambda: WalletLoad(WalletState.ABSENT, detail="none"))

        self.assertIs(acceptance._decide, refuse_without_escrow)
        self.assertIsNone(acceptance._settle_job)

    def test_an_escrow_client_that_cannot_be_built_refuses_everything(self):
        def broken(settings, wallet):
            raise RuntimeError("the ABI does not match")

        acceptance = self.build(make_chain=broken)

        self.assertIs(acceptance._decide, refuse_without_escrow)
        self.assertIsNone(acceptance._cancel_job)

    def test_the_escrow_settings_are_this_workers_own(self):
        escrow = job_wiring.escrow_settings_of(configured(chain_id=31337))

        self.assertEqual(escrow, EscrowSettings(
            rpc_url="http://127.0.0.1:8545", escrow_address=ESCROW_ADDRESS,
            chain_id=31337))

    def test_a_cancellation_waits_on_this_acceptances_runtime(self):
        seen = {}

        class RecordingCancellation(Cancellation):
            def __init__(self, runner, chain, escrow_settings, operator_address, **kwargs):
                super().__init__(runner, chain, escrow_settings, operator_address, **kwargs)
                seen["wait"] = kwargs.get("wait_for_runtime")

            def cancel(self, job, terminal, outcome=None):
                seen["cancelled"] = (job, terminal, outcome)
                return "cancelled"

        with patch.object(job_wiring, "Cancellation", RecordingCancellation):
            acceptance = self.build()

        # Wired to this acceptance's runtime-finished event: set now, so the
        # wait returns at once.
        acceptance._runtime_done = threading.Event()
        acceptance._runtime_done.set()
        self.assertTrue(seen["wait"](0.01))
        self.assertEqual(acceptance._cancel_job("job", "terminal", "outcome"), "cancelled")
        self.assertEqual(seen["cancelled"], ("job", "terminal", "outcome"))


if __name__ == "__main__":
    unittest.main()
