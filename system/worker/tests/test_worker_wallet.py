"""This machine's signing key: made once, sealed, and never quietly replaced.

No ``eth_account`` here. Key generation and address derivation go through a
Protocol, so everything this module decides -- when to create, when to refuse,
what is written down -- is exercised without the dependency.

DPAPI is the same: :mod:`gshared_worker.credentials` takes an injectable API,
and the fakes below stand in for it exactly as the credential tests do.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gshared_worker.worker_wallet import (
    plan_operator_migration,
    OperatorPlan,
    check_operator_matches,
    WALLET_FILE_NAME,
    WalletError,
    WalletState,
    WorkerWallet,
    create_wallet,
    load_wallet,
    prepare_wallet,
    wallet_file_path,
)

KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
ADDRESS = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
OTHER = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


class FakeAccounts:
    """Stands in for eth_account. Deterministic, and counts what it made."""

    def __init__(self, address=ADDRESS, key=KEY, derives=None) -> None:
        self._address = address
        self._key = key
        self._derives = derives
        self.created = 0

    def create(self):
        self.created += 1
        return self._address, self._key

    def address_of(self, private_key):
        if self._derives is not None:
            return self._derives(private_key)
        if private_key != self._key:
            raise ValueError("not a key this fake knows")
        return self._address


def sealing():
    """A DPAPI stand-in.

    Base64 rather than a prefixed copy. The first version of this fake was
    ``"sealed:" + secret``, and the test asserting the key is not written in
    the clear failed against it -- correctly. A stand-in for encryption whose
    output contains the plaintext cannot be used to check that the plaintext
    is not stored.
    """
    import base64

    def seal(secret: str) -> str:
        return base64.b64encode(secret.encode("utf-8")).decode("ascii")

    def unseal(blob: str) -> str:
        try:
            return base64.b64decode(
                blob.encode("ascii"), validate=True).decode("utf-8")
        except Exception:
            return _refuse()

    return seal, unseal


def _refuse():
    raise ValueError("this blob was sealed for another account")


class WalletTestCase(unittest.TestCase):
    def setUp(self):
        self._directory = TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)
        self.protect, self.unprotect = sealing()

    def said(self):
        lines = []
        return lines, lambda *parts: lines.append(" ".join(str(p) for p in parts))

    def stored(self):
        return json.loads(
            wallet_file_path(self.directory).read_text(encoding="utf-8"))

    def make(self, accounts=None):
        return prepare_wallet(
            lambda *p: None, self.directory,
            protector=self.protect, unprotector=self.unprotect,
            accounts=accounts or FakeAccounts())


class TestMakingOne(unittest.TestCase):
    def setUp(self):
        WalletTestCase.setUp(self)

    make = WalletTestCase.make
    stored = WalletTestCase.stored
    said = WalletTestCase.said

    def test_a_machine_with_no_wallet_gets_one(self):
        wallet = self.make()

        self.assertEqual(wallet.address, ADDRESS)

    def test_it_is_made_exactly_once(self):
        accounts = FakeAccounts()

        self.make(accounts)

        self.assertEqual(accounts.created, 1)

    def test_starting_again_reuses_it_rather_than_making_another(self):
        accounts = FakeAccounts()
        self.make(accounts)

        again = self.make(accounts)

        self.assertEqual(accounts.created, 1)
        self.assertEqual(again.address, ADDRESS)

    def test_the_key_comes_back_the_same(self):
        first = self.make()
        again = self.make()

        self.assertEqual(first.private_key, again.private_key)

    def test_the_supplier_is_told_which_wallet_it_is(self):
        lines, say = self.said()

        prepare_wallet(say, self.directory, protector=self.protect,
                       unprotector=self.unprotect, accounts=FakeAccounts())

        self.assertTrue(any("0x7099...79C8" in line for line in lines))


class TestWhatIsWrittenDown(unittest.TestCase):
    setUp = WalletTestCase.setUp
    make = WalletTestCase.make
    stored = WalletTestCase.stored

    def test_the_address_is_recorded_in_the_open(self):
        self.make()

        self.assertEqual(self.stored()["address"], ADDRESS)

    def test_the_key_is_not(self):
        self.make()

        raw = wallet_file_path(self.directory).read_text(encoding="utf-8")
        self.assertNotIn(KEY, raw)

    def test_the_key_is_sealed_instead(self):
        self.make()

        import base64

        sealed = self.stored()["privateKeyProtected"]
        self.assertNotIn(KEY, sealed)
        self.assertEqual(base64.b64decode(sealed).decode("utf-8"), KEY)

    def test_the_file_holds_nothing_else(self):
        self.make()

        self.assertEqual(set(self.stored()), {"address", "privateKeyProtected"})

    def test_there_is_no_wallet_json(self):
        # The blockchain notes sketch a wallet.json with a plaintext
        # privateKey. This is not that file and does not become it.
        self.make()

        self.assertFalse((self.directory / "wallet.json").exists())
        self.assertEqual(wallet_file_path(self.directory).name, WALLET_FILE_NAME)

    def test_an_interrupted_write_leaves_nothing_behind(self):
        class Exploding(FakeAccounts):
            def create(self):
                raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            create_wallet(self.directory, protector=self.protect,
                          accounts=Exploding())

        self.assertEqual(list(self.directory.iterdir()), [])


class TestRefusingToReplaceOne(unittest.TestCase):
    """The easy thing to do with an unreadable key is overwrite it."""

    setUp = WalletTestCase.setUp
    make = WalletTestCase.make

    def test_a_key_sealed_for_somebody_else_is_not_replaced(self):
        self.make()
        accounts = FakeAccounts()

        with self.assertRaises(WalletError):
            prepare_wallet(lambda *p: None, self.directory,
                           protector=self.protect,
                           unprotector=lambda blob: _refuse(),
                           accounts=accounts)

        self.assertEqual(accounts.created, 0)

    def test_the_file_survives_that_refusal(self):
        self.make()

        try:
            prepare_wallet(lambda *p: None, self.directory,
                           protector=self.protect,
                           unprotector=lambda blob: _refuse(),
                           accounts=FakeAccounts())
        except WalletError:
            pass

        self.assertTrue(wallet_file_path(self.directory).is_file())

    def test_a_key_that_does_not_match_its_address_is_refused(self):
        # The most dangerous case: both halves are readable and they disagree.
        # Guessing which is right means either signing as somebody else or
        # abandoning the identity that holds a balance.
        self.make()
        mismatched = FakeAccounts(derives=lambda key: OTHER)

        with self.assertRaises(WalletError):
            prepare_wallet(lambda *p: None, self.directory,
                           protector=self.protect, unprotector=self.unprotect,
                           accounts=mismatched)

    def test_the_refusal_says_what_is_wrong_without_the_key(self):
        self.make()

        with self.assertRaises(WalletError) as refused:
            prepare_wallet(lambda *p: None, self.directory,
                           protector=self.protect,
                           unprotector=lambda blob: _refuse(),
                           accounts=FakeAccounts())

        self.assertNotIn(KEY, str(refused.exception))

    def test_a_half_written_file_is_refused_rather_than_completed(self):
        wallet_file_path(self.directory).write_text(
            json.dumps({"address": ADDRESS}), encoding="utf-8")

        with self.assertRaises(WalletError):
            self.make()

    def test_a_file_that_is_not_json_is_refused(self):
        wallet_file_path(self.directory).write_text("{ half", encoding="utf-8")

        with self.assertRaises(WalletError):
            self.make()

    def test_an_address_that_is_not_an_address_is_refused(self):
        wallet_file_path(self.directory).write_text(
            json.dumps({"address": "nonsense", "privateKeyProtected": "x"}),
            encoding="utf-8")

        with self.assertRaises(WalletError):
            self.make()

    def test_a_key_the_library_cannot_use_is_refused(self):
        wallet_file_path(self.directory).write_text(
            json.dumps({"address": ADDRESS, "privateKeyProtected": "anVuaw=="}),
            encoding="utf-8")

        with self.assertRaises(WalletError):
            self.make()


class TestTheStatesItReports(unittest.TestCase):
    setUp = WalletTestCase.setUp
    make = WalletTestCase.make

    def test_nothing_on_disk_is_absent_rather_than_broken(self):
        loaded = load_wallet(self.directory, unprotector=self.unprotect,
                             accounts=FakeAccounts())

        self.assertIs(loaded.state, WalletState.ABSENT)

    def test_a_good_one_is_present(self):
        self.make()

        loaded = load_wallet(self.directory, unprotector=self.unprotect,
                             accounts=FakeAccounts())

        self.assertIs(loaded.state, WalletState.PRESENT)
        self.assertTrue(loaded.is_present)

    def test_a_broken_one_is_unusable_and_not_absent(self):
        # The difference decides everything: ABSENT means make one.
        self.make()

        loaded = load_wallet(self.directory, unprotector=lambda blob: _refuse(),
                             accounts=FakeAccounts())

        self.assertIs(loaded.state, WalletState.UNUSABLE)


class TestTheKeyDoesNotLeak(unittest.TestCase):
    def test_the_repr_does_not_carry_it(self):
        wallet = WorkerWallet(address=ADDRESS, private_key=KEY)

        self.assertNotIn(KEY, repr(wallet))

    def test_the_string_form_does_not_either(self):
        wallet = WorkerWallet(address=ADDRESS, private_key=KEY)

        self.assertNotIn(KEY, str(wallet))
        self.assertNotIn(KEY, f"{wallet}")

    def test_the_short_form_is_the_address(self):
        wallet = WorkerWallet(address=ADDRESS, private_key=KEY)

        self.assertEqual(wallet.short, "0x7099...79C8")

    def test_two_wallets_on_the_same_address_compare_equal(self):
        # The key is compare=False, which is what keeps it out of the repr.
        self.assertEqual(
            WorkerWallet(ADDRESS, KEY), WorkerWallet(ADDRESS, "0xdifferent"))


class TestWhatThisModuleDoesNotDo(unittest.TestCase):
    def source(self):
        import ast
        import inspect

        from gshared_worker import worker_wallet

        tree = ast.parse(inspect.getsource(worker_wallet))
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

    def test_the_key_is_never_logged(self):
        source = self.source()

        for line in source.splitlines():
            if "_debug" in line or "say(" in line:
                self.assertNotIn("private_key", line)

    def test_it_never_sends_the_key_anywhere(self):
        for absent in ("urllib", "requests", "httpx", "socket", "BackendClient"):
            self.assertNotIn(absent, self.source())

    def test_it_has_no_seed_phrase_anything(self):
        # A supplier's own wallet is never imported here. There is nothing to
        # paste a mnemonic into, deliberately.
        for absent in ("mnemonic", "seed", "from_mnemonic", "import_key"):
            self.assertNotIn(absent, self.source().lower())


if __name__ == "__main__":
    unittest.main()


class OperatorMigrationTest(unittest.TestCase):
    """Attaching an address to a Worker that registered before keys existed.

    Two refusals here, and both protect the same thing: a job accepted with one
    key can only be finished by that key. ``submitResult`` and ``abortJob``
    check ``msg.sender == job.operator``, so replacing the key under a running
    job strands it until its execution timeout expires.
    """

    LOCAL = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    OTHER = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"

    def test_a_worker_with_no_address_may_register_one(self):
        decision = plan_operator_migration(self.LOCAL, None, holding_a_job=False)

        self.assertIs(decision.plan, OperatorPlan.REGISTER)
        self.assertTrue(decision.may_register)

    def test_an_empty_address_counts_as_none(self):
        decision = plan_operator_migration(self.LOCAL, "", holding_a_job=False)

        self.assertTrue(decision.may_register)

    def test_a_worker_that_already_has_ours_sends_nothing(self):
        decision = plan_operator_migration(
            self.LOCAL, self.LOCAL, holding_a_job=False)

        self.assertIs(decision.plan, OperatorPlan.ALREADY_REGISTERED)
        self.assertFalse(decision.may_register)

    def test_the_comparison_ignores_case(self):
        # Checksummed and lowercase forms of one address are one address.
        decision = plan_operator_migration(
            self.LOCAL, self.LOCAL.lower(), holding_a_job=False)

        self.assertIs(decision.plan, OperatorPlan.ALREADY_REGISTERED)

    def test_a_different_address_is_never_overwritten(self):
        """The refusal that matters.

        Overwriting would orphan every job the other key accepted: they could
        then be finished by nobody, and their deposits would sit locked until
        the execution timeout ran out.
        """
        decision = plan_operator_migration(
            self.LOCAL, self.OTHER, holding_a_job=False)

        self.assertIs(decision.plan, OperatorPlan.BLOCKED_MISMATCH)
        self.assertFalse(decision.may_register)

    def test_the_mismatch_says_why_rather_than_just_no(self):
        decision = plan_operator_migration(
            self.LOCAL, self.OTHER, holding_a_job=False)

        self.assertIn("finished by nobody", decision.detail)

    def test_rotation_is_refused_while_a_job_is_held(self):
        decision = plan_operator_migration(self.LOCAL, None, holding_a_job=True)

        self.assertIs(decision.plan, OperatorPlan.BLOCKED_ACTIVE_JOB)
        self.assertFalse(decision.may_register)

    def test_an_active_job_wins_over_everything_else(self):
        # Even when the addresses agree and nothing would actually change.
        decision = plan_operator_migration(
            self.LOCAL, self.LOCAL, holding_a_job=True)

        self.assertIs(decision.plan, OperatorPlan.BLOCKED_ACTIVE_JOB)


class TheKeyOnThisPcIsTheRegisteredOneTest(unittest.TestCase):
    """The last key check before accepting: is this the *right* key.

    Loading proves a key is readable and internally consistent. It does not
    prove it is the one the provider approved -- and signing with an unapproved
    key produces a run of contract rejections that look like somebody else's
    fault.
    """

    LOCAL = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

    def wallet(self, address=None):
        return WorkerWallet(address=address or self.LOCAL,
                            private_key="not-a-real-key")

    def test_a_matching_address_passes(self):
        check_operator_matches(self.wallet(), self.LOCAL)

    def test_case_does_not_matter(self):
        check_operator_matches(self.wallet(), self.LOCAL.lower())

    def test_a_different_address_is_refused(self):
        with self.assertRaises(WalletError):
            check_operator_matches(
                self.wallet(), "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")

    def test_no_registered_address_at_all_is_refused(self):
        # Fail closed. Nothing confirms this key is the one that was approved.
        with self.assertRaises(WalletError):
            check_operator_matches(self.wallet(), "")

    def test_the_refusal_never_quotes_the_key(self):
        try:
            check_operator_matches(self.wallet(), "")
        except WalletError as refused:
            self.assertNotIn("not-a-real-key", str(refused))
