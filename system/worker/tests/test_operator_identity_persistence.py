"""Operator identity outlives Worker registration and connection lifecycles."""

from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gshared_worker import auth_check, config, worker_credentials, worker_wallet
from gshared_worker.auth_check import authenticate_worker
from gshared_worker.benchmark import BenchmarkOutcome, BenchmarkReport, BenchmarkResult
from gshared_worker.config import CONFIG_FILE_NAME, WorkerSettings
from gshared_worker.exit_codes import WorkerExitCode
from gshared_worker.machine_facts import MachineFacts
from gshared_worker.worker_auth import RegistryAnswer, WorkerAuthState
from gshared_worker.worker_credentials import WorkerCredentials
from gshared_worker.worker_wallet import WALLET_FILE_NAME, prepare_wallet

ADDRESS = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
OTHER_ADDRESS = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
OTHER_KEY = "0x8b3a350cf5c34c9194ca3a545d4f0b0d6cc3e14c4f8c7bb4b92c08f4c8b1f7e1"


def seal(secret: str) -> str:
    return base64.b64encode(secret.encode("utf-8")).decode("ascii")


def unseal(protected: str) -> str:
    return base64.b64decode(protected.encode("ascii"), validate=True).decode("utf-8")


class Accounts:
    def __init__(self, identities=None):
        self.identities = list(identities or [(ADDRESS, KEY)])
        self.created = 0

    def create(self):
        identity = self.identities[min(self.created, len(self.identities) - 1)]
        self.created += 1
        return identity

    def address_of(self, private_key):
        known = {key: address for address, key in self.identities}
        return known[private_key]


def measured() -> BenchmarkReport:
    return BenchmarkReport(
        BenchmarkOutcome.MEASURED,
        "1,250 tokens/sec over 3 runs",
        BenchmarkResult(
            tokens_per_sec=1250.0,
            processed_tokens=122880,
            elapsed_seconds=98.3,
            measured_steps=60,
            batch_size=4,
            sequence_length=512,
            run_rates=(1250.0, 1250.0, 1250.0),
        ),
    )


FACTS = MachineFacts(
    device_name="DESKTOP-TEST",
    os_version="Windows 10.0.26200",
    gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU",
    vram_mb=8188,
    driver_version="596.36",
)

SETTINGS = WorkerSettings(
    backend_url="https://j15e204.p.ssafy.io",
    worker_id="worker-before",
    hourly_price=600,
    gpu_index=0,
    supported_workloads=(),
    worker_token=None,
    config_path=None,
)


class Registry:
    def __init__(self):
        self.answers = [
            RegistryAnswer(
                WorkerAuthState.CREDENTIALS_INVALIDATED,
                "the old worker was deleted",
            ),
            RegistryAnswer(WorkerAuthState.REGISTERED, "accepted"),
        ]
        self.registered = []
        self.verified = []

    def verify(self, credentials):
        self.verified.append(credentials)
        return self.answers.pop(0)

    def register(self, access_token, registration):
        self.registered.append((access_token, registration))
        return RegistryAnswer(
            WorkerAuthState.REGISTERED,
            "registered",
            WorkerCredentials("worker-after", "new-worker-token"),
        )


class OperatorPersistenceTest(unittest.TestCase):
    def setUp(self):
        self._temporary = TemporaryDirectory()
        self.directory = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def test_first_creation_restart_and_settings_reload_keep_one_operator(self):
        accounts = Accounts()

        first = prepare_wallet(
            lambda *parts: None,
            self.directory,
            protector=seal,
            unprotector=unseal,
            accounts=accounts,
        )
        worker_credentials.store_credentials(
            "worker-001",
            "worker-token",
            self.directory,
            protector=seal,
        )
        restarted = prepare_wallet(
            lambda *parts: None,
            self.directory,
            protector=seal,
            unprotector=unseal,
            accounts=accounts,
        )

        self.assertEqual(accounts.created, 1)
        self.assertEqual(restarted.address, first.address)
        self.assertEqual(restarted.private_key, first.private_key)

    def test_invalid_token_reregistration_uses_the_real_stored_operator(self):
        accounts = Accounts()
        original = prepare_wallet(
            lambda *parts: None,
            self.directory,
            protector=seal,
            unprotector=unseal,
            accounts=accounts,
        )
        wallet_path = self.directory / WALLET_FILE_NAME
        wallet_before = wallet_path.read_bytes()
        (self.directory / CONFIG_FILE_NAME).write_text(
            json.dumps({
                "workerId": "worker-before",
                "workerTokenProtected": seal("dead-worker-token"),
            }),
            encoding="utf-8",
        )
        registry = Registry()

        with (
            patch.object(config, "worker_directory", lambda: self.directory),
            patch.object(worker_credentials, "protect_current_user", seal),
            patch.object(worker_credentials, "unprotect_current_user", unseal),
            patch.object(worker_wallet, "protect_current_user", seal),
            patch.object(worker_wallet, "unprotect_current_user", unseal),
            patch.object(worker_wallet, "EthAccounts", lambda: accounts),
        ):
            code = authenticate_worker(
                SETTINGS,
                lambda *parts: None,
                registry=registry,
                ask_to_sign_in=lambda: "user-access-token",
                describe=lambda: FACTS,
                benchmark=measured,
            )

        self.assertEqual(code, WorkerExitCode.OK)
        self.assertEqual(accounts.created, 1)
        self.assertEqual(wallet_path.read_bytes(), wallet_before)
        payload = registry.registered[0][1].as_payload()
        self.assertEqual(payload["operatorAddress"], original.address)
        stored = json.loads((self.directory / CONFIG_FILE_NAME).read_text("utf-8"))
        self.assertEqual(stored["workerId"], "worker-after")

    def test_complete_local_data_removal_is_the_only_case_that_makes_a_new_operator(self):
        accounts = Accounts([(ADDRESS, KEY), (OTHER_ADDRESS, OTHER_KEY)])
        first = prepare_wallet(
            lambda *parts: None,
            self.directory,
            protector=seal,
            unprotector=unseal,
            accounts=accounts,
        )
        for path in self.directory.iterdir():
            path.unlink()

        after_reset = prepare_wallet(
            lambda *parts: None,
            self.directory,
            protector=seal,
            unprotector=unseal,
            accounts=accounts,
        )

        self.assertEqual(accounts.created, 2)
        self.assertNotEqual(after_reset.address, first.address)

if __name__ == "__main__":
    unittest.main()
