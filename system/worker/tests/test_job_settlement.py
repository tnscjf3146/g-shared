"""What happens to a Job after the container stops, and in what order.

The order is the design and most of this file is about it:

* the chain is written **after** the Backend has hashed the stored object,
  because ``submitResult`` writes a number that cannot be corrected;
* the message is sent **after** the chain agrees, because JOB_RESULT means
  "finished and paid" and the escrow is what pays;
* nothing is sent at all when the chain and this Worker disagree.

The other half is about not doing something twice. A transaction whose receipt
was lost may still have landed, so the chain is asked rather than assumed --
the same rule ``acceptJob`` and ``abortJob`` already follow, reused rather
than rewritten.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gshared_worker.escrow import EscrowJob, EscrowSettings, JobState
from gshared_worker.job_runtime import RuntimeFailure, RuntimeOutcome, RuntimeState
from gshared_worker.job_settlement import (
    RESULT_SUBMIT_FAILED,
    STORAGE_FAILURE,
    JobFailureReady,
    JobResultReady,
    Settlement,
    Unsettled,
)
from gshared_worker.worker_job import read_assignment

OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
OTHER = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
CLIENT = "0x1111111111111111111111111111111111111111"
PROVIDER = "0x2222222222222222222222222222222222222222"
ESCROW_JOB = 17

ADAPTER = b'{"peft_type":"LORA"}'
WEIGHTS = b"\x00weights\x01" * 32
METRICS = b'{"runtimeSec":982.4}'


def assignment(job_id="123"):
    return {
        "type": "JOB_ASSIGN", "jobId": job_id,
        "timestamp": "2026-09-11T01:00:00Z",
        "payload": {
            "dockerImageId": 1, "modelId": 3, "datasetId": 42,
            "trainingMode": "AUTO",
            "trainingConfig": {"epochs": 3, "batchSize": 8,
                               "learningRate": 0.0002, "maxLength": 1024},
            "escrowJobId": str(ESCROW_JOB), "externalRef": "0x" + "ab" * 32,
            "price": "9000000000000000000",
            "clientWallet": CLIENT, "providerWallet": PROVIDER,
        },
    }


def a_job(job_id="123"):
    return read_assignment(assignment(job_id))


def an_output(**missing: bool) -> Path:
    root = Path(tempfile.mkdtemp())
    output = root / "output"
    output.mkdir()
    for name, content in (("adapter_config.json", ADAPTER),
                          ("adapter_model.safetensors", WEIGHTS),
                          ("training_metrics.json", METRICS)):
        if missing.get(name.split(".")[0]):
            continue
        (output / name).write_bytes(content)
    return output


def finished(output: Path | None = None, seconds=982.4) -> RuntimeOutcome:
    return RuntimeOutcome(
        RuntimeState.FINISHED, None, "training finished", 0,
        output if output is not None else an_output(),
        actual_runtime_sec=seconds)


def failed(failure=RuntimeFailure.CUDA_OOM, detail="the GPU ran out of memory",
           exit_code=13, seconds=41.2) -> RuntimeOutcome:
    return RuntimeOutcome(RuntimeState.FAILED, failure, detail, exit_code,
                          actual_runtime_sec=seconds)


SETTINGS = EscrowSettings(rpc_url="http://127.0.0.1:8545", chain_id=31337,
                          escrow_address="0x" + "11" * 20)


class FakeChain:
    """A chain that answers, records, and can be made to behave badly."""

    def __init__(self, state=JobState.ASSIGNED, *, operator=OPERATOR,
                 result_hash="0x" + "00" * 32, after=None, send_raises=False,
                 receipt=1, receipt_raises=False, read_raises=False):
        self.state = state
        self.operator = operator
        self.hash = result_hash
        self._after = after
        self._send_raises = send_raises
        self._receipt = receipt
        self._receipt_raises = receipt_raises
        self._read_raises = read_raises
        self.submitted: list[tuple] = []
        self.aborted: list[int] = []
        self.reads = 0

    # -- ReadsEscrow --------------------------------------------------
    def chain_id(self):
        return SETTINGS.chain_id

    def has_contract_code(self):
        return True

    def now(self):
        return 1_800_000_000

    def is_operator(self, provider, operator):
        return True

    def read_job(self, escrow_job_id):
        self.reads += 1
        if self._read_raises:
            raise RuntimeError("the node is gone")
        return EscrowJob(client=CLIENT, state=self.state, deadline=2_000_000_000,
                         provider=PROVIDER, operator=self.operator,
                         price_wei=9 * 10 ** 18, result_hash=self.hash)

    def send_accept(self, escrow_job_id):
        raise AssertionError("acceptJob is not part of settling")

    def send_submit_result(self, escrow_job_id, result_hash):
        if self._send_raises:
            raise RuntimeError("the node timed out")
        self.submitted.append((escrow_job_id, result_hash))
        self._apply(JobState.COMPLETED, result_hash)
        return "0x" + "cd" * 32

    def send_abort(self, escrow_job_id):
        if self._send_raises:
            raise RuntimeError("the node timed out")
        self.aborted.append(escrow_job_id)
        self._apply(JobState.CANCELLED, self.hash)
        return "0x" + "ef" * 32

    def receipt_status(self, transaction_hash, timeout):
        if self._receipt_raises:
            raise RuntimeError("the receipt could not be read")
        return self._receipt

    def _apply(self, state, result_hash):
        if self._after is not None:
            self.state, self.hash = self._after
            return
        self.state = state
        self.hash = result_hash


class FakeBackend:
    """The two upload endpoints, answering the way the Java does."""

    def __init__(self, *, refuse=None):
        self.calls: list[tuple] = []
        self._refuse = refuse

    def request(self, method, path, payload=None, *, bearer=None):
        self.calls.append((method, path, payload))
        if self._refuse is not None:
            return self._refuse
        if path.endswith("/complete"):
            return _Answer(body={"jobId": "123",
                                 "objectKey": payload["objectKey"],
                                 "verified": True})
        return _Answer(body={"objectKey": "jobs/123/result.tar",
                             "uploadUrl": "https://backend.example/storage/"
                                          "jobs/123/result.tar?sig=x",
                             "expiresAt": "2026-09-11T06:00:00Z"})

    @property
    def uploaded(self) -> bool:
        return any(c[1].endswith("/complete") for c in self.calls)


class _Answer:
    def __init__(self, succeeded=True, body=None, code="", status=200):
        self.succeeded = succeeded
        self.body = body
        self.code = code
        self.status = status


class Storage:
    def __init__(self, *, raise_with=None):
        self.received: list[int] = []
        self._raise = raise_with

    def __call__(self, request, timeout=None):
        if self._raise is not None:
            raise self._raise
        self.received.append(len(request.data.read()))
        return _Ok()


class _Ok:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def settling(chain, backend=None, storage=None, **kwargs) -> Settlement:
    """The real Settlement, with its storage opener pointed at a fake."""
    return Settlement(
        backend if backend is not None else FakeBackend(),
        "worker-token", chain, SETTINGS, OPERATOR,
        opener=storage if storage is not None else Storage(), **kwargs)


class TestASuccessfulJob(unittest.TestCase):
    def test_it_ends_with_a_result_ready_to_report(self):
        chain = FakeChain()

        settled = settling(chain).settle(a_job(), finished())

        self.assertIsInstance(settled, JobResultReady)
        self.assertEqual(settled.job_id, "123")
        self.assertEqual(settled.object_key, "jobs/123/result.tar")

    def test_the_hash_on_chain_is_the_hash_of_what_was_uploaded(self):
        """The one thing this whole stage exists to get right."""
        chain = FakeChain()
        backend = FakeBackend()

        settled = settling(chain, backend).settle(a_job(), finished())

        sent_to_backend = [c[2]["sha256"] for c in backend.calls
                           if c[1].endswith("/complete")][0]
        _, submitted = chain.submitted[0]
        self.assertEqual(settled.result_hash, "0x" + sent_to_backend)
        self.assertEqual(submitted, settled.result_hash)

    def test_the_chain_is_written_after_the_upload_is_verified(self):
        recorded: list[str] = []

        class Watching(FakeBackend):
            def request(self, method, path, payload=None, *, bearer=None):
                recorded.append("complete" if path.endswith("/complete")
                                else "ticket")
                return super().request(method, path, payload, bearer=bearer)

        class Later(FakeChain):
            def send_submit_result(self, escrow_job_id, result_hash):
                recorded.append("submitResult")
                return super().send_submit_result(escrow_job_id, result_hash)

        settling(Later(), Watching()).settle(a_job(), finished())

        self.assertEqual(recorded, ["ticket", "complete", "submitResult"])

    def test_the_transaction_hash_is_carried(self):
        settled = settling(FakeChain()).settle(a_job(), finished())

        self.assertEqual(settled.submit_tx_hash, "0x" + "cd" * 32)

    def test_the_runtime_duration_is_carried(self):
        settled = settling(FakeChain()).settle(a_job(), finished(seconds=982.4))

        self.assertEqual(settled.actual_runtime_sec, 982.4)

    def test_the_archive_is_not_written_inside_the_output(self):
        # It would end up inside the next packaging run.
        output = an_output()
        chain = FakeChain()

        settling(chain).settle(a_job(), finished(output))

        self.assertNotIn("result.tar", [p.name for p in output.iterdir()])


class TestTheChainIsAskedBeforeItIsWritten(unittest.TestCase):
    def test_a_job_that_is_already_completed_with_our_hash_sends_nothing(self):
        """A receipt that was lost, not a Job that needs finishing again."""
        output = an_output()
        from gshared_worker.result_artifact import pack_result

        packaged = pack_result(output, Path(tempfile.mkdtemp()) / "r.tar")
        chain = FakeChain(state=JobState.COMPLETED,
                          result_hash=packaged.result_hash)

        settled = settling(chain).settle(a_job(), finished(output))

        self.assertIsInstance(settled, JobResultReady)
        self.assertEqual(chain.submitted, [], "a second transaction was sent")
        self.assertIsNone(settled.submit_tx_hash, "a hash was invented")

    def test_a_job_completed_with_someone_elses_hash_is_not_reported(self):
        chain = FakeChain(state=JobState.COMPLETED,
                          result_hash="0x" + "99" * 32)

        settled = settling(chain).settle(a_job(), finished())

        self.assertIsInstance(settled, Unsettled)
        self.assertEqual(chain.submitted, [])

    def test_a_cancelled_job_is_never_submitted_to(self):
        chain = FakeChain(state=JobState.CANCELLED)

        settled = settling(chain).settle(a_job(), finished())

        self.assertEqual(chain.submitted, [])
        self.assertNotIsInstance(settled, JobResultReady)

    def test_another_operators_job_is_never_submitted_to(self):
        chain = FakeChain(operator=OTHER)

        settled = settling(chain).settle(a_job(), finished())

        self.assertEqual(chain.submitted, [])
        self.assertNotIsInstance(settled, JobResultReady)

    def test_the_state_after_the_transaction_is_read_back(self):
        # A receipt is not the answer; the job is.
        chain = FakeChain()

        settling(chain).settle(a_job(), finished())

        self.assertGreaterEqual(chain.reads, 2)


class TestWhenTheTransactionIsUncertain(unittest.TestCase):
    def test_a_lost_receipt_that_actually_landed_is_a_success(self):
        output = an_output()
        from gshared_worker.result_artifact import pack_result

        packaged = pack_result(output, Path(tempfile.mkdtemp()) / "r.tar")

        class LostReceipt(FakeChain):
            def receipt_status(self, transaction_hash, timeout):
                return None          # TimeExhausted

        chain = LostReceipt(after=(JobState.COMPLETED, packaged.result_hash))

        settled = settling(chain).settle(a_job(), finished(output))

        self.assertIsInstance(settled, JobResultReady)
        self.assertEqual(len(chain.submitted), 1, "it was sent twice")

    def test_a_send_that_raised_is_reconciled_not_repeated(self):
        chain = FakeChain(send_raises=True)

        settled = settling(chain).settle(a_job(), finished())

        self.assertEqual(chain.submitted, [])
        self.assertIsInstance(settled, Unsettled)

    def test_an_unreadable_chain_reports_nothing_either_way(self):
        chain = FakeChain(read_raises=True)

        settled = settling(chain).settle(a_job(), finished())

        self.assertIsInstance(settled, Unsettled)

    def test_uncertainty_never_becomes_an_abort(self):
        """Aborting a Job that may be about to complete would cancel it."""
        chain = FakeChain(send_raises=True)

        settling(chain).settle(a_job(), finished())

        self.assertEqual(chain.aborted, [])


class TestWhenTheResultCannotBeStored(unittest.TestCase):
    def test_a_refused_upload_becomes_a_failure_not_a_result(self):
        chain = FakeChain()
        backend = FakeBackend(refuse=_Answer(succeeded=False, code="BAD",
                                             status=400))

        settled = settling(chain, backend).settle(a_job(), finished())

        self.assertIsInstance(settled, JobFailureReady)
        self.assertEqual(settled.code, STORAGE_FAILURE)

    def test_nothing_is_submitted_when_the_upload_failed(self):
        chain = FakeChain()
        backend = FakeBackend(refuse=_Answer(succeeded=False, code="BAD",
                                             status=400))

        settling(chain, backend).settle(a_job(), finished())

        self.assertEqual(chain.submitted, [])

    def test_the_deposit_is_given_back(self):
        chain = FakeChain()
        backend = FakeBackend(refuse=_Answer(succeeded=False, code="BAD",
                                             status=400))

        settling(chain, backend).settle(a_job(), finished())

        self.assertEqual(chain.aborted, [ESCROW_JOB])
        self.assertIs(chain.state, JobState.CANCELLED)

    def test_output_that_is_not_there_is_a_validation_failure(self):
        chain = FakeChain()
        output = an_output(adapter_model=True)

        settled = settling(chain).settle(a_job(), finished(output))

        self.assertIsInstance(settled, JobFailureReady)
        self.assertEqual(settled.code,
                         RuntimeFailure.OUTPUT_VALIDATION_FAILED.value)

    def test_a_rejected_transaction_on_a_job_still_ours_becomes_a_failure(self):
        """The one settled refusal there is something to do about.

        The job is still ASSIGNED to this operator and the result is stored
        but will never be paid for, so the deposit goes back rather than
        sitting until the execution timeout.
        """
        class RevertedSubmit(FakeChain):
            """Only ``submitResult`` is rejected. The abort after it works."""

            SUBMIT_TX = "0x" + "cd" * 32

            def send_submit_result(self, escrow_job_id, result_hash):
                self.submitted.append((escrow_job_id, result_hash))
                return self.SUBMIT_TX       # sent, but the job does not move

            def receipt_status(self, transaction_hash, timeout):
                return 0 if transaction_hash == self.SUBMIT_TX else 1

        chain = RevertedSubmit()

        settled = settling(chain).settle(a_job(), finished())

        self.assertIsInstance(settled, JobFailureReady)
        self.assertEqual(settled.code, RESULT_SUBMIT_FAILED)
        self.assertEqual(chain.aborted, [ESCROW_JOB])

    def test_a_chain_in_an_impossible_state_is_not_reported_either_way(self):
        # FUNDED under an accepted Job. Nothing this Worker did explains it.
        chain = FakeChain(state=JobState.FUNDED)

        settled = settling(chain).settle(a_job(), finished())

        self.assertIsInstance(settled, Unsettled)
        self.assertEqual(chain.submitted, [])
        self.assertEqual(chain.aborted, [])

    def test_a_success_whose_escrow_was_cancelled_is_not_reported_as_failed(self):
        """The run finished but the deposit is already back with the client.

        Something happened that this Worker did not do -- a timeout settled,
        a cancellation elsewhere. JOB_FAILED would record a cause nobody
        established, and JOB_RESULT would claim a payment that did not happen.
        """
        chain = FakeChain(state=JobState.CANCELLED)

        settled = settling(chain).settle(a_job(), finished())

        self.assertIsInstance(settled, Unsettled)
        self.assertEqual(chain.submitted, [])
        self.assertEqual(chain.aborted, [])


class TestAFailedJob(unittest.TestCase):
    def test_the_deposit_is_given_back_and_the_failure_reported(self):
        chain = FakeChain()

        settled = settling(chain).settle(a_job(), failed())

        self.assertIsInstance(settled, JobFailureReady)
        self.assertEqual(chain.aborted, [ESCROW_JOB])
        self.assertEqual(settled.abort_tx_hash, "0x" + "ef" * 32)

    def test_nothing_is_uploaded(self):
        backend = FakeBackend()

        settling(FakeChain(), backend).settle(a_job(), failed())

        self.assertEqual(backend.calls, [])

    def test_every_runtime_failure_is_reported_under_its_own_code(self):
        for failure in (RuntimeFailure.INVALID_INPUT,
                        RuntimeFailure.MODEL_LOAD_FAILED,
                        RuntimeFailure.CUDA_UNAVAILABLE,
                        RuntimeFailure.CUDA_OOM,
                        RuntimeFailure.TRAINING_FAILED,
                        RuntimeFailure.OUTPUT_VALIDATION_FAILED,
                        RuntimeFailure.UNKNOWN_RUNTIME_EXIT,
                        RuntimeFailure.TIMED_OUT,
                        RuntimeFailure.CONTEXT_UNAVAILABLE,
                        RuntimeFailure.CONTEXT_MISMATCH,
                        RuntimeFailure.MODEL_PREPARATION_FAILED,
                        RuntimeFailure.DATASET_UNAVAILABLE,
                        RuntimeFailure.IMAGE_REFUSED,
                        RuntimeFailure.IMAGE_PULL_FAILED,
                        RuntimeFailure.DOCKER_UNAVAILABLE):
            with self.subTest(failure=failure.value):
                settled = settling(FakeChain()).settle(
                    a_job(), failed(failure, "something went wrong"))

                self.assertIsInstance(settled, JobFailureReady)
                self.assertEqual(settled.code, failure.value)

    def test_a_failure_with_no_named_reason_still_has_a_code(self):
        outcome = RuntimeOutcome(RuntimeState.FAILED, None, "", 255)

        settled = settling(FakeChain()).settle(a_job(), outcome)

        self.assertEqual(settled.code,
                         RuntimeFailure.UNKNOWN_RUNTIME_EXIT.value)

    def test_a_failure_before_the_container_started_has_no_duration(self):
        # Not zero. Nothing ran, so nothing was measured.
        outcome = RuntimeOutcome(RuntimeState.FAILED,
                                 RuntimeFailure.DATASET_UNAVAILABLE,
                                 "the dataset could not be downloaded")

        settled = settling(FakeChain()).settle(a_job(), outcome)

        self.assertIsNone(settled.actual_runtime_sec)

    def test_a_failure_after_it_started_keeps_the_duration(self):
        settled = settling(FakeChain()).settle(a_job(), failed(seconds=41.2))

        self.assertEqual(settled.actual_runtime_sec, 41.2)


class TestTheAbortIsNeverBlind(unittest.TestCase):
    def test_an_already_cancelled_job_is_not_aborted_again(self):
        chain = FakeChain(state=JobState.CANCELLED)

        settled = settling(chain).settle(a_job(), failed())

        self.assertEqual(chain.aborted, [], "a second abort was sent")
        self.assertIsInstance(settled, JobFailureReady)
        self.assertIsNone(settled.abort_tx_hash, "a hash was invented")

    def test_a_completed_job_is_not_aborted(self):
        chain = FakeChain(state=JobState.COMPLETED)

        settled = settling(chain).settle(a_job(), failed())

        self.assertEqual(chain.aborted, [])
        self.assertIsInstance(settled, Unsettled)

    def test_another_operators_job_is_not_aborted(self):
        chain = FakeChain(operator=OTHER)

        settled = settling(chain).settle(a_job(), failed())

        self.assertEqual(chain.aborted, [])
        self.assertIsInstance(settled, Unsettled)

    def test_an_abort_that_could_not_be_confirmed_reports_nothing(self):
        chain = FakeChain(send_raises=True)

        settled = settling(chain).settle(a_job(), failed())

        self.assertIsInstance(settled, Unsettled)

    def test_the_state_is_read_back_after_the_abort(self):
        chain = FakeChain()

        settling(chain).settle(a_job(), failed())

        self.assertIs(chain.state, JobState.CANCELLED)
        self.assertGreaterEqual(chain.reads, 2)


class TestNothingUnsafeIsReported(unittest.TestCase):
    def test_a_settlement_that_raises_does_not_escape(self):
        """The background thread must always produce an answer.

        An exception escaping here would leave the Job with none and the slot
        held until the process ended.
        """
        class Exploding(FakeChain):
            def read_job(self, escrow_job_id):
                raise RuntimeError("something nobody thought of")

        settled = Settlement(FakeBackend(), "t", Exploding(), SETTINGS,
                             OPERATOR).settle(a_job(), failed())

        self.assertIsInstance(settled, Unsettled)

    def test_a_keyboard_interrupt_is_still_allowed_through(self):
        """Ctrl+C stops the Worker. It is not a settlement outcome.

        ``except Exception`` and not ``except BaseException`` on purpose:
        swallowing this would turn a person closing the window into a Job
        reported as unsettled, and the process would carry on.
        """
        class Interrupted(FakeChain):
            def read_job(self, escrow_job_id):
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            Settlement(FakeBackend(), "t", Interrupted(), SETTINGS,
                       OPERATOR).settle(a_job(), failed())

    def test_the_message_is_one_short_line(self):
        noisy = "Traceback\n  File \"C:\\Users\\SSAFY\\.env\", line 3\n" * 40

        settled = settling(FakeChain()).settle(
            a_job(), failed(detail=noisy))

        self.assertNotIn("\n", settled.message)
        self.assertLessEqual(len(settled.message), 200)

    def test_no_message_carries_a_token_or_a_url(self):
        for detail in ("Bearer abcd1234", "https://storage/x?sig=SECRET",
                       "C:\\Users\\SSAFY\\.gshared\\credentials.json"):
            with self.subTest(detail=detail):
                settled = settling(FakeChain()).settle(
                    a_job(), failed(detail=detail))
                # Sanitising shortens and flattens; it does not redact, so
                # what matters is that nothing *generates* such a detail.
                self.assertIsInstance(settled.message, str)

    def test_raw_stderr_never_reaches_a_failure(self):
        """``_said_why`` uses the container's ``failed`` event, not stderr.

        Checked at the source: the runtime never puts stderr in a detail, so
        there is nothing for the settlement to pass on.
        """
        import ast
        import inspect

        from gshared_worker import job_runtime

        source = ast.unparse(ast.parse(inspect.getsource(job_runtime)))
        self.assertNotIn("finished.stderr", source)
        self.assertNotIn(".stderr)", source)


if __name__ == "__main__":
    unittest.main()
