"""Tests for Setup-owned LocalAppData paths."""

from __future__ import annotations

from pathlib import Path
import unittest

from gpu_worker_setup.layout import InstallLayout


class InstallLayoutTests(unittest.TestCase):
    def test_for_local_app_data_keeps_setup_state_and_logs_outside_worker(self) -> None:
        root = Path("C:/Users/tester/AppData/Local/GPUCompute")

        layout = InstallLayout.for_local_app_data(root)

        self.assertEqual(layout.worker_dir, root / "Worker")
        self.assertEqual(layout.stable_setup_exe, root / "Setup" / "GPUWorkerSetup.exe")
        self.assertEqual(layout.state_dir, root / "state")
        self.assertEqual(layout.logs_dir, root / "logs")
        self.assertEqual(layout.setup_state_path, root / "state" / "setup-state.json")
        self.assertEqual(layout.update_journal_path, root / "state" / "update-transaction.json")
        self.assertEqual(layout.setup_lock_path, root / "state" / "setup.lock")
        self.assertEqual(layout.setup_log_path, root / "logs" / "setup.log")


if __name__ == "__main__":
    unittest.main()
