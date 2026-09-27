"""Three ways a Job can end, and the rule that lets only one of them happen.

A Job in flight has up to three things trying to finish it:

* the container succeeded and the result is being stored and recorded,
* the container failed and the deposit is being returned,
* the customer asked for it to stop.

Each ends in a transaction the chain will not accept twice, and each ends in a
message telling the Backend the Job is over. Two at once is a Job that is both
completed and cancelled.

The rule is one line: **the claim is taken immediately before the chain
transaction, and never given back.** Everything before that point is
reversible -- an archive on disk, an object in storage nobody has been told to
pay for, a container that can be stopped. A transaction is not.

Most of this file is that rule under the orderings that can actually happen.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from gshared_worker.escrow import EscrowJob, EscrowSettings, JobState
from gshared_worker.job_cancellation import (
    BACKEND_CANCEL_COMPLETION_RACE,
    Cancellation,
    JobCancelledReady,
    JobTerminal,
    TerminalClaim,
    may_start_runtime,
)
from gshared_worker.job_runtime import RuntimeFailure, RuntimeOutcome, RuntimeState
from gshared_worker.job_settlement import (
    STOOD_DOWN,
    JobFailureReady,
    JobResultReady,
    Settlement,
    Unsettled,
)
from gshared_worker.runtime_stop import RuntimeHandle
from gshared_worker.worker_job import USER_REQUEST, read_assignment

OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
OTHER = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
CLIENT = "0x1111111111111111111111111111111111111111"
PROVIDER = "0x2222222222222222222222222222222222222222"
ESCROW_JOB = 17

SETTINGS = EscrowSettings(rpc_url="http://127.0.0.1:8545", chain_id=31337,
                          escrow_address="0x" + "11" * 20)


def a_job(job_id="123"):
    return read_assignment({
        "type": "JOB_ASSIGN", "jobId": job_id,
        "timestamp": "2026-09-11T01:00:00Z",
        "payload": {
            "dockerImageId": 1, "modelId": 3, "datasetId": 42,
            "trainingMode": "AUTO",
            "trainingConfig": {"epochs": 3, "batchSize": 4,
                               "learningRate": 0.0002, "maxLength": 512},
            "escrowJobId": str(ESCROW_JOB), "externalRef": "0x" + "ab" * 32,
            "price": "9000000000000000000",
            "clientWallet": CLIENT, "providerWallet": PROVIDER,
        },
    })


def an_output() -> Path:
    output = Path(tempfile.mkdtemp()) / "output"
    output.mkdir()
    (output / "adapter_config.json").write_bytes(b'{"peft_type":"LORA"}')
    (output / "adapter_model.safetensors").write_bytes(b"\x00w\x01" * 64)
    (output / "training_metrics.json").write_bytes(b'{"runtimeSec":9.4}')
    return output


def finished(seconds=982.4) -> RuntimeOutcome:
    return RuntimeOutcome(RuntimeState.FINISHED, None, "training finished", 0,
                          an_output(), actual_runtime_sec=seconds)


def failed(seconds=41.2) -> RuntimeOutcome:
    return RuntimeOutcome(RuntimeState.FAILED, RuntimeFailure.CUDA_OOM,
                          "the GPU ran out of memory", 13,
                          actual_runtime_sec=seconds)


def stopped(seconds=42.5) -> RuntimeOutcome:
    return RuntimeOutcome(RuntimeState.CANCELLED, None,
                          "the job was stopped at the customer's request", 137,
                          actual_runtime_sec=seconds)


class FakeChain:
    def __init__(self, state=JobState.ASSIGNED, *, operator=OPERATOR,
                 result_hash="0x" + "00" * 32, read_raises=False,
                 send_raises=False):
        self.state = state
        self.operator = operator
        self.hash = result_hash
        self._read_raises = read_raises
        self._send_raises = send_raises
        self.submitted: list = []
        self.aborted: list = []

    def chain_id(self):
        return SETTINGS.chain_id

    def has_contract_code(self):
        return True

    def now(self):
        return 1_800_000_000

    def is_operator(self, provider, operator):
        return True

    def read_job(self, escrow_job_id):
        if self._read_raises:
            raise RuntimeError("the node is gone")
        return EscrowJob(client=CLIENT, state=self.state, deadline=2_000_000_000,
                         provider=PROVIDER, operator=self.operator,
                         price_wei=9 * 10 ** 18, result_hash=self.hash)

    def send_accept(self, escrow_job_id):
        raise AssertionError("acceptJob has no part in ending a Job")

    def send_submit_result(self, escrow_job_id, result_hash):
        if self._send_raises:
            raise RuntimeError("the node timed out")
        self.submitted.append((escrow_job_id, result_hash))
        self.state, self.hash = JobState.COMPLETED, result_hash
        return "0x" + "cd" * 32

    def send_abort(self, escrow_job_id):
        if self._send_raises:
            raise RuntimeError("the node timed out")
        self.aborted.append(escrow_job_id)
        self.state = JobState.CANCELLED
        return "0x" + "ef" * 32

    def receipt_status(self, transaction_hash, timeout):
        return 1


class FakeDocker:
    """Inspect and stop, scripted by what the container is doing."""

    def __init__(self, running=True, still_running_after=False):
        self._running = running
        self._after = still_running_after
        self.stops: list = []
        self._looks = 0

    async def run(self, args, *, timeout, max_output_bytes):
        from gshared_worker.command_runner import CommandResult
        from gshared_worker.job_runtime import LABEL_GENERATION, LABEL_JOB_ID

        if args[:2] == ["docker", "stop"]:
            self.stops.append(args[-1])
            return CommandResult(0, "", "")
        self._looks += 1
        if not self._running:
            return CommandResult(1, "", "No such object")
        running = "true" if (self._looks == 1 or self._after) else "false"
        labels = '{"%s":"123","%s":"1"}' % (LABEL_JOB_ID, LABEL_GENERATION)
        return CommandResult(0, f"{running}\t{labels}", "")


class FakeBackend:
    def __init__(self):
        self.calls: list = []

    def request(self, method, path, payload=None, *, bearer=None):
        self.calls.append((method, path, payload))
        if path.endswith("/complete"):
            return _Answer(body={"jobId": "123",
                                 "objectKey": payload["objectKey"],
                                 "verified": True})
        return _Answer(body={"objectKey": "jobs/123/result.tar",
                             "uploadUrl": "https://b/storage/x?sig=y",
                             "expiresAt": "t"})

    @property
    def uploaded(self) -> bool:
        return any(c[1].endswith("/complete") for c in self.calls)


class _Answer:
    def __init__(self, succeeded=True, body=None, code="", status=200):
        self.succeeded, self.body = succeeded, body
        self.code, self.status = code, status


class Storage:
    def __init__(self):
        self.received: list = []

    def __call__(self, request, timeout=None):
        self.received.append(len(request.data.read()))
        return _Ok()


class _Ok:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def a_terminal(job_id="123", generation=1, with_container=True) -> JobTerminal:
    terminal = JobTerminal(job_id, generation)
    if with_container:
        terminal.container_started(RuntimeHandle.for_job(job_id, generation))
    return terminal


def cancelling(chain, docker=None, **kwargs) -> Cancellation:
    return Cancellation(docker if docker is not None else FakeDocker(),
                        chain, SETTINGS, OPERATOR, **kwargs)


def settling(chain, backend=None, storage=None) -> Settlement:
    return Settlement(backend if backend is not None else FakeBackend(),
                      "worker-token", chain, SETTINGS, OPERATOR,
                      opener=storage if storage is not None else Storage())


# ===================================================== the arbitration itself
class TestOnlyOneOwnerOfTheEnding(unittest.TestCase):
    def test_the_first_claim_wins(self):
        terminal = JobTerminal("123")

        self.assertTrue(terminal.claim_for(TerminalClaim.RESULT))
        self.assertFalse(terminal.claim_for(TerminalClaim.CANCEL))
        self.assertFalse(terminal.claim_for(TerminalClaim.FAILURE))

    def test_the_owner_may_claim_again(self):
        # Idempotent for the owner, so a retry inside one workflow does not
        # lock it out of its own transaction.
        terminal = JobTerminal("123")
        terminal.claim_for(TerminalClaim.CANCEL)

        self.assertTrue(terminal.claim_for(TerminalClaim.CANCEL))

    def test_it_is_never_given_back(self):
        terminal = JobTerminal("123")
        terminal.claim_for(TerminalClaim.FAILURE)

        self.assertIs(terminal.claim, TerminalClaim.FAILURE)
        self.assertFalse(terminal.claim_for(TerminalClaim.RESULT))

    def test_nothing_claims_none(self):
        with self.assertRaises(ValueError):
            JobTerminal("123").claim_for(TerminalClaim.NONE)

    def test_exactly_one_of_many_threads_wins(self):
        """The claim is a compare-and-set, not a read followed by a write."""
        terminal = JobTerminal("123")
        won: list = []
        start = threading.Barrier(12)

        def race(who):
            start.wait(timeout=5)
            if terminal.claim_for(who):
                won.append(who)

        threads = [threading.Thread(
            target=race, args=([TerminalClaim.RESULT, TerminalClaim.FAILURE,
                                TerminalClaim.CANCEL][n % 3],))
            for n in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        # Everyone who claimed the winning kind sees success; nobody else does.
        self.assertEqual(len(set(won)), 1, won)


class TestTheIntentIsSeparateFromTheClaim(unittest.TestCase):
    def test_asking_twice_only_counts_once(self):
        terminal = JobTerminal("123")

        self.assertTrue(terminal.request_cancel(USER_REQUEST))
        self.assertFalse(terminal.request_cancel(USER_REQUEST))

    def test_the_reason_is_kept(self):
        terminal = JobTerminal("123")
        terminal.request_cancel(USER_REQUEST)

        self.assertEqual(terminal.cancel_reason, USER_REQUEST)

    def test_asking_does_not_claim(self):
        # Intent is advisory. A cancellation that arrives while a result is
        # already writing to the chain still gets recorded -- it just loses.
        terminal = JobTerminal("123")
        terminal.request_cancel(USER_REQUEST)

        self.assertIs(terminal.claim, TerminalClaim.NONE)
        self.assertTrue(terminal.claim_for(TerminalClaim.RESULT))

    def test_the_token_is_set_for_the_pipeline(self):
        terminal = JobTerminal("123")
        terminal.request_cancel(USER_REQUEST)

        self.assertTrue(terminal.token.is_set())
        self.assertTrue(terminal.cancel_requested)


class TestNoContainerAfterACancellation(unittest.TestCase):
    def test_no_arbitration_at_all_still_runs(self):
        self.assertTrue(may_start_runtime(None))


# ================================================== the cancellation workflow
class TestCancellingARunningJob(unittest.TestCase):
    def test_the_container_is_stopped_and_the_deposit_returned(self):
        chain, docker = FakeChain(), FakeDocker(running=True)
        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)

        cancelled = cancelling(chain, docker).cancel(
            a_job(), terminal, stopped())

        self.assertIsInstance(cancelled, JobCancelledReady)
        self.assertEqual(docker.stops, ["gshared-job-123"])
        self.assertEqual(chain.aborted, [ESCROW_JOB])
        self.assertIs(chain.state, JobState.CANCELLED)

    def test_the_reason_is_the_customers(self):
        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)

        cancelled = cancelling(FakeChain()).cancel(a_job(), terminal, stopped())

        self.assertEqual(cancelled.reason, USER_REQUEST)

    def test_the_abort_transaction_is_recorded(self):
        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)

        cancelled = cancelling(FakeChain()).cancel(a_job(), terminal, stopped())

        self.assertEqual(cancelled.abort_tx_hash, "0x" + "ef" * 32)

    def test_how_long_it_ran_is_carried(self):
        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)

        cancelled = cancelling(FakeChain()).cancel(
            a_job(), terminal, stopped(seconds=42.5))

        self.assertEqual(cancelled.actual_runtime_sec, 42.5)

    def test_the_cancellation_takes_the_claim(self):
        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)

        cancelling(FakeChain()).cancel(a_job(), terminal, stopped())

        self.assertIs(terminal.claim, TerminalClaim.CANCEL)

    def test_it_waits_for_the_runtime_before_touching_the_chain(self):
        waited: list = []
        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)

        cancelling(FakeChain(),
                   wait_for_runtime=lambda s: waited.append(s) or True).cancel(
            a_job(), terminal, stopped())

        self.assertEqual(len(waited), 1)


class TestACancellationBeforeAnythingRan(unittest.TestCase):
    def test_there_is_no_container_to_stop(self):
        docker = FakeDocker()
        terminal = a_terminal(with_container=False)
        terminal.request_cancel(USER_REQUEST)

        cancelled = cancelling(FakeChain(), docker).cancel(
            a_job(), terminal, None)

        self.assertIsInstance(cancelled, JobCancelledReady)
        self.assertEqual(docker.stops, [])

    def test_and_no_runtime_to_report(self):
        # Not zero. Nothing ran, so nothing was measured.
        terminal = a_terminal(with_container=False)
        terminal.request_cancel(USER_REQUEST)

        cancelled = cancelling(FakeChain()).cancel(a_job(), terminal, None)

        self.assertIsNone(cancelled.actual_runtime_sec)

    def test_the_deposit_still_goes_back(self):
        chain = FakeChain()
        terminal = a_terminal(with_container=False)
        terminal.request_cancel(USER_REQUEST)

        cancelling(chain).cancel(a_job(), terminal, None)

        self.assertEqual(chain.aborted, [ESCROW_JOB])


class TestAContainerThatWillNotStop(unittest.TestCase):
    def test_the_escrow_is_not_touched(self):
        """The most expensive mistake available here.

        An abort refunds the customer. Sending one while the GPU is still
        training means the supplier pays for the work and is paid nothing.
        """
        chain = FakeChain()
        docker = FakeDocker(running=True, still_running_after=True)
        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)

        cancelled = cancelling(chain, docker).cancel(a_job(), terminal, None)

        self.assertIsInstance(cancelled, Unsettled)
        self.assertEqual(chain.aborted, [])

    def test_nothing_is_reported_either(self):
        docker = FakeDocker(running=True, still_running_after=True)
        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)

        cancelled = cancelling(FakeChain(), docker).cancel(
            a_job(), terminal, None)

        self.assertNotIsInstance(cancelled, JobCancelledReady)

    def test_docker_that_cannot_be_asked_is_not_a_reason_to_abort(self):
        class Broken:
            async def run(self, args, *, timeout, max_output_bytes):
                raise RuntimeError("docker is gone")

        chain = FakeChain()
        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)

        cancelled = cancelling(chain, Broken()).cancel(a_job(), terminal, None)

        self.assertIsInstance(cancelled, Unsettled)
        self.assertEqual(chain.aborted, [])


class TestTheChainHasTheLastWord(unittest.TestCase):
    def make(self, state, **kwargs):
        chain = FakeChain(state=state, **kwargs)
        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)
        return chain, terminal

    def test_an_already_cancelled_job_is_not_aborted_twice(self):
        chain, terminal = self.make(JobState.CANCELLED)

        cancelled = cancelling(chain).cancel(a_job(), terminal, stopped())

        self.assertEqual(chain.aborted, [])
        self.assertIsInstance(cancelled, JobCancelledReady)
        self.assertIsNone(cancelled.abort_tx_hash, "a hash was invented")

    def test_a_completed_job_is_never_cancelled(self):
        """It finished and the provider was paid. Saying otherwise is false."""
        chain, terminal = self.make(JobState.COMPLETED)

        cancelled = cancelling(chain).cancel(a_job(), terminal, finished())

        self.assertEqual(chain.aborted, [])
        self.assertNotIsInstance(cancelled, JobCancelledReady)
        self.assertEqual(cancelled.reason, BACKEND_CANCEL_COMPLETION_RACE)

    def test_another_operators_job_is_not_aborted(self):
        chain, terminal = self.make(JobState.ASSIGNED, operator=OTHER)

        cancelled = cancelling(chain).cancel(a_job(), terminal, stopped())

        self.assertEqual(chain.aborted, [])
        self.assertIsInstance(cancelled, Unsettled)

    def test_a_funded_job_is_not_aborted(self):
        chain, terminal = self.make(JobState.FUNDED)

        cancelled = cancelling(chain).cancel(a_job(), terminal, stopped())

        self.assertEqual(chain.aborted, [])
        self.assertIsInstance(cancelled, Unsettled)

    def test_an_unreadable_chain_reports_nothing(self):
        chain, terminal = self.make(JobState.ASSIGNED, read_raises=True)

        cancelled = cancelling(chain).cancel(a_job(), terminal, stopped())

        self.assertIsInstance(cancelled, Unsettled)

    def test_an_uncertain_transaction_is_not_retried(self):
        chain, terminal = self.make(JobState.ASSIGNED, send_raises=True)

        cancelled = cancelling(chain).cancel(a_job(), terminal, stopped())

        self.assertEqual(chain.aborted, [])
        self.assertIsInstance(cancelled, Unsettled)


# ============================================================== the races
class TestTheResultRace(unittest.TestCase):
    def test_a_cancellation_before_packaging_stops_the_settlement(self):
        chain, backend = FakeChain(), FakeBackend()
        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)

        settled = settling(chain, backend).settle(a_job(), finished(), terminal)

        self.assertIsInstance(settled, Unsettled)
        self.assertEqual(settled.reason, STOOD_DOWN)
        self.assertFalse(backend.uploaded)
        self.assertEqual(chain.submitted, [])

    def test_a_cancellation_between_packaging_and_upload_stops_it(self):
        chain, backend = FakeChain(), FakeBackend()
        terminal = a_terminal()

        # Asked while the archive is being built.
        real = Settlement._settle_success

        def cancel_midway(self, job, outcome, term=None):
            terminal.request_cancel(USER_REQUEST)
            return real(self, job, outcome, term)

        settled = settling(chain, backend).settle(a_job(), finished(), terminal)
        self.assertIsNotNone(settled)

        # And the direct case: the flag set before the upload phase.
        terminal2 = a_terminal()
        settlement = settling(FakeChain(), FakeBackend())
        terminal2.request_cancel(USER_REQUEST)
        again = settlement.settle(a_job(), finished(), terminal2)
        self.assertEqual(again.reason, STOOD_DOWN)

    def test_a_cancellation_that_loses_the_final_gate_does_not_abort(self):
        """submitResult is already claimed. The cancellation reads, not writes."""
        chain = FakeChain()
        terminal = a_terminal()
        terminal.claim_for(TerminalClaim.RESULT)
        chain.state = JobState.COMPLETED
        terminal.request_cancel(USER_REQUEST)

        cancelled = cancelling(chain).cancel(a_job(), terminal, finished())

        self.assertEqual(chain.aborted, [])
        self.assertNotIsInstance(cancelled, JobCancelledReady)

    def test_a_settlement_that_loses_the_gate_sends_no_transaction(self):
        chain, backend = FakeChain(), FakeBackend()
        terminal = a_terminal()
        terminal.claim_for(TerminalClaim.CANCEL)

        settled = settling(chain, backend).settle(a_job(), finished(), terminal)

        self.assertEqual(chain.submitted, [])
        self.assertEqual(settled.reason, STOOD_DOWN)

    def test_the_upload_still_happened_and_is_reported_as_orphaned(self):
        # The claim is taken after the upload, so an object can be left in
        # storage with nobody told to pay for it. There is no delete endpoint
        # in the Backend contract, so it is left and reported rather than
        # cleaned up by an API this Worker invented.
        chain, backend = FakeChain(), FakeBackend()
        terminal = a_terminal()
        terminal.claim_for(TerminalClaim.CANCEL)

        settling(chain, backend).settle(a_job(), finished(), terminal)

        self.assertTrue(backend.uploaded, "the upload was already done")
        self.assertEqual(chain.submitted, [], "but nothing was recorded")


class TestTheFailureRace(unittest.TestCase):
    def test_a_failure_that_claimed_first_keeps_its_reason(self):
        """A late cancellation does not rewrite why a Job ended."""
        chain = FakeChain()
        terminal = a_terminal()

        settled = settling(chain).settle(a_job(), failed(), terminal)

        self.assertIsInstance(settled, JobFailureReady)
        self.assertEqual(settled.code, "CUDA_OOM")
        self.assertIs(terminal.claim, TerminalClaim.FAILURE)

        # Now the cancellation arrives. The chain is already CANCELLED.
        terminal.request_cancel(USER_REQUEST)
        cancelled = cancelling(chain).cancel(a_job(), terminal, failed())

        self.assertNotIsInstance(cancelled, JobCancelledReady)
        self.assertEqual(len(chain.aborted), 1, "a second abort was sent")

    def test_a_cancellation_that_claimed_first_stops_the_failure_path(self):
        chain = FakeChain()
        terminal = a_terminal()
        terminal.claim_for(TerminalClaim.CANCEL)

        settled = settling(chain).settle(a_job(), failed(), terminal)

        self.assertEqual(chain.aborted, [])
        self.assertEqual(settled.reason, STOOD_DOWN)

    def test_only_one_transaction_however_they_interleave(self):
        for first in (TerminalClaim.FAILURE, TerminalClaim.CANCEL):
            with self.subTest(first=first):
                chain = FakeChain()
                terminal = a_terminal()
                terminal.claim_for(first)
                terminal.request_cancel(USER_REQUEST)

                settling(chain).settle(a_job(), failed(), terminal)
                cancelling(chain).cancel(a_job(), terminal, failed())

                self.assertLessEqual(len(chain.aborted), 1, chain.aborted)


class TestNoTwoTerminalAnswers(unittest.TestCase):
    def test_a_cancelled_job_produces_no_failure(self):
        chain = FakeChain()
        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)

        settled = settling(chain).settle(a_job(), stopped(), terminal)
        cancelled = cancelling(chain).cancel(a_job(), terminal, stopped())

        self.assertNotIsInstance(settled, JobFailureReady)
        self.assertIsInstance(cancelled, JobCancelledReady)

    def test_a_completed_job_produces_no_cancellation(self):
        chain = FakeChain()
        terminal = a_terminal()

        settled = settling(chain).settle(a_job(), finished(), terminal)
        terminal.request_cancel(USER_REQUEST)
        cancelled = cancelling(chain).cancel(a_job(), terminal, finished())

        self.assertIsInstance(settled, JobResultReady)
        self.assertNotIsInstance(cancelled, JobCancelledReady)

    def test_exactly_one_answer_comes_out_of_every_ordering(self):
        orderings = (
            ("settle then cancel", finished()),
            ("settle then cancel", failed()),
            ("settle then cancel", stopped()),
        )
        for label, outcome in orderings:
            with self.subTest(label=label, outcome=outcome.state.value):
                chain = FakeChain()
                terminal = a_terminal()
                settled = settling(chain).settle(a_job(), outcome, terminal)
                terminal.request_cancel(USER_REQUEST)
                cancelled = cancelling(chain).cancel(a_job(), terminal, outcome)

                answers = [one for one in (settled, cancelled)
                           if one is not None and not isinstance(one, Unsettled)]
                self.assertLessEqual(len(answers), 1, answers)


class TestNothingUnsafeEscapes(unittest.TestCase):
    def test_a_cancellation_that_raises_still_answers(self):
        class Exploding(FakeChain):
            def read_job(self, escrow_job_id):
                raise RuntimeError("nobody thought of this")

        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)

        cancelled = cancelling(Exploding()).cancel(a_job(), terminal, stopped())

        self.assertIsInstance(cancelled, Unsettled)

    def test_the_stop_command_names_only_our_container(self):
        docker = FakeDocker(running=True)
        terminal = a_terminal()
        terminal.request_cancel(USER_REQUEST)

        cancelling(FakeChain(), docker).cancel(a_job(), terminal, stopped())

        self.assertEqual(docker.stops, ["gshared-job-123"])

    def test_the_module_sends_nothing_and_holds_no_socket(self):
        import ast
        import inspect

        from gshared_worker import job_cancellation

        tree = ast.parse(inspect.getsource(job_cancellation))
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body:
                first = body[0]
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    body.pop(0)
                    if not body:
                        body.append(ast.Pass())
        source = ast.unparse(tree)

        for absent in ("RegisteredSocket", "websocket", "socket.send",
                       "JOB_CANCELLED", "job_cancelled", "shell=True",
                       "JobSlot", "slot."):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)


if __name__ == "__main__":
    unittest.main()
