"""Which chain and contract this Worker signs against, and where that comes from.

Like the Backend address: a built-in default, a configuration file that
overrides it, an environment variable that overrides both. The default
contract is the one the Blockchain team deployed on the SSAFY chain, checked
against their deployment record rather than retyped here.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gshared_worker.config import (
    CONFIG_FILE_NAME,
    DEFAULT_CHAIN_ID,
    DEFAULT_ESCROW_ADDRESS,
    DEFAULT_ESCROW_RPC_URL,
    ENV_CHAIN_ID,
    ENV_ESCROW_ADDRESS,
    ENV_ESCROW_RPC_URL,
    resolve_settings,
)
from gshared_worker.errors import WorkerConfigurationError
from gshared_worker.escrow import SSAFY_CHAIN_ID

HARDHAT_ESCROW = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"

#: The Blockchain team's record of the SSAFY deployment, in this checkout.
SSAFY_DEPLOYMENT = (Path(__file__).resolve().parents[3]
                    / "blockchain" / "deployments" / "ssafy-31221" / "GPUJobEscrow.json")


class Folder:
    def __init__(self, values=None):
        self._temporary = TemporaryDirectory()
        self.path = Path(self._temporary.name)
        if values is not None:
            (self.path / CONFIG_FILE_NAME).write_text(json.dumps(values), encoding="utf-8")

    def __enter__(self):
        return self.path

    def __exit__(self, *_):
        self._temporary.cleanup()
        return False


class TheDefaultsTest(unittest.TestCase):
    def test_it_is_the_ssafy_chain(self):
        self.assertEqual(DEFAULT_CHAIN_ID, SSAFY_CHAIN_ID)
        self.assertEqual(DEFAULT_ESCROW_RPC_URL, "https://rpc.ssafy-blockchain.com")

    def test_the_default_contract_is_the_recorded_ssafy_deployment(self):
        """Compared with the Blockchain team's record, not with a retyped copy.

        A redeployment changes that file; this fails until the Worker follows.
        """
        record = json.loads(SSAFY_DEPLOYMENT.read_text(encoding="utf-8"))

        self.assertEqual(record["chainId"], DEFAULT_CHAIN_ID)
        self.assertEqual(record["contractName"], "GPUJobEscrow")
        self.assertEqual(DEFAULT_ESCROW_ADDRESS.lower(), record["address"].lower())

    def test_a_worker_nobody_configured_uses_the_ssafy_escrow(self):
        with Folder() as folder:
            settings = resolve_settings(env={}, directory=folder)

        self.assertEqual(settings.escrow_address, DEFAULT_ESCROW_ADDRESS)
        self.assertEqual(settings.chain_id, SSAFY_CHAIN_ID)
        self.assertEqual(settings.escrow_rpc_url, DEFAULT_ESCROW_RPC_URL)


class OverridingTheChainTest(unittest.TestCase):
    def test_the_configuration_file_sets_all_three(self):
        with Folder({"escrowRpcUrl": "http://127.0.0.1:8545",
                     "escrowAddress": HARDHAT_ESCROW, "chainId": 31337}) as folder:
            settings = resolve_settings(env={}, directory=folder)

        self.assertEqual(settings.escrow_rpc_url, "http://127.0.0.1:8545")
        self.assertEqual(settings.escrow_address, HARDHAT_ESCROW)
        self.assertEqual(settings.chain_id, 31337)

    def test_the_environment_wins_over_the_file(self):
        with Folder({"escrowRpcUrl": "http://from-the-file:8545",
                     "escrowAddress": "0x" + "11" * 20, "chainId": 1}) as folder:
            settings = resolve_settings(env={
                ENV_ESCROW_RPC_URL: "http://127.0.0.1:8545",
                ENV_ESCROW_ADDRESS: HARDHAT_ESCROW,
                ENV_CHAIN_ID: "31337",
            }, directory=folder)

        self.assertEqual(settings.escrow_rpc_url, "http://127.0.0.1:8545")
        self.assertEqual(settings.escrow_address, HARDHAT_ESCROW)
        self.assertEqual(settings.chain_id, 31337)


class AWrongValueIsRefusedTest(unittest.TestCase):
    def test_a_malformed_contract_address(self):
        with Folder({"escrowAddress": "0x1234"}) as folder:
            with self.assertRaises(WorkerConfigurationError) as refused:
                resolve_settings(env={}, directory=folder)

        self.assertIn("escrowAddress", str(refused.exception))

    def test_a_chain_id_that_is_not_a_number(self):
        with Folder() as folder:
            with self.assertRaises(WorkerConfigurationError):
                resolve_settings(env={ENV_CHAIN_ID: "ssafy"}, directory=folder)

    def test_a_chain_id_in_the_file_must_be_an_integer(self):
        with Folder({"chainId": "31221"}) as folder:
            with self.assertRaises(WorkerConfigurationError):
                resolve_settings(env={}, directory=folder)


if __name__ == "__main__":
    unittest.main()
