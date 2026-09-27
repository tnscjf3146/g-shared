"""What the Backend says to run, checked against what was accepted.

The Worker has already signed a transaction by the time this runs. If the
context that arrives a second later describes a different price, a different
client or a different dataset, then the Backend's own records disagree with
each other -- and neither version is safe to run. So every field is compared,
and a disagreement stops the Job rather than picking a side.

The one thing that is **not** taken from the context is which chain and which
contract. Those are configuration; the context is checked against them.
"""

from __future__ import annotations

import unittest

from gshared_worker.execution_context import (
    EXPECTED_ESCROW_STATE_FUNDED,
    ContextRefused,
    check_matches_assignment,
    check_matches_trusted_escrow,
    context_path,
    fetch_execution_context,
    read_context,
)
from gshared_worker.worker_job import read_assignment

CLIENT = "0x1111111111111111111111111111111111111111"
PROVIDER = "0x2222222222222222222222222222222222222222"
REF = "0x" + "ab" * 32
PRICE = "9000000000000000000"
IMAGE = "gshared/qlora-sft-runtime@sha256:" + "cd" * 32


def body(**changes):
    payload = {
        "jobId": "123",
        "assignedWorkerId": "worker-001",
        "model": {"modelId": 1, "modelRef": "Qwen/Qwen3-0.6B",
                  "modelRevision": "c1899de289a04d12100db370d81485cdf75e47ca"},
        "runtimeImage": {"dockerImageId": 3, "imageRef": IMAGE,
                         "digest": "sha256:" + "cd" * 32},
        "dataset": {"datasetId": 42, "downloadUrl": "https://s/x?sig=abc",
                    "sha256": "ab" * 32, "datasetSizeBytes": 52428800,
                    "sampleCount": 10000, "totalTokens": 2500000,
                    "effectiveTokens": 1800000, "avgSeqLength": 250.0,
                    "maxSeqLength": 1024},
        "trainingConfig": {"epochs": 1, "batchSize": 16,
                           "learningRate": 0.0002, "maxLength": 512},
        "escrow": {"chainId": 31221, "contractAddress": "0xESCROW",
                   "escrowJobId": "17", "externalRef": REF,
                   "client": CLIENT, "provider": PROVIDER,
                   "expectedPriceToken": 9, "expectedPriceWei": PRICE,
                   "expectedState": 1},
    }
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return payload


def without(section, field):
    payload = body()
    del payload[section][field]
    return payload


def an_assignment(**changes):
    payload = {
        "dockerImageId": 3, "modelId": 1, "datasetId": 42,
        "trainingMode": "AUTO",
        "trainingConfig": {"epochs": 1, "batchSize": 16,
                           "learningRate": 0.0002, "maxLength": 512},
        "escrowJobId": "17", "externalRef": REF, "price": PRICE,
        "clientWallet": CLIENT, "providerWallet": PROVIDER,
    }
    payload.update(changes)
    return read_assignment({"type": "JOB_ASSIGN", "jobId": "123",
                            "timestamp": "t", "payload": payload})


class Settings:
    chain_id = 31221
    escrow_address = "0xescrow"


class Answer:
    def __init__(self, status=200, payload=None, unreachable=False):
        from gshared_worker.backend_client import Outcome

        self.status = None if unreachable else status
        self.body = payload
        self.outcome = Outcome.UNREACHABLE if unreachable else (
            Outcome.OK if status == 200 else Outcome.REFUSED)
        self.code = ""
        self.message = ""

    @property
    def succeeded(self):
        return self.status == 200


class Client:
    """A Backend that answers a scripted sequence."""

    def __init__(self, *answers):
        self._answers = list(answers)
        self.calls: list = []

    def request(self, method, path, payload=None, *, bearer=None):
        self.calls.append((method, path, bearer))
        return self._answers.pop(0) if self._answers else Answer(500)


class TestReadingTheContext(unittest.TestCase):
    def test_a_whole_context_is_read(self):
        context = read_context(body())

        self.assertEqual(context.job_id, "123")
        self.assertEqual(context.model.model_ref, "Qwen/Qwen3-0.6B")
        self.assertEqual(context.dataset.dataset_id, 42)
        self.assertEqual(context.training.epochs, 1)

    def test_the_escrow_numbers_are_integers(self):
        context = read_context(body())

        self.assertEqual(context.escrow.escrow_job_id, 17)
        self.assertEqual(context.escrow.expected_price_wei,
                         9_000_000_000_000_000_000)

    def test_a_price_beyond_64_bits_survives(self):
        huge = str(2 ** 200)

        context = read_context(body(escrow={"expectedPriceWei": huge}))

        self.assertEqual(context.escrow.expected_price_wei, 2 ** 200)

    def test_a_json_number_price_is_refused(self):
        # uint256 does not survive a JSON number, and the result would look
        # entirely reasonable.
        with self.assertRaises(ContextRefused):
            read_context(body(escrow={"expectedPriceWei": 9000000000000000000}))

    def test_a_null_contract_address_is_allowed(self):
        # The Backend does not always know it. Absence is not a mismatch.
        context = read_context(body(escrow={"contractAddress": None}))

        self.assertIsNone(context.escrow.contract_address)

    def test_every_section_is_required(self):
        for section in ("model", "runtimeImage", "dataset", "trainingConfig",
                        "escrow"):
            with self.subTest(section=section):
                payload = body()
                del payload[section]
                with self.assertRaises(ContextRefused):
                    read_context(payload)

    def test_every_field_is_required(self):
        for section, field in (
                ("model", "modelRef"), ("model", "modelRevision"),
                ("runtimeImage", "imageRef"), ("runtimeImage", "digest"),
                ("dataset", "downloadUrl"), ("dataset", "sha256"),
                ("dataset", "datasetSizeBytes"),
                ("trainingConfig", "epochs"), ("trainingConfig", "maxLength"),
                ("escrow", "escrowJobId"), ("escrow", "externalRef"),
                ("escrow", "client"), ("escrow", "provider"),
                ("escrow", "expectedPriceWei")):
            with self.subTest(field=f"{section}.{field}"):
                with self.assertRaises(ContextRefused):
                    read_context(without(section, field))

    def test_a_missing_field_is_never_filled_in(self):
        """The single most important property of this module.

        A context with no dataset hash is not a context with a gap -- it is a
        Job that cannot be verified, and supplying a default would mean
        training on an unchecked file.
        """
        with self.assertRaises(ContextRefused) as caught:
            read_context(without("dataset", "sha256"))

        self.assertIn("sha256", str(caught.exception))

    def test_a_zero_epoch_count_is_refused(self):
        with self.assertRaises(ContextRefused):
            read_context(body(trainingConfig={"epochs": 0}))

    def test_a_non_finite_learning_rate_is_refused(self):
        with self.assertRaises(ContextRefused):
            read_context(body(trainingConfig={"learningRate": float("inf")}))


class TestComparingAgainstTheAssignment(unittest.TestCase):
    def check(self, context_changes=None, assignment=None):
        context = read_context(body(**(context_changes or {})))
        check_matches_assignment(
            context, assignment or an_assignment(), "worker-001")

    def test_a_matching_pair_passes(self):
        self.check()

    def test_a_different_job_id(self):
        with self.assertRaises(ContextRefused):
            self.check({"jobId": "999"})

    def test_a_context_for_another_worker(self):
        context = read_context(body(assignedWorkerId="worker-999"))

        with self.assertRaises(ContextRefused):
            check_matches_assignment(context, an_assignment(), "worker-001")

    def test_a_different_image_id(self):
        with self.assertRaises(ContextRefused):
            self.check({"runtimeImage": {"dockerImageId": 9}})

    def test_a_different_model_id(self):
        with self.assertRaises(ContextRefused):
            self.check({"model": {"modelId": 9}})

    def test_a_different_dataset_id(self):
        with self.assertRaises(ContextRefused):
            self.check({"dataset": {"datasetId": 9}})

    def test_a_different_training_config(self):
        for field, value in (("epochs", 3), ("batchSize", 8),
                             ("learningRate", 0.001), ("maxLength", 1024)):
            with self.subTest(field=field):
                with self.assertRaises(ContextRefused):
                    self.check({"trainingConfig": {field: value}})

    def test_a_different_escrow_job(self):
        with self.assertRaises(ContextRefused):
            self.check({"escrow": {"escrowJobId": "18"}})

    def test_a_different_external_ref(self):
        with self.assertRaises(ContextRefused):
            self.check({"escrow": {"externalRef": "0x" + "cd" * 32}})

    def test_a_different_client(self):
        with self.assertRaises(ContextRefused):
            self.check({"escrow": {"client": "0x" + "99" * 20}})

    def test_a_different_provider(self):
        with self.assertRaises(ContextRefused):
            self.check({"escrow": {"provider": "0x" + "99" * 20}})

    def test_a_different_price(self):
        """The one the Worker already signed against.

        The contract does not enforce price; this Worker checked it before
        accepting, and a context that now says something else means the
        Backend's two records disagree.
        """
        with self.assertRaises(ContextRefused):
            self.check({"escrow": {"expectedPriceWei": "1"}})

    def test_addresses_compare_without_case(self):
        self.check({"escrow": {"client": CLIENT.upper().replace("0X", "0x")}})

    def test_the_expected_state_is_funded(self):
        # It reports what acceptance expected to find, not what the job is
        # now. A Backend that "updated" it to ASSIGNED has changed its meaning.
        self.assertEqual(EXPECTED_ESCROW_STATE_FUNDED, 1)
        with self.assertRaises(ContextRefused):
            self.check({"escrow": {"expectedState": 2}})


class TestTheChainComesFromConfiguration(unittest.TestCase):
    def test_a_matching_chain_passes(self):
        check_matches_trusted_escrow(read_context(body()), Settings())

    def test_a_different_chain_is_refused(self):
        with self.assertRaises(ContextRefused):
            check_matches_trusted_escrow(
                read_context(body(escrow={"chainId": 1})), Settings())

    def test_a_different_contract_is_refused(self):
        # The Worker already signed against the configured address. Running
        # against another would put the money and the work on two contracts.
        with self.assertRaises(ContextRefused):
            check_matches_trusted_escrow(
                read_context(body(escrow={"contractAddress": "0xOTHER"})),
                Settings())

    def test_a_null_contract_is_accepted(self):
        check_matches_trusted_escrow(
            read_context(body(escrow={"contractAddress": None})), Settings())

    def test_the_comparison_ignores_case(self):
        check_matches_trusted_escrow(
            read_context(body(escrow={"contractAddress": "0xESCROW"})),
            Settings())


class TestFetchingIt(unittest.TestCase):
    def test_the_path_names_the_job(self):
        self.assertEqual(context_path("123"),
                         "/api/worker/jobs/123/execution-context")

    def test_a_job_id_is_escaped_into_the_path(self):
        self.assertEqual(context_path("a/b"),
                         "/api/worker/jobs/a%2Fb/execution-context")

    def test_it_is_authorised_with_the_worker_token(self):
        client = Client(Answer(200, body()))

        fetch_execution_context(client, "the-token", "123")

        self.assertEqual(client.calls[0][2], "the-token")

    def test_a_200_returns_the_body(self):
        client = Client(Answer(200, body()))

        self.assertEqual(
            fetch_execution_context(client, "t", "123")["jobId"], "123")

    def test_a_409_is_retried(self):
        """Not a failure -- this Worker was faster than the Backend.

        The Job becomes RUNNING when the WebSocket handler processes
        JOB_ACCEPTED, and an HTTP request can easily arrive first.
        """
        client = Client(Answer(409), Answer(409), Answer(200, body()))
        waits: list = []

        result = fetch_execution_context(
            client, "t", "123", sleep=lambda s: waits.append(s) or True)

        self.assertEqual(result["jobId"], "123")
        self.assertEqual(len(waits), 2)

    def test_the_wait_grows(self):
        client = Client(Answer(409), Answer(409), Answer(409), Answer(200, body()))
        waits: list = []

        fetch_execution_context(
            client, "t", "123", sleep=lambda s: waits.append(s) or True)

        self.assertEqual(waits, sorted(waits))
        self.assertGreater(waits[-1], waits[0])

    def test_retrying_is_bounded(self):
        client = Client(*[Answer(409) for _ in range(20)])

        with self.assertRaises(ContextRefused):
            fetch_execution_context(
                client, "t", "123", sleep=lambda s: True, attempts=3)

        self.assertEqual(len(client.calls), 3)

    def test_a_5xx_is_retried(self):
        client = Client(Answer(503), Answer(200, body()))

        result = fetch_execution_context(
            client, "t", "123", sleep=lambda s: True)

        self.assertEqual(result["jobId"], "123")

    def test_an_unreachable_service_is_retried(self):
        client = Client(Answer(unreachable=True), Answer(200, body()))

        result = fetch_execution_context(
            client, "t", "123", sleep=lambda s: True)

        self.assertEqual(result["jobId"], "123")

    def test_a_401_is_not_retried(self):
        client = Client(Answer(401))

        with self.assertRaises(ContextRefused):
            fetch_execution_context(client, "t", "123", sleep=lambda s: True)

        self.assertEqual(len(client.calls), 1)

    def test_a_403_is_not_retried(self):
        client = Client(Answer(403))

        with self.assertRaises(ContextRefused):
            fetch_execution_context(client, "t", "123", sleep=lambda s: True)

        self.assertEqual(len(client.calls), 1)

    def test_a_404_is_not_retried(self):
        client = Client(Answer(404))

        with self.assertRaises(ContextRefused):
            fetch_execution_context(client, "t", "123", sleep=lambda s: True)

        self.assertEqual(len(client.calls), 1)

    def test_stopping_ends_the_wait_at_once(self):
        # A person closing the window is not held for the whole budget.
        client = Client(Answer(409), Answer(409))

        with self.assertRaises(ContextRefused) as caught:
            fetch_execution_context(client, "t", "123", sleep=lambda s: False)

        self.assertFalse(caught.exception.fatal)

    def test_a_body_that_is_not_an_object_is_refused(self):
        client = Client(Answer(200, ["not", "a", "context"]))

        with self.assertRaises(ContextRefused):
            fetch_execution_context(client, "t", "123")

    def test_the_token_is_never_in_the_refusal(self):
        client = Client(Answer(401))

        with self.assertRaises(ContextRefused) as caught:
            fetch_execution_context(client, "a-real-worker-token", "123")

        self.assertNotIn("a-real-worker-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
