"""Being given a Job, and saying yes to it.

The payload here is the one ``JobMatcher.buildAssignMessage`` actually builds,
with the types the ``Job`` entity declares -- the jobId a string because the
Java sends ``String.valueOf(job.getId())``, the three catalogue ids integers
because the columns are ``Long`` and V8 made the catalogue keys integers.

Nothing in this file starts anything. Accepting is agreeing to do the work.
"""

from __future__ import annotations

import json
import unittest

from gshared_worker.job_rejection import RejectCode
from gshared_worker.worker_job import (
    JOB_ACCEPTED,
    SlotState,
    AssignedJob,
    JobAssignmentError,
    JobSlot,
    OfferOutcome,
    job_accepted_message,
    job_rejected_message,
    read_assignment,
)

TOKEN = "gsh_wk_a-secret-nobody-should-see"

#: A public signing address. Not a key.
OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


def payload(**changes):
    """What the Backend puts in a JOB_ASSIGN, with anything a test wants moved."""
    values = {
        "dockerImageId": 1,
        "modelId": 3,
        "datasetId": 42,
        "trainingMode": "AUTO",
        "trainingConfig": {
            "epochs": 3, "batchSize": 8, "learningRate": 0.0002, "maxLength": 1024,
        },
        # The on-chain half. Required since the escrow contract was finalised:
        # a Worker cannot accept a job whose terms it has no way to check.
        "escrowJobId": "17",
        "externalRef": "0x" + "ab" * 32,
        "price": "9000000000000000000",
        "clientWallet": "0x1111111111111111111111111111111111111111",
        "providerWallet": "0x2222222222222222222222222222222222222222",
    }
    values.update(changes)
    return values


def assignment(job_id="123", **changes):
    """A whole JOB_ASSIGN envelope, as WorkerMessage serialises one."""
    return {
        "type": "JOB_ASSIGN",
        "jobId": job_id,
        "timestamp": "2026-09-10T14:00:00+09:00",
        "payload": payload(**changes),
    }


def with_config(**changes):
    config = payload()["trainingConfig"]
    config.update(changes)
    return assignment(trainingConfig=config)


class TestReadingAnAssignment(unittest.TestCase):
    def test_the_job_id_comes_from_the_envelope(self):
        # Not from the payload. handleJobAccepted reads the envelope's.
        self.assertEqual(read_assignment(assignment("456")).job_id, "456")

    def test_the_job_id_stays_a_string(self):
        # JobMatcher sends String.valueOf(id) and Long.parseLong reads it back.
        # A round trip through int would be a conversion each way for nothing.
        self.assertIsInstance(read_assignment(assignment()).job_id, str)

    def test_the_catalogue_ids_are_whole_numbers(self):
        job = read_assignment(assignment())

        self.assertEqual(job.docker_image_id, 1)
        self.assertEqual(job.model_id, 3)
        self.assertEqual(job.dataset_id, 42)

    def test_the_training_mode_is_carried(self):
        self.assertEqual(read_assignment(assignment()).training_mode, "AUTO")

    def test_the_training_settings_are_carried(self):
        training = read_assignment(assignment()).training

        self.assertEqual(training.epochs, 3)
        self.assertEqual(training.batch_size, 8)
        self.assertEqual(training.learning_rate, 0.0002)
        self.assertEqual(training.max_length, 1024)

    def test_what_comes_back_cannot_be_changed(self):
        job = read_assignment(assignment())

        with self.assertRaises(Exception):
            job.job_id = "999"

    def test_a_field_the_backend_adds_later_is_ignored_rather_than_refused(self):
        # The Java builds this payload and may grow it -- the TODO in
        # buildAssignMessage says datasetUrl is coming. A Worker that refused
        # the whole Job over a field it did not know would stop working the
        # day that lands, for a field it does not need.
        job = read_assignment(assignment(datasetUrl="https://example/data.jsonl"))

        self.assertEqual(job.job_id, "123")


class TestAnAssignmentThatCannotBeUsed(unittest.TestCase):
    """Refused before anything is believed, not with a KeyError three stages on."""

    def test_no_job_id(self):
        message = assignment()
        del message["jobId"]

        with self.assertRaises(JobAssignmentError):
            read_assignment(message)

    def test_a_job_id_that_is_not_a_string(self):
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(job_id=123))

    def test_an_empty_job_id(self):
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(job_id="  "))

    def test_no_payload(self):
        message = assignment()
        del message["payload"]

        with self.assertRaises(JobAssignmentError):
            read_assignment(message)

    def test_a_payload_that_is_not_an_object(self):
        message = assignment()
        message["payload"] = "everything"

        with self.assertRaises(JobAssignmentError):
            read_assignment(message)

    def test_no_training_config(self):
        message = assignment()
        del message["payload"]["trainingConfig"]

        with self.assertRaises(JobAssignmentError):
            read_assignment(message)

    def test_a_missing_catalogue_id(self):
        for name in ("dockerImageId", "modelId", "datasetId"):
            message = assignment()
            del message["payload"][name]

            with self.assertRaises(JobAssignmentError):
                read_assignment(message)

    def test_a_null_catalogue_id(self):
        # ObjectNode.put(String, Long) writes null for a null Long.
        for name in ("dockerImageId", "modelId", "datasetId"):
            with self.assertRaises(JobAssignmentError):
                read_assignment(assignment(**{name: None}))

    def test_a_catalogue_id_that_is_a_string(self):
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(modelId="3"))

    def test_a_catalogue_id_of_zero_or_less(self):
        for bad in (0, -1):
            with self.assertRaises(JobAssignmentError):
                read_assignment(assignment(modelId=bad))

    def test_a_boolean_is_not_a_number_here(self):
        # True == 1 in Python. It is not a database key.
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(modelId=True))

    def test_a_missing_training_value(self):
        for name in ("epochs", "batchSize", "learningRate", "maxLength"):
            message = assignment()
            del message["payload"]["trainingConfig"][name]

            with self.assertRaises(JobAssignmentError):
                read_assignment(message)

    def test_a_training_value_of_zero_or_less(self):
        for name in ("epochs", "batchSize", "maxLength"):
            with self.assertRaises(JobAssignmentError):
                read_assignment(with_config(**{name: 0}))

    def test_a_learning_rate_that_is_not_finite(self):
        for bad in (float("inf"), float("nan"), 0.0, -0.1):
            with self.assertRaises(JobAssignmentError):
                read_assignment(with_config(learningRate=bad))

    def test_a_missing_training_mode(self):
        message = assignment()
        del message["payload"]["trainingMode"]

        with self.assertRaises(JobAssignmentError):
            read_assignment(message)

    def test_the_refusal_names_the_job_it_was_about(self):
        with self.assertRaises(JobAssignmentError) as refused:
            read_assignment(assignment("789", modelId=None))

        self.assertIn("789", str(refused.exception))

    def test_the_refusal_names_the_field(self):
        with self.assertRaises(JobAssignmentError) as refused:
            read_assignment(assignment(datasetId=None))

        self.assertIn("datasetId", str(refused.exception))


class TestTheOneSlot(unittest.TestCase):
    """One GPU, one Job."""

    def job(self, job_id="123"):
        return read_assignment(assignment(job_id))

    def test_a_new_slot_is_idle(self):
        slot = JobSlot()

        self.assertTrue(slot.is_idle)
        self.assertIsNone(slot.held)

    def test_an_idle_slot_takes_the_job(self):
        slot = JobSlot()

        self.assertIs(slot.offer(self.job()), OfferOutcome.ACCEPTED)
        self.assertFalse(slot.is_idle)
        self.assertEqual(slot.held.job_id, "123")

    def test_taking_it_is_a_reservation_not_an_acceptance(self):
        # Between the offer and the chain saying yes the slot is neither free
        # nor settled.
        slot = JobSlot()
        slot.offer(self.job())

        self.assertIs(slot.state, SlotState.ACCEPTING)
        self.assertTrue(slot.is_accepting)

    def test_the_same_job_again_while_still_deciding(self):
        slot = JobSlot()
        slot.offer(self.job("123"))

        self.assertIs(slot.offer(self.job("123")), OfferOutcome.STILL_ACCEPTING)
        self.assertEqual(slot.held.job_id, "123")

    def test_confirming_settles_it(self):
        slot = JobSlot()
        slot.offer(self.job())
        slot.confirm()

        self.assertIs(slot.state, SlotState.ASSIGNED)
        self.assertFalse(slot.is_accepting)

    def test_the_same_job_again_after_it_settled(self):
        slot = JobSlot()
        slot.offer(self.job("123"))
        slot.confirm()

        self.assertIs(slot.offer(self.job("123")), OfferOutcome.ALREADY_HELD)
        self.assertEqual(slot.held.job_id, "123")

    def test_confirming_an_empty_slot_does_nothing(self):
        slot = JobSlot()
        slot.confirm()

        self.assertTrue(slot.is_idle)

    def test_a_different_job_is_refused_while_one_is_being_decided(self):
        slot = JobSlot()
        slot.offer(self.job("123"))

        self.assertIs(slot.offer(self.job("456")), OfferOutcome.BUSY)

    def test_a_different_job_is_refused_while_one_is_held(self):
        slot = JobSlot()
        slot.offer(self.job("123"))
        slot.confirm()

        self.assertIs(slot.offer(self.job("456")), OfferOutcome.BUSY)

    def test_the_first_job_is_kept_when_a_second_is_refused(self):
        slot = JobSlot()
        slot.offer(self.job("123"))
        slot.offer(self.job("456"))

        self.assertEqual(slot.held.job_id, "123")

    def test_releasing_it_makes_it_idle_again(self):
        slot = JobSlot()
        slot.offer(self.job())
        slot.release()

        self.assertTrue(slot.is_idle)

    def test_a_released_slot_takes_the_next_job(self):
        slot = JobSlot()
        slot.offer(self.job("123"))
        slot.release()

        self.assertIs(slot.offer(self.job("456")), OfferOutcome.ACCEPTED)


class TestTheAcceptedMessage(unittest.TestCase):
    """The reply, which is what moves a Job to RUNNING on the Backend."""

    def accepted(self, job_id="123", stamp="t", **changes):
        values = {"escrow_job_id": 17, "operator_address": OPERATOR,
                  "transaction_hash": "0x" + "cd" * 32}
        values.update(changes)
        return job_accepted_message(job_id, stamp, **values)

    def test_it_is_a_job_accepted(self):
        self.assertEqual(self.accepted()["type"], JOB_ACCEPTED)

    def test_the_job_id_is_in_the_envelope(self):
        # parseJobId reads msg.jobId(), not the payload.
        self.assertEqual(self.accepted()["jobId"], "123")

    def test_the_envelope_is_the_agreed_four_fields(self):
        self.assertEqual(set(self.accepted()),
                         {"type", "jobId", "timestamp", "payload"})

    def test_the_timestamp_is_carried_through(self):
        stamp = "2026-09-10T14:00:01+09:00"

        self.assertEqual(self.accepted(stamp=stamp)["timestamp"], stamp)

    def test_the_job_id_goes_back_exactly_as_it_arrived(self):
        # Long.parseLong reads it. A reformatted number would not parse the same.
        job = read_assignment(assignment("9007199254740993"))

        self.assertEqual(self.accepted(job.job_id)["jobId"], "9007199254740993")

    def test_the_payload_names_the_on_chain_job(self):
        # An earlier build sent {}. The Backend can now check the claim
        # against the chain instead of trusting it, which matters because
        # this message is what starts the Job.
        self.assertEqual(self.accepted()["payload"]["escrowJobId"], "17")

    def test_the_escrow_job_id_is_a_string(self):
        # uint256. It does not survive a JavaScript number, and the Backend
        # stores it in a column that would overflow.
        self.assertIsInstance(self.accepted()["payload"]["escrowJobId"], str)

    def test_the_payload_names_the_key_that_signed(self):
        self.assertEqual(self.accepted()["payload"]["operatorAddress"], OPERATOR)

    def test_the_payload_carries_the_transaction(self):
        self.assertTrue(self.accepted()["payload"]["txHash"].startswith("0x"))

    def test_a_job_that_needed_no_transaction_omits_the_hash(self):
        # Already ours on chain. There is no transaction to point at, and a
        # null would decode to something that looks like an answer.
        payload = self.accepted(transaction_hash=None)["payload"]

        self.assertNotIn("txHash", payload)
        self.assertEqual(payload["escrowJobId"], "17")

    def test_it_carries_no_credential(self):
        raw = json.dumps(self.accepted())

        for forbidden in (TOKEN, "workerToken", "accessToken", "refreshToken",
                          "password", "Authorization", "workerId",
                          "privateKey", "private_key"):
            self.assertNotIn(forbidden, raw)

    def test_the_operator_address_is_not_a_credential(self):
        # It is public and it is the thing being asserted. Worth saying once:
        # this is an address, never the key behind it.
        raw = json.dumps(self.accepted())

        self.assertIn(OPERATOR, raw)
        self.assertNotIn("0x" + "11" * 32, raw)   # a key-shaped value


class TestTheRejectedMessage(unittest.TestCase):
    """Saying no. Silence costs the client their deposit for the full timeout."""

    def test_it_is_a_job_rejected(self):
        message = job_rejected_message("123", "t", code="PRICE_MISMATCH",
                                       message="funded for a different amount")

        self.assertEqual(message["type"], "JOB_REJECTED")

    def test_the_job_id_is_in_the_envelope(self):
        message = job_rejected_message("123", "t", code="PRICE_MISMATCH",
                                       message="x")

        self.assertEqual(message["jobId"], "123")

    def test_it_carries_a_code_a_program_can_read(self):
        message = job_rejected_message("123", "t", code="OPERATOR_NOT_APPROVED",
                                       message="x")

        self.assertEqual(message["payload"]["code"], "OPERATOR_NOT_APPROVED")

    def test_it_carries_a_message_a_person_can_read(self):
        message = job_rejected_message("123", "t", code="PRICE_MISMATCH",
                                       message="funded for 1 wei, not 9")

        self.assertIn("funded for", message["payload"]["message"])

    def test_every_reject_code_can_be_sent(self):
        for code in RejectCode:
            with self.subTest(code=code):
                message = job_rejected_message(
                    "123", "t", code=code.value, message="x")
                self.assertEqual(message["payload"]["code"], code.value)


class TestWhatThisBuildDoesNotDo(unittest.TestCase):
    def source(self):
        import ast
        import inspect

        from gshared_worker import worker_job

        tree = ast.parse(inspect.getsource(worker_job))
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list) or not body:
                continue
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                body.pop(0)
                if not body:
                    body.append(ast.Pass())
        return ast.unparse(tree)

    def test_nothing_is_executed(self):
        # "docker" on its own is not a giveaway here: dockerImageId is a field
        # name in the contract. What would give it away is the machinery.
        for absent in ("subprocess", "popen", "os.system", "docker run",
                       "nvidia-smi", "wsl.exe", "command_runner"):
            self.assertNotIn(absent, self.source().lower())

    def test_nothing_is_downloaded(self):
        for absent in ("urllib", "requests", "httpx", "download"):
            self.assertNotIn(absent, self.source().lower())

    def test_no_message_type_that_has_not_landed_is_here(self):
        """Shortens as stages land, and only then.

        JOB_CANCEL and JOB_CANCELLED arrived with the escrow cleanup path,
        JOB_PROGRESS with task 8, JOB_RESULT and JOB_FAILED with task 9 --
        each checked on its own below. What is left is the protocol this
        Worker still does not speak, and the point of the test is that none
        of it creeps in ahead of the work.
        """
        for absent in ("JOB_LOG", "JOB_METRICS", "JOB_HEARTBEAT"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, self.source())

    def test_the_terminal_messages_are_envelopes_and_nothing_more(self):
        """JOB_RESULT and JOB_FAILED are built here. Nothing is decided here.

        The hash, the object key, the transaction and the duration are all
        measured somewhere else and arrive as a value. A module that could
        hash a file or read a clock would be a second place those numbers
        could come from.
        """
        source = self.source()

        self.assertIn("job_result_message", source)
        self.assertIn("job_failed_message", source)
        for absent in ("hashlib", "sha256", "tarfile", "urllib",
                       "time.monotonic", "submitResult", "abortJob",
                       "result-upload"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_progress_is_built_here_but_nothing_is_measured_here(self):
        """JOB_PROGRESS is an envelope. The numbers in it are somebody else's.

        This module turns a value into the shape the Backend reads. Deciding
        what the value is -- validating it, ordering it, timing it -- is
        :mod:`gshared_worker.runtime_progress`, and a percentage worked out
        here would be a second place that could be wrong.
        """
        source = self.source()

        self.assertIn("job_progress_message", source)
        for absent in ("time.monotonic", "datetime", "GSHARED_EVENT",
                       "parse_event", "/ total", "* 100"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_cancellation_is_read_but_nothing_is_executed_for_it(self):
        # This module turns a JOB_CANCEL into a value. Deciding what to do
        # about it -- and the abortJob that follows -- lives elsewhere.
        source = self.source()

        self.assertIn("read_cancellation", source)
        # `abort_tx_hash` is a field of the outgoing message and `escrow_job_id`
        # is a field of EscrowTerms -- both belong here. What must be absent is
        # anything that *reaches a chain*.
        for absent in ("abort_escrow_job", "web3", "ReadsEscrow", "send_abort",
                       "Web3Escrow"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_nothing_is_written_to_disk(self):
        # An assignment that outlived the process would have this Worker come
        # back believing it owes work the Backend gave to somebody else.
        for absent in ("open(", "Path", "write_text", "json.dump"):
            self.assertNotIn(absent, self.source())


if __name__ == "__main__":
    unittest.main()
