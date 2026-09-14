"""What a packaged G-shared.exe needs from the source, checked without building.

Three things break only once the Worker is frozen, and each one fails on a
supplier's PC rather than a developer's:

* **Where files are written.** Beside the executable is ``Program Files`` for
  an installed copy, and a log that cannot be opened there is a crash before
  anything is said. A packaged Worker writes to ``%LOCALAPPDATA%\\G-shared``
  unless it is a portable copy with its settings already beside it.
* **Where the escrow ABI is read from.** In the repository it is found by
  walking up from the source file; in a bundle there is nothing to walk up to.
* **What goes into the bundle.** The spec and the build requirements must
  carry the ABI and nothing secret, and must keep the training stack out.

``packaging/build.ps1`` then proves the same things against the real
executable with ``selfcheck.exe``.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gshared_worker import config
from gshared_worker.config import (
    CONFIG_FILE_NAME,
    USER_DATA_DIRECTORY_NAME,
    config_file_path,
    data_directory,
    worker_directory,
)
from gshared_worker.escrow_abi import ARTIFACT_RELATIVE_PATH, default_artifact_path
from gshared_worker.logging_setup import DEBUG_LOG_NAME, start_debug_log, stop_debug_log
from gshared_worker.worker_wallet import WALLET_FILE_NAME, wallet_file_path

PACKAGING = Path(__file__).resolve().parent.parent / "packaging"


def frozen_at(executable: Path):
    """Pretend to be G-shared.exe at this path."""
    return (patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(executable)))


class TheDataDirectoryTest(unittest.TestCase):
    def setUp(self):
        self._temporary = TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.program = self.root / "Program Files" / "G-shared"
        self.program.mkdir(parents=True)
        self.local = self.root / "AppData" / "Local"
        self.addCleanup(self._temporary.cleanup)

    def test_run_from_source_it_is_the_worker_directory_as_before(self):
        self.assertEqual(data_directory(env={"LOCALAPPDATA": str(self.local)}),
                         worker_directory())

    def test_packaged_it_is_per_user_rather_than_beside_the_program(self):
        frozen, executable = frozen_at(self.program / "G-shared.exe")
        with frozen, executable:
            where = data_directory(env={"LOCALAPPDATA": str(self.local)})

        self.assertEqual(where, self.local / USER_DATA_DIRECTORY_NAME)

    def test_a_portable_copy_keeps_the_settings_beside_it(self):
        # Moving these would start a second identity and abandon the sealed
        # token and signing key the supplier already has.
        (self.program / CONFIG_FILE_NAME).write_text("{}", encoding="utf-8")
        frozen, executable = frozen_at(self.program / "G-shared.exe")
        with frozen, executable:
            where = data_directory(env={"LOCALAPPDATA": str(self.local)})

        self.assertEqual(where, self.program)

    def test_with_no_profile_folder_it_falls_back_to_the_program(self):
        frozen, executable = frozen_at(self.program / "G-shared.exe")
        with frozen, executable:
            self.assertEqual(data_directory(env={}), self.program)

    def test_settings_and_wallet_follow_it_together(self):
        with patch.object(config, "data_directory", lambda: self.local / "G-shared"):
            self.assertEqual(config_file_path(),
                             self.local / "G-shared" / CONFIG_FILE_NAME)
            self.assertEqual(wallet_file_path(),
                             self.local / "G-shared" / WALLET_FILE_NAME)

    def test_the_log_creates_a_folder_that_does_not_exist_yet(self):
        fresh = self.local / "G-shared"
        self.assertFalse(fresh.exists())
        try:
            path = start_debug_log(fresh)
        finally:
            stop_debug_log()

        self.assertEqual(path, fresh / DEBUG_LOG_NAME)
        self.assertTrue(path.is_file())


class TheBundledAbiTest(unittest.TestCase):
    def test_a_packaged_worker_reads_the_abi_inside_the_bundle(self):
        with TemporaryDirectory() as bundle, \
                patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "_MEIPASS", bundle, create=True):
            self.assertEqual(default_artifact_path(),
                             Path(bundle) / ARTIFACT_RELATIVE_PATH)

    def test_run_from_source_it_still_finds_the_repository_copy(self):
        path = default_artifact_path()

        self.assertTrue(path.is_file(), path)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["contractName"],
                         "GPUJobEscrow")


class TheBuildRecipeTest(unittest.TestCase):
    def setUp(self):
        self.spec = (PACKAGING / "G-shared.spec").read_text(encoding="utf-8")
        self.requirements = (PACKAGING / "requirements-build.txt").read_text(
            encoding="utf-8")

    def test_the_only_data_file_is_the_escrow_abi(self):
        self.assertIn('DATAS = [(str(ABI), "blockchain/abi")]', self.spec)
        self.assertEqual(self.spec.count("DATAS = "), 1)
        # Beside it only package metadata, and only for the runtime
        # dependencies -- names and versions, no code, nothing secret.
        self.assertIn("datas=DATAS + METADATA + list(extra)", self.spec)
        self.assertIn('RUNTIME_DISTRIBUTIONS = ["websockets", "web3", "eth-account"]',
                      self.spec)
        self.assertIn("copy_metadata(_dist, recursive=True)", self.spec)

    def test_every_worker_module_is_bundled(self):
        self.assertIn('collect_submodules("gshared_worker")', self.spec)
        self.assertIn("HIDDEN = WORKER_MODULES + [", self.spec)

    def test_the_entry_point_is_the_workers_own(self):
        self.assertIn('WORKER / "gshared_worker" / "__main__.py"', self.spec)
        self.assertIn('onefile(worker, "G-shared")', self.spec)

    def test_the_training_stack_and_test_tools_are_kept_out(self):
        for name in ("torch", "transformers", "xgboost", "pandas", "numpy",
                     "pytest", "tests"):
            self.assertIn(f'"{name}"', self.spec, name)

    def test_nothing_secret_is_named_as_bundle_content(self):
        # Quoted file names, so "os.environ" is not mistaken for ".env".
        for secret in ('".env"', '"worker-config.json"', '"worker-wallet.json"',
                       "privateKey", "workerToken"):
            for line in self.spec.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                self.assertNotIn(secret, line, line)

    def test_the_build_installs_only_runtime_dependencies(self):
        packages = [line.split("==")[0].strip().lower()
                    for line in self.requirements.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(sorted(packages),
                         ["eth-account", "pyinstaller", "web3", "websockets"])


if __name__ == "__main__":
    unittest.main()
