"""Paths owned by the current-user GPU Worker Setup installation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InstallLayout:
    """The fixed LocalAppData layout kept outside the installed Worker payload."""

    root: Path
    worker_dir: Path
    stable_setup_exe: Path
    state_dir: Path
    logs_dir: Path

    @classmethod
    def for_local_app_data(cls, root: str | Path) -> "InstallLayout":
        root_path = Path(root)
        return cls(
            root=root_path,
            worker_dir=root_path / "Worker",
            stable_setup_exe=root_path / "Setup" / "GPUWorkerSetup.exe",
            state_dir=root_path / "state",
            logs_dir=root_path / "logs",
        )

    @property
    def setup_state_path(self) -> Path:
        return self.state_dir / "setup-state.json"

    @property
    def update_journal_path(self) -> Path:
        return self.state_dir / "update-transaction.json"

    @property
    def setup_lock_path(self) -> Path:
        return self.state_dir / "setup.lock"

    @property
    def setup_log_path(self) -> Path:
        return self.logs_dir / "setup.log"
