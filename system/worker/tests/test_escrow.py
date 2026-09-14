"""What has to be true before this machine signs anything.

The subject of this file is a signature that moves somebody else's money. Every
check exists because without it a Worker would sign against a fact it had not
verified, and the contract does not check most of them for us -- its own
documentation says so:

    가격이 온체인에서 강제되지 않습니다. 공급자 노드의 대조가 유일한 방어선입니다.

**The previous version of this module was wrong in four ways at once**, and
none of them raised anything: ``FUNDED`` was 2 when it had become 1, the job
struct was decoded as six fields when it had grown to seven, ownership was
checked against the wrong address entirely, and the ABI was hand-copied from a
branch that was later redesigned. That is why several tests here look paranoid
about numbering and field positions.

No real chain is involved. The escrow is not deployed on 31221 yet, so these
pin decisions rather than transactions -- what is refused, what is retried,
what is treated as unknown, and above all **what is never signed for**.
"""

from __future__ import annotations

import ast
import inspect
import json
import unittest

from gshared_worker.escrow import (
    AcceptOutcome,
    EscrowJob,
    EscrowSettings,
    EscrowUnavailable,
    FundedEvent,
    JobState,
    abort_escrow_job,
    accept_escrow_job,
    check_chain,
    check_deadline,
    check_job_matches,
    check_operator_approved,
)
from gshared_worker.escrow_abi import (
    AbiMismatch,
    EscrowArtifact,
    check_contract_matches,
    default_artifact_path,
)
from gshared_worker.job_rejection import JobRefused, RejectCode
from gshared_worker.worker_job import EscrowTerms

#: This machine. An operator address -- the key behind it never appears here.
OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
#: The human who owns the GPU and gets paid. A different address, always.
PROVIDER = "0x2222222222222222222222222222222222222222"
CLIENT = "0x1111111111111111111111111111111111111111"
EXTERNAL_REF = "0x" + "ab" * 32
PRICE_WEI = 9 * 10 ** 18

SETTINGS = EscrowSettings(
    rpc_url="http://127.0.0.1:8545",
    escrow_address="0x5FbDB2315678afecb367f032d93F642f64180aa3",
    chain_id=31221,
)


def code_only(module) -> str:
    """Source with the prose removed, for the "this never happens" searches."""
    tree = ast.parse(inspect.getsource(module))
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


def terms(**changes) -> EscrowTerms:
    values = {
        "escrow_job_id": 17,
        "external_ref": EXTERNAL_REF,
        "price_wei": PRICE_WEI,
        "client_wallet": CLIENT,
        "provider_wallet": PROVIDER,
    }
    values.update(changes)
    return EscrowTerms(**values)


def a_job(**changes) -> EscrowJob:
    values = {
        "client": CLIENT,
        "state": JobState.FUNDED,
        "deadline": 2_000_000_000,
        "provider": PROVIDER,
        "operator": "0x" + "00" * 20,
        "price_wei": PRICE_WEI,
        "result_hash": "0x" + "00" * 32,
    }
    values.update(changes)
    return EscrowJob(**values)


def a_funded_event(**changes) -> FundedEvent:
    values = {
        "escrow_job_id": 17, "external_ref": EXTERNAL_REF,
        "provider": PROVIDER, "client": CLIENT,
        "price_wei": PRICE_WEI, "deadline": 2_000_000_000,
    }
    values.update(changes)
    return FundedEvent(**values)


class FakeChain:
    """A chain that answers what a test wants, and counts signatures.

    ``sent`` is the point of it. Most tests below assert it is **empty** -- the
    question is not whether a refusal was reported, it is whether a transaction
    was signed before the refusal.
    """

    #: ``funded=None`` has to mean "there is no event", which is one of the
    #: things being tested. A plain default of None would swallow that -- it
    #: did, and the test passed vacuously until it was made to fail.
    DEFAULT = object()

    def __init__(self, job=None, funded=DEFAULT, *, approved=True, now=1_000_000,
                 chain=31221, has_code=True, after=None, receipt=1,
                 send_raises=None):
        self._job = job if job is not None else a_job()
        self._after = after
        self._funded = a_funded_event() if funded is self.DEFAULT else funded
        self._approved = approved
        self._now = now
        self._chain = chain
        self._has_code = has_code
        self._receipt = receipt
        self._send_raises = send_raises
        self.sent: list = []
        self.reads = 0

    def chain_id(self):
        return self._chain

    def has_contract_code(self):
        return self._has_code

    def read_job(self, escrow_job_id):
        self.reads += 1
        if self.reads > 1 and self._after is not None:
            return self._after
        return self._job

    def find_funded_event(self, escrow_job_id):
        return self._funded

    def is_operator(self, provider, operator):
        return self._approved

    def now(self):
        return self._now

    def send_accept(self, escrow_job_id):
        if self._send_raises:
            raise self._send_raises
        self.sent.append(("accept", escrow_job_id))
        return "0x" + "cd" * 32

    def send_abort(self, escrow_job_id):
        if self._send_raises:
            raise self._send_raises
        self.sent.append(("abort", escrow_job_id))
        return "0x" + "ef" * 32

    def receipt_status(self, transaction_hash, timeout):
        return self._receipt


# ---------------------------------------------------------------------------
# The canonical ABI
# ---------------------------------------------------------------------------

class TestTheCanonicalAbi(unittest.TestCase):
    """The ABI is the Blockchain module's artifact, not a copy of it."""

    def setUp(self):
        self.artifact = EscrowArtifact.load()

    def test_the_artifact_is_in_the_repository(self):
        self.assertTrue(default_artifact_path().is_file(),
                        f"no artifact at {default_artifact_path()}")

    def test_it_is_the_escrow_contract(self):
        self.assertEqual(self.artifact.contract_name, "GPUJobEscrow")

    def test_it_records_the_commit_it_came_from(self):
        self.assertNotEqual(self.artifact.source_commit, "unknown")

    def test_it_records_the_compiler(self):
        self.assertTrue(self.artifact.solc_version.startswith("0.8"))

    def test_it_says_whether_it_is_deployed(self):
        self.assertIsInstance(self.artifact.deployed, bool)

    def test_the_state_numbering_matches_this_build(self):
        """The check that would have caught the last redesign on the day.

        The contract renumbered its states and this package did not notice for
        months. Now a renumbering fails here instead of in a transaction.
        """
        self.assertEqual(self.artifact.state_enum, JobState.as_mapping())

    def test_funded_is_one(self):
        # It was 2 in the previous build. Under that reading a 2 -- which now
        # means ASSIGNED -- looked like a job still waiting to be taken.
        self.assertEqual(self.artifact.state_enum[1], "FUNDED")
        self.assertEqual(int(JobState.FUNDED), 1)

    def test_assigned_is_two(self):
        self.assertEqual(self.artifact.state_enum[2], "ASSIGNED")
        self.assertEqual(int(JobState.ASSIGNED), 2)

    def test_there_are_five_states(self):
        self.assertEqual(len(self.artifact.state_enum), 5)
        self.assertEqual(len(list(JobState)), 5)

    def test_jobs_returns_seven_values(self):
        self.assertEqual(len(self.artifact.output_types("jobs")), 7)

    def test_the_job_fields_are_in_the_order_this_build_decodes(self):
        self.assertEqual(
            self.artifact.output_types("jobs"),
            ("address", "uint8", "uint64", "address", "address", "uint256",
             "bytes32"))

    def test_the_functions_this_package_calls_exist(self):
        for name in ("jobs", "acceptJob", "abortJob", "isOperator"):
            with self.subTest(function=name):
                self.assertEqual(self.artifact.function(name)["name"], name)

    def test_the_funded_event_carries_the_external_ref(self):
        names = [i["name"] for i in self.artifact.event("JobFunded")["inputs"]]

        self.assertIn("externalRef", names)
        self.assertIn("jobId", names)

    def test_the_job_id_is_indexed_so_a_node_can_filter_on_it(self):
        inputs = {i["name"]: i for i in self.artifact.event("JobFunded")["inputs"]}

        self.assertTrue(inputs["jobId"]["indexed"])

    def test_the_external_ref_is_not_a_field_of_the_job_struct(self):
        # This is why the event must be read at all. A build that looked for
        # externalRef in jobs() would find nothing and have to invent it.
        outputs = self.artifact.function("jobs")["outputs"]

        self.assertNotIn("externalRef", [o.get("name") for o in outputs])

    def test_a_renumbered_contract_is_refused(self):
        payload = json.loads(default_artifact_path().read_text(encoding="utf-8"))
        payload["stateEnum"] = {"0": "NONE", "1": "CREATED", "2": "FUNDED"}

        with self.assertRaises(AbiMismatch):
            check_contract_matches(
                EscrowArtifact(payload, default_artifact_path()),
                JobState.as_mapping())

    def test_the_real_artifact_passes(self):
        check_contract_matches(self.artifact, JobState.as_mapping())


class TestNoSecondCopyOfTheAbi(unittest.TestCase):
    def test_the_escrow_module_does_not_carry_one(self):
        """A hand-copied ABI is what caused the last four bugs.

        It cannot fail loudly: a wrong ABI decodes the wrong struct into
        plausible numbers and hands them to code that is about to sign.
        """
        from gshared_worker import escrow

        source = code_only(escrow)
        for absent in ("stateMutability", "nonpayable", '"inputs"', "'inputs'"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

class TestDecodingAJob(unittest.TestCase):
    def test_the_seven_fields_land_in_the_right_places(self):
        job = EscrowJob.from_row(
            (CLIENT, 1, 123, PROVIDER, OPERATOR, 5, b"\x00" * 32))

        self.assertEqual(job.client, CLIENT)
        self.assertIs(job.state, JobState.FUNDED)
        self.assertEqual(job.deadline, 123)
        self.assertEqual(job.provider, PROVIDER)
        self.assertEqual(job.operator, OPERATOR)
        self.assertEqual(job.price_wei, 5)

    def test_the_operator_sits_between_provider_and_price(self):
        """The field that was inserted in the middle.

        A six-field decoder read position 4 as the price. It is the operator
        address now -- and an address read as a number is a price no comparison
        would match, or, far worse, one that silently would.
        """
        job = EscrowJob.from_row(
            (CLIENT, 1, 123, PROVIDER, OPERATOR, 999, b"\x00" * 32))

        self.assertEqual(job.operator, OPERATOR)
        self.assertEqual(job.price_wei, 999)

    def test_a_six_field_row_is_refused_rather_than_decoded(self):
        with self.assertRaises(EscrowUnavailable):
            EscrowJob.from_row((CLIENT, 1, 123, PROVIDER, 5, b"\x00" * 32))

    def test_a_job_that_does_not_exist_reads_as_none(self):
        zero = "0x" + "00" * 20

        self.assertFalse(EscrowJob.from_row(
            (zero, 0, 0, zero, zero, 0, b"\x00" * 32)).exists)


# ---------------------------------------------------------------------------
# Pre-accept: the chain against what the Backend claimed
# ---------------------------------------------------------------------------

class TestComparingTheChainToTheAssignment(unittest.TestCase):
    def refusal(self, job=None, funded=None, **term_changes) -> JobRefused:
        with self.assertRaises(JobRefused) as caught:
            check_job_matches(
                job if job is not None else a_job(),
                funded if funded is not None else a_funded_event(),
                terms(**term_changes), operator_address=OPERATOR)
        return caught.exception

    def test_a_matching_job_is_accepted(self):
        self.assertTrue(check_job_matches(
            a_job(), a_funded_event(), terms(), operator_address=OPERATOR))

    def test_a_job_that_does_not_exist(self):
        self.assertIs(self.refusal(job=a_job(state=JobState.NONE)).code,
                      RejectCode.ESCROW_JOB_NOT_FOUND)

    def test_a_different_client(self):
        self.assertIs(self.refusal(job=a_job(client="0x" + "99" * 20)).code,
                      RejectCode.CLIENT_MISMATCH)

    def test_a_different_provider(self):
        self.assertIs(self.refusal(job=a_job(provider="0x" + "99" * 20)).code,
                      RejectCode.PROVIDER_MISMATCH)

    def test_a_different_price(self):
        # The check the contract cannot make. A client writes the price into
        # the funding transaction, so one token could be offered for an A100.
        self.assertIs(self.refusal(job=a_job(price_wei=1)).code,
                      RejectCode.PRICE_MISMATCH)

    def test_a_price_off_by_one_wei(self):
        # Integers, exactly. No tolerance and no rounding.
        self.assertIs(self.refusal(job=a_job(price_wei=PRICE_WEI - 1)).code,
                      RejectCode.PRICE_MISMATCH)

    def test_a_job_that_is_not_funded(self):
        self.assertIs(self.refusal(job=a_job(state=JobState.CANCELLED)).code,
                      RejectCode.INVALID_ESCROW_STATE)

    def test_a_completed_job(self):
        self.assertIs(self.refusal(job=a_job(state=JobState.COMPLETED)).code,
                      RejectCode.INVALID_ESCROW_STATE)

    def test_a_job_assigned_to_somebody_else(self):
        assigned = a_job(state=JobState.ASSIGNED, operator="0x" + "77" * 20)

        self.assertIs(self.refusal(job=assigned).code,
                      RejectCode.INVALID_ESCROW_STATE)

    def test_a_job_already_assigned_to_us_needs_no_transaction(self):
        # Not a refusal and not a second signature: the work is already ours.
        mine = a_job(state=JobState.ASSIGNED, operator=OPERATOR)

        self.assertFalse(check_job_matches(
            mine, a_funded_event(), terms(), operator_address=OPERATOR))

    def test_addresses_compare_without_case(self):
        self.assertTrue(check_job_matches(
            a_job(provider=PROVIDER.upper().replace("0X", "0x")),
            a_funded_event(), terms(), operator_address=OPERATOR))


class TestTheExternalRefComesFromTheEvent(unittest.TestCase):
    """``externalRef`` is not in the job struct. The event is the only source."""

    def test_a_matching_ref_passes(self):
        self.assertTrue(check_job_matches(
            a_job(), a_funded_event(external_ref=EXTERNAL_REF), terms(),
            operator_address=OPERATOR))

    def test_a_different_ref_is_refused(self):
        with self.assertRaises(JobRefused) as caught:
            check_job_matches(
                a_job(), a_funded_event(external_ref="0x" + "cd" * 32),
                terms(), operator_address=OPERATOR)

        self.assertIs(caught.exception.code, RejectCode.EXTERNAL_REF_MISMATCH)

    def test_no_event_at_all_is_refused(self):
        # It could not be confirmed, so it is not assumed.
        with self.assertRaises(JobRefused) as caught:
            check_job_matches(a_job(), None, terms(), operator_address=OPERATOR)

        self.assertIs(caught.exception.code, RejectCode.EXTERNAL_REF_MISMATCH)

    def test_the_ref_is_checked_before_the_rest_of_the_job(self):
        """Order matters.

        Without the ref, client, provider and price are compared against a job
        that may not be the one this assignment is about -- and they could all
        match by coincidence on a job belonging to a different customer.
        """
        with self.assertRaises(JobRefused) as caught:
            check_job_matches(
                a_job(client="0x" + "99" * 20), None, terms(),
                operator_address=OPERATOR)

        self.assertIs(caught.exception.code, RejectCode.EXTERNAL_REF_MISMATCH)

    def test_hex_case_does_not_matter(self):
        upper = EXTERNAL_REF.upper().replace("0X", "0x")

        self.assertTrue(check_job_matches(
            a_job(), a_funded_event(external_ref=upper), terms(),
            operator_address=OPERATOR))


class TestTheDeadline(unittest.TestCase):
    def test_before_the_deadline_passes(self):
        check_deadline(a_job(deadline=1000), 999, 17)

    def test_the_deadline_second_itself_is_still_allowed(self):
        # The contract reverts on `>`, not `>=`. Refusing a second early would
        # turn away work the chain would have taken.
        check_deadline(a_job(deadline=1000), 1000, 17)

    def test_past_the_deadline_is_refused(self):
        with self.assertRaises(JobRefused) as caught:
            check_deadline(a_job(deadline=1000), 1001, 17)

        self.assertIs(caught.exception.code, RejectCode.ACCEPT_DEADLINE_EXPIRED)

    def test_the_clock_is_the_chains_and_not_this_machines(self):
        """A Windows PC a minute fast would refuse jobs the contract accepts.

        The supplier would watch work pass by with nothing in the log to
        explain it.
        """
        from gshared_worker import escrow

        source = code_only(escrow)
        for absent in ("time.time", "datetime.now", "utcnow"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)


class TestOperatorApproval(unittest.TestCase):
    """``isOperator(provider, us)`` -- did the human who gets paid approve us."""

    def test_an_approved_operator_passes(self):
        check_operator_approved(FakeChain(approved=True), PROVIDER, OPERATOR)

    def test_an_unapproved_operator_is_refused(self):
        with self.assertRaises(JobRefused) as caught:
            check_operator_approved(FakeChain(approved=False), PROVIDER, OPERATOR)

        self.assertIs(caught.exception.code, RejectCode.OPERATOR_NOT_APPROVED)

    def test_an_rpc_failure_is_transient_rather_than_a_refusal(self):
        class Broken(FakeChain):
            def is_operator(self, provider, operator):
                raise OSError("no route to host")

        with self.assertRaises(JobRefused) as caught:
            check_operator_approved(Broken(), PROVIDER, OPERATOR)

        self.assertIs(caught.exception.code, RejectCode.BLOCKCHAIN_RPC_FAILED)
        self.assertTrue(caught.exception.transient)


class TestProviderIsNotTheOperator(unittest.TestCase):
    """The mistake the previous build made, pinned so it cannot come back.

    ``provider`` is a person's settlement wallet; ``operator`` is this machine's
    signing key. The contract separates them so a program can sign without
    holding the money, so ``provider == our address`` can never be true in the
    finished system -- the old check would have refused every real job.
    """

    def test_a_job_whose_provider_is_not_us_is_still_acceptable(self):
        self.assertTrue(check_job_matches(
            a_job(provider=PROVIDER), a_funded_event(), terms(),
            operator_address=OPERATOR))

    def test_nothing_compares_the_provider_to_our_own_wallet(self):
        from gshared_worker import escrow

        source = code_only(escrow)
        for absent in ("job.provider == operator", "provider == wallet",
                       "wallet_address"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_the_provider_is_compared_to_the_assignment_instead(self):
        with self.assertRaises(JobRefused) as caught:
            check_job_matches(
                a_job(provider="0x" + "99" * 20), a_funded_event(), terms(),
                operator_address=OPERATOR)

        self.assertIs(caught.exception.code, RejectCode.PROVIDER_MISMATCH)


class TestTheChainAndTheContract(unittest.TestCase):
    def test_the_right_chain_passes(self):
        check_chain(FakeChain(chain=31221), SETTINGS)

    def test_a_different_chain_is_refused(self):
        with self.assertRaises(EscrowUnavailable):
            check_chain(FakeChain(chain=1), SETTINGS)

    def test_an_address_with_no_contract_is_refused(self):
        with self.assertRaises(EscrowUnavailable):
            check_chain(FakeChain(has_code=False), SETTINGS)

    def test_an_unreachable_node_is_transient(self):
        class Broken(FakeChain):
            def chain_id(self):
                raise OSError("connection refused")

        with self.assertRaises(JobRefused) as caught:
            check_chain(Broken(), SETTINGS)

        self.assertTrue(caught.exception.transient)

    def test_the_contract_address_comes_from_settings_and_nowhere_else(self):
        """Every line that *reads* an address reads it from configuration.

        The declaration on EscrowSettings is skipped -- it is where the field
        is defined, not a use of it.
        """
        from gshared_worker import escrow

        for line in code_only(escrow).splitlines():
            stripped = line.strip()
            if stripped.startswith("escrow_address:"):
                continue                      # the dataclass field itself
            if stripped.startswith("return bool(self.rpc_url"):
                continue                      # EscrowSettings.is_configured
            if "escrow_address" in line:
                self.assertIn("_settings", line, line)


# ---------------------------------------------------------------------------
# Accepting
# ---------------------------------------------------------------------------

class TestAccepting(unittest.TestCase):
    def accept(self, chain, **changes):
        return accept_escrow_job(
            chain, SETTINGS, terms(**changes), operator_address=OPERATOR)

    def test_a_good_job_is_signed_for(self):
        chain = FakeChain(after=a_job(state=JobState.ASSIGNED, operator=OPERATOR))

        result = self.accept(chain)

        self.assertIs(result.outcome, AcceptOutcome.ACCEPTED)
        self.assertEqual(chain.sent, [("accept", 17)])

    def test_the_transaction_hash_comes_back(self):
        chain = FakeChain(after=a_job(state=JobState.ASSIGNED, operator=OPERATOR))

        self.assertTrue(self.accept(chain).transaction_hash.startswith("0x"))

    def test_success_is_confirmed_by_reading_the_job_back(self):
        """A receipt is not enough.

        A receipt says a transaction succeeded. Reading the job back says this
        machine is the operator on an ASSIGNED job, which is what has to be
        true before the Backend is told anything.
        """
        chain = FakeChain(after=a_job(state=JobState.FUNDED))

        self.assertIsNot(self.accept(chain).outcome, AcceptOutcome.ACCEPTED)

    def test_a_job_already_ours_sends_nothing(self):
        chain = FakeChain(a_job(state=JobState.ASSIGNED, operator=OPERATOR))

        result = self.accept(chain)

        self.assertTrue(result.accepted)
        self.assertEqual(chain.sent, [])

    def test_a_reverted_transaction_is_a_settled_no(self):
        chain = FakeChain(receipt=0)

        result = self.accept(chain)

        self.assertIs(result.outcome, AcceptOutcome.REFUSED)
        self.assertIs(result.code, RejectCode.TRANSACTION_FAILED)

    def test_a_receipt_that_never_arrives_is_uncertain(self):
        # Something of ours may still land, so the caller keeps the slot.
        chain = FakeChain(receipt=None, after=a_job(state=JobState.COMPLETED))

        self.assertIs(self.accept(chain).outcome, AcceptOutcome.UNCERTAIN)

    def test_a_send_that_raised_is_reconciled_rather_than_assumed(self):
        """The send may have reached the node before the connection broke.

        Assuming failure sends a second transaction for the same job.
        """
        chain = FakeChain(send_raises=OSError("connection reset"),
                          after=a_job(state=JobState.ASSIGNED, operator=OPERATOR))

        result = self.accept(chain)

        self.assertTrue(result.accepted)
        self.assertGreater(chain.reads, 1)

    def test_a_timed_out_receipt_that_actually_landed_is_recovered(self):
        chain = FakeChain(receipt=None,
                          after=a_job(state=JobState.ASSIGNED, operator=OPERATOR))

        self.assertTrue(self.accept(chain).accepted)


class TestNothingIsSignedOnAnUncheckedFact(unittest.TestCase):
    """Every one of these must end with no transaction sent."""

    def sent_after(self, **kwargs):
        chain = FakeChain(**kwargs)
        accept_escrow_job(chain, SETTINGS, terms(), operator_address=OPERATOR)
        return chain.sent

    def test_wrong_client(self):
        self.assertEqual(self.sent_after(job=a_job(client="0x" + "99" * 20)), [])

    def test_wrong_provider(self):
        self.assertEqual(self.sent_after(job=a_job(provider="0x" + "99" * 20)), [])

    def test_wrong_price(self):
        self.assertEqual(self.sent_after(job=a_job(price_wei=1)), [])

    def test_wrong_state(self):
        self.assertEqual(self.sent_after(job=a_job(state=JobState.COMPLETED)), [])

    def test_expired_deadline(self):
        self.assertEqual(
            self.sent_after(job=a_job(deadline=10), now=1_000_000), [])

    def test_mismatched_external_ref(self):
        self.assertEqual(
            self.sent_after(funded=a_funded_event(external_ref="0x" + "11" * 32)),
            [])

    def test_missing_funded_event(self):
        self.assertEqual(self.sent_after(funded=None), [])

    def test_unapproved_operator(self):
        self.assertEqual(self.sent_after(approved=False), [])

    def test_wrong_chain(self):
        self.assertEqual(self.sent_after(chain=1), [])

    def test_no_contract_at_the_address(self):
        self.assertEqual(self.sent_after(has_code=False), [])

    def test_a_job_that_does_not_exist(self):
        self.assertEqual(self.sent_after(job=a_job(state=JobState.NONE)), [])


class TestTheRefusalSaysWhy(unittest.TestCase):
    def code_for(self, **kwargs):
        return accept_escrow_job(
            FakeChain(**kwargs), SETTINGS, terms(),
            operator_address=OPERATOR).code

    def test_price(self):
        self.assertIs(self.code_for(job=a_job(price_wei=1)),
                      RejectCode.PRICE_MISMATCH)

    def test_client(self):
        self.assertIs(self.code_for(job=a_job(client="0x" + "99" * 20)),
                      RejectCode.CLIENT_MISMATCH)

    def test_provider(self):
        self.assertIs(self.code_for(job=a_job(provider="0x" + "99" * 20)),
                      RejectCode.PROVIDER_MISMATCH)

    def test_approval(self):
        self.assertIs(self.code_for(approved=False),
                      RejectCode.OPERATOR_NOT_APPROVED)

    def test_deadline(self):
        self.assertIs(self.code_for(job=a_job(deadline=10), now=1_000_000),
                      RejectCode.ACCEPT_DEADLINE_EXPIRED)

    def test_missing_job(self):
        self.assertIs(self.code_for(job=a_job(state=JobState.NONE)),
                      RejectCode.ESCROW_JOB_NOT_FOUND)

    def test_only_rpc_trouble_is_worth_retrying(self):
        for kwargs in ({"job": a_job(price_wei=1)},
                       {"approved": False},
                       {"job": a_job(state=JobState.COMPLETED)}):
            with self.subTest(kwargs=str(kwargs)):
                result = accept_escrow_job(
                    FakeChain(**kwargs), SETTINGS, terms(),
                    operator_address=OPERATOR)
                self.assertFalse(result.transient)


# ---------------------------------------------------------------------------
# Aborting
# ---------------------------------------------------------------------------

class TestAborting(unittest.TestCase):
    """A capability, so a test job does not hold a deposit for 30 hours."""

    def abort(self, chain):
        return abort_escrow_job(
            chain, SETTINGS, escrow_job_id=17, operator_address=OPERATOR)

    def test_our_assigned_job_can_be_given_back(self):
        chain = FakeChain(a_job(state=JobState.ASSIGNED, operator=OPERATOR),
                          after=a_job(state=JobState.CANCELLED))

        result = self.abort(chain)

        self.assertTrue(result.accepted)
        self.assertEqual(chain.sent, [("abort", 17)])

    def test_the_end_state_is_confirmed_by_reading_it_back(self):
        chain = FakeChain(a_job(state=JobState.ASSIGNED, operator=OPERATOR),
                          after=a_job(state=JobState.ASSIGNED, operator=OPERATOR))

        self.assertFalse(self.abort(chain).accepted)

    def test_another_operators_job_is_never_aborted(self):
        """``abortJob`` requires ``msg.sender == job.operator``.

        A job accepted with another key can only be finished by that key, so
        trying reads as an attempt to interfere with it.
        """
        chain = FakeChain(
            a_job(state=JobState.ASSIGNED, operator="0x" + "77" * 20))

        result = self.abort(chain)

        self.assertIs(result.outcome, AcceptOutcome.REFUSED)
        self.assertEqual(chain.sent, [])
        self.assertIs(result.code, RejectCode.OPERATOR_ADDRESS_MISMATCH)

    def test_a_funded_job_is_not_aborted(self):
        chain = FakeChain(a_job(state=JobState.FUNDED))

        result = self.abort(chain)

        self.assertIs(result.outcome, AcceptOutcome.REFUSED)
        self.assertEqual(chain.sent, [])

    def test_an_already_cancelled_job_sends_nothing(self):
        chain = FakeChain(a_job(state=JobState.CANCELLED))

        self.assertTrue(self.abort(chain).accepted)
        self.assertEqual(chain.sent, [])

    def test_a_completed_job_is_not_aborted(self):
        chain = FakeChain(a_job(state=JobState.COMPLETED, operator=OPERATOR))

        self.assertIs(self.abort(chain).outcome, AcceptOutcome.REFUSED)
        self.assertEqual(chain.sent, [])

    def test_a_send_that_raised_is_reconciled(self):
        chain = FakeChain(a_job(state=JobState.ASSIGNED, operator=OPERATOR),
                          send_raises=OSError("reset"),
                          after=a_job(state=JobState.CANCELLED))

        self.assertTrue(self.abort(chain).accepted)

    def test_nothing_aborts_a_job_on_its_own(self):
        """Accepting never leads to aborting.

        A job accepted and waiting for a GPU runtime that does not exist yet is
        not a failure, and aborting it automatically would give the work away.
        """
        from gshared_worker import escrow, job_acceptance, worker_connection

        # The accept path never mentions it.
        source = code_only(escrow)
        accept_body = source[source.index("def accept_escrow_job"):
                             source.index("def abort_escrow_job")]
        self.assertNotIn("abort", accept_body)

        # One caller, and it is the cleanup the Backend has to ask for by
        # name. `escrow_cleaner` is only reached from a JOB_CANCEL carrying
        # ACCEPT_VALIDATION_FAILED -- see test_job_cancel.py.
        callers = [line for line in code_only(job_acceptance).splitlines()
                   if "abort_escrow_job" in line]
        self.assertEqual(len(callers), 2, callers)   # the import and the call

        # Nothing in the connection layer reaches for it directly: it goes
        # through the background thread like every other piece of chain work.
        self.assertNotIn("abort_escrow_job", code_only(worker_connection))


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

class TestNothingLeaksTheKey(unittest.TestCase):
    def source(self):
        from gshared_worker import escrow

        return code_only(escrow)

    def test_the_key_is_used_on_exactly_one_line(self):
        lines = [line for line in self.source().splitlines()
                 if "private_key" in line]

        self.assertEqual(len(lines), 1, lines)
        self.assertIn("sign_transaction", lines[0])

    def test_it_is_never_logged(self):
        for line in self.source().splitlines():
            if "_debug" in line:
                self.assertNotIn("private_key", line)

    def test_failures_report_a_type_name_and_nothing_else(self):
        # A library's exception message can carry what it was given.
        self.assertIn("type(error).__name__", self.source())

    def test_it_submits_a_result_but_never_computes_one(self):
        """``submitResult`` lives here now. Hashing still does not.

        This module writes a hash to the chain; deciding what that hash is
        belongs to :mod:`gshared_worker.result_artifact`, which takes it from
        the exact bytes that were uploaded. A digest computed here would be a
        second answer to the one question where two answers is the failure.
        """
        source = self.source()

        self.assertIn("submitResult", source)
        for absent in ("hashlib", "sha256(", "open(", "read(", "Path("):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_the_hash_it_is_given_is_checked_before_it_is_sent(self):
        # bytes32 is thirty-two bytes. A short hash would be left-padded by
        # the encoder into a number that is not the file's digest, written to
        # a contract that cannot be corrected.
        from gshared_worker.escrow import _result_hash

        self.assertEqual(_result_hash("AB" * 32), "0x" + "ab" * 32)
        for wrong in ("0xdeadbeef", "", "0x" + "zz" * 32, "ab" * 31):
            with self.subTest(wrong=wrong):
                with self.assertRaises(ValueError):
                    _result_hash(wrong)

    def test_it_never_reaches_a_container(self):
        for absent in ("docker", "subprocess", "Popen", "environment"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, self.source())


if __name__ == "__main__":
    unittest.main()
