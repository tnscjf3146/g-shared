"""The five on-chain fields of a JOB_ASSIGN, and what happens without them.

Everything here is one rule seen from different angles: **no fallback**. Each of
these five could plausibly be guessed at, and every guess produces a Worker
that signs a transaction against terms nobody sent it.

    escrowJobId     the database job id looks like a number too
    price           could be recomputed from a rate card
    providerWallet  could be assumed to be this machine's own
    clientWallet    could be left out of the comparison
    externalRef     could be derived from the job id

The first is the dangerous one. The Backend's job id and the escrow's job id
both count up from one, so during development they are equal -- and a Worker
that substituted one for the other would work perfectly until the day the two
sequences drifted, and then accept somebody else's funded job.
"""

from __future__ import annotations

import unittest

from gshared_worker.worker_job import JobAssignmentError, read_assignment

ADDRESS = "0x1111111111111111111111111111111111111111"
PROVIDER = "0x2222222222222222222222222222222222222222"
REF = "0x" + "ab" * 32


def payload(**changes):
    values = {
        "dockerImageId": 3,
        "modelId": 1,
        "datasetId": 42,
        "trainingMode": "AUTO",
        "trainingConfig": {
            "epochs": 1, "batchSize": 16,
            "learningRate": 0.0002, "maxLength": 512},
        "escrowJobId": "17",
        "externalRef": REF,
        "price": "9000000000000000000",
        "clientWallet": ADDRESS,
        "providerWallet": PROVIDER,
    }
    values.update(changes)
    return values


def assignment(**changes):
    return {
        "type": "JOB_ASSIGN",
        "jobId": "123",
        "timestamp": "2026-09-11T01:00:00Z",
        "payload": payload(**changes),
    }


def without(field):
    body = payload()
    del body[field]
    return {"type": "JOB_ASSIGN", "jobId": "123",
            "timestamp": "2026-09-11T01:00:00Z", "payload": body}


class TestTheEscrowTermsAreRead(unittest.TestCase):
    def test_the_escrow_job_id_becomes_an_integer(self):
        self.assertEqual(read_assignment(assignment()).escrow.escrow_job_id, 17)

    def test_it_is_not_the_backend_job_id(self):
        """The mistake that survives testing.

        Both sequences start at one, so they agree for a while. A Worker that
        used the wrong one would be correct until they drifted, and would then
        accept a funded job belonging to somebody else.
        """
        job = read_assignment(assignment(escrowJobId="17"))

        self.assertEqual(job.job_id, "123")
        self.assertEqual(job.escrow.escrow_job_id, 17)

    def test_the_price_becomes_an_integer(self):
        self.assertEqual(read_assignment(assignment()).escrow.price_wei,
                         9_000_000_000_000_000_000)

    def test_a_price_larger_than_a_64_bit_integer_survives(self):
        # uint256. Python's int is unbounded; a float or an int64 is not.
        huge = str(2 ** 200)

        self.assertEqual(read_assignment(assignment(price=huge)).escrow.price_wei,
                         2 ** 200)

    def test_the_addresses_come_through(self):
        escrow = read_assignment(assignment()).escrow

        self.assertEqual(escrow.client_wallet, ADDRESS)
        self.assertEqual(escrow.provider_wallet, PROVIDER)

    def test_the_external_ref_comes_through(self):
        self.assertEqual(read_assignment(assignment()).escrow.external_ref, REF)

    def test_the_runtime_fields_are_still_there(self):
        # The escrow half was added beside the existing contract, not instead
        # of it.
        job = read_assignment(assignment())

        self.assertEqual(job.docker_image_id, 3)
        self.assertEqual(job.model_id, 1)
        self.assertEqual(job.dataset_id, 42)
        self.assertEqual(job.training.epochs, 1)


class TestEachFieldIsRequired(unittest.TestCase):
    """Missing means refused. There is no substitute for any of them."""

    def refusal(self, field) -> JobAssignmentError:
        with self.assertRaises(JobAssignmentError) as caught:
            read_assignment(without(field))
        return caught.exception

    def test_escrow_job_id(self):
        self.assertIn("escrowJobId", str(self.refusal("escrowJobId")))

    def test_external_ref(self):
        self.assertIn("externalRef", str(self.refusal("externalRef")))

    def test_price(self):
        self.assertIn("price", str(self.refusal("price")))

    def test_client_wallet(self):
        self.assertIn("clientWallet", str(self.refusal("clientWallet")))

    def test_provider_wallet(self):
        self.assertIn("providerWallet", str(self.refusal("providerWallet")))

    def test_a_backend_that_sends_none_of_them_is_refused(self):
        body = {k: v for k, v in payload().items()
                if k not in ("escrowJobId", "externalRef", "price",
                             "clientWallet", "providerWallet")}

        with self.assertRaises(JobAssignmentError):
            read_assignment({"type": "JOB_ASSIGN", "jobId": "123",
                             "timestamp": "t", "payload": body})


class TestNumbersTravelAsStrings(unittest.TestCase):
    """uint256 does not fit in a JSON number, so it is not sent as one."""

    def test_a_json_number_price_is_refused(self):
        # Refused rather than accepted and converted: a value that went
        # through a JavaScript number has already lost digits, and it would
        # look entirely reasonable afterwards.
        with self.assertRaises(JobAssignmentError) as caught:
            read_assignment(assignment(price=9000000000000000000))

        self.assertIn("decimal string", str(caught.exception))

    def test_a_json_number_escrow_job_id_is_refused(self):
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(escrowJobId=17))

    def test_a_negative_price_is_refused(self):
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(price="-1"))

    def test_a_price_that_is_not_a_number_is_refused(self):
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(price="nine"))

    def test_a_hex_price_is_refused(self):
        # Decimal was agreed. A hex string parsed as decimal is a wrong number,
        # not an error.
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(price="0x7ce66c50e2840000"))

    def test_a_decimal_point_is_refused(self):
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(price="9.0"))

    def test_zero_is_allowed_as_a_value_even_if_odd(self):
        # The contract decides whether a zero-priced job is acceptable. This
        # layer only decides whether the message is well formed.
        self.assertEqual(read_assignment(assignment(price="0")).escrow.price_wei, 0)


class TestTheHexFieldsAreShaped(unittest.TestCase):
    def test_an_address_that_is_too_short(self):
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(clientWallet="0x1111"))

    def test_an_address_with_no_prefix(self):
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(clientWallet="1" * 40))

    def test_an_address_with_non_hex_characters(self):
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(providerWallet="0x" + "z" * 40))

    def test_an_external_ref_of_the_wrong_width(self):
        # bytes32 is exactly 64 hex digits. A shorter one is a different value
        # once it is zero-padded, and it would compare unequal for ever.
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(externalRef="0x" + "ab" * 16))

    def test_an_external_ref_that_is_an_address(self):
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(externalRef=ADDRESS))

    def test_uppercase_hex_is_accepted(self):
        # Checksummed addresses are mixed case. Rejecting them would refuse
        # correctly formed input.
        upper = "0x" + "AB" * 32

        self.assertEqual(
            read_assignment(assignment(externalRef=upper)).escrow.external_ref,
            upper)

    def test_a_non_string_address_is_refused(self):
        with self.assertRaises(JobAssignmentError):
            read_assignment(assignment(clientWallet=1234))


class TestNoFallbackExists(unittest.TestCase):
    """Searched for, because this is the rule most easily broken by kindness.

    The searches below are scoped to the **parsing** code rather than the whole
    module. Two earlier attempts were not, and both failed on innocent matches:
    "rate" inside ``learning_rate``, and ``operator_address`` in the signature
    of :func:`job_accepted_message`, which legitimately receives one. A search
    that matches what it was not looking for teaches nothing.
    """

    def parsing_source(self) -> str:
        """Just the functions that turn a payload into EscrowTerms."""
        import ast
        import inspect

        from gshared_worker import worker_job

        tree = ast.parse(inspect.getsource(worker_job))
        wanted = {"_escrow_terms", "_decimal_string", "_hex_string",
                  "read_assignment"}
        chunks = [ast.unparse(node) for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name in wanted]
        assert len(chunks) == len(wanted), chunks
        return "\n".join(chunks)

    def test_the_escrow_job_id_is_never_defaulted_from_the_job_id(self):
        source = self.parsing_source()

        self.assertNotIn("int(job_id)", source)
        self.assertNotIn("escrow_job_id=job_id", source)

    def test_no_price_is_ever_computed(self):
        source = self.parsing_source()

        for absent in ("hourly", "estimate", "10 ** 18", "WEI_PER"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_the_parsing_code_does_no_arithmetic_on_a_value(self):
        """Checked on the tree, not the text.

        A string search for "*" matched a bold marker in a docstring. The
        question is whether these functions multiply or divide anything, and
        the syntax tree answers that exactly.
        """
        import ast

        tree = ast.parse(self.parsing_source())
        operators = [type(node.op).__name__ for node in ast.walk(tree)
                     if isinstance(node, ast.BinOp)]

        # The one subtraction is `length - 2`, formatting a message about how
        # many hex digits were expected. Nothing multiplies or divides.
        self.assertEqual(set(operators) - {"Sub"}, set(), operators)

    def test_no_address_is_ever_defaulted(self):
        source = self.parsing_source()

        for absent in ("wallet.address", "local_address", "operator_address",
                       "self.address"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_no_external_ref_is_ever_derived(self):
        source = self.parsing_source()

        for absent in ("keccak", "sha256", "hashlib", "md5"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_the_execution_context_is_not_consulted_here(self):
        # A missing JOB_ASSIGN field is never quietly filled in from the
        # execution-context endpoint. That would turn a contract violation into
        # a silent success.
        source = self.parsing_source()

        for absent in ("execution", "requests", "urllib", "http"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_every_escrow_field_is_read_straight_from_the_payload(self):
        # Five reads, five fields, no branches offering an alternative.
        source = self.parsing_source()

        for field in ("escrowJobId", "externalRef", "price", "clientWallet",
                      "providerWallet"):
            with self.subTest(field=field):
                self.assertEqual(source.count(repr(field)), 1)


if __name__ == "__main__":
    unittest.main()
