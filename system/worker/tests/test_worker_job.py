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

from gshared_worker.worker_job import (
    JOB_ACCEPTED,
    AssignedJob,
    JobAssignmentError,
    JobSlot,
    OfferOutcome,
    job_accepted_message,
    read_assignment,
)

TOKEN = "gsh_wk_a-secret-nobody-should-see"


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

    def test_the_same_job_again_is_not_a_new_job(self):
        slot = JobSlot()
        slot.offer(self.job("123"))

        self.assertIs(slot.offer(self.job("123")), OfferOutcome.ALREADY_HELD)
        self.assertEqual(slot.held.job_id, "123")

    def test_a_different_job_is_refused_while_one_is_held(self):
        slot = JobSlot()
        slot.offer(self.job("123"))

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
    def test_it_is_a_job_accepted(self):
        self.assertEqual(job_accepted_message("123", "t")["type"], JOB_ACCEPTED)

    def test_the_job_id_is_in_the_envelope(self):
        # parseJobId reads msg.jobId(), not the payload.
        self.assertEqual(job_accepted_message("123", "t")["jobId"], "123")

    def test_the_payload_is_empty(self):
        # handleJobAccepted reads nothing out of it.
        self.assertEqual(job_accepted_message("123", "t")["payload"], {})

    def test_the_envelope_is_the_agreed_four_fields(self):
        self.assertEqual(
            set(job_accepted_message("123", "t")),
            {"type", "jobId", "timestamp", "payload"})

    def test_the_timestamp_is_carried_through(self):
        stamp = "2026-09-10T14:00:01+09:00"

        self.assertEqual(job_accepted_message("123", stamp)["timestamp"], stamp)

    def test_the_job_id_goes_back_exactly_as_it_arrived(self):
        # Long.parseLong reads it. A reformatted number would not parse the same.
        job = read_assignment(assignment("9007199254740993"))

        self.assertEqual(
            job_accepted_message(job.job_id, "t")["jobId"], "9007199254740993")

    def test_it_carries_no_credential(self):
        raw = json.dumps(job_accepted_message("123", "t"))

        for forbidden in (TOKEN, "workerToken", "accessToken", "refreshToken",
                          "password", "Authorization", "workerId"):
            self.assertNotIn(forbidden, raw)


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

    def test_the_later_messages_are_not_here(self):
        for absent in ("JOB_PROGRESS", "JOB_RESULT", "JOB_FAILED", "JOB_CANCEL",
                       "progressPercent", "resultHash"):
            self.assertNotIn(absent, self.source())

    def test_nothing_is_written_to_disk(self):
        # An assignment that outlived the process would have this Worker come
        # back believing it owes work the Backend gave to somebody else.
        for absent in ("open(", "Path", "write_text", "json.dump"):
            self.assertNotIn(absent, self.source())


if __name__ == "__main__":
    unittest.main()
