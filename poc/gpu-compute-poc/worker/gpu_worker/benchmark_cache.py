"""Defensive, executable-directory cache for the startup GPU benchmark."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .agent_config import agent_directory
from .benchmark_models import BENCHMARK_VERSION, BenchmarkResult
from .errors import BenchmarkError

BENCHMARK_FILE_NAME = "benchmark-result.json"


def benchmark_file_path(directory: Path | None = None) -> Path:
    return (directory or agent_directory()) / BENCHMARK_FILE_NAME


class BenchmarkCache:
    def __init__(
        self,
        directory: Path | None = None,
        *,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.path = benchmark_file_path(directory)
        self._logger = logger

    def load(
        self,
        *,
        gpu_uuid: str,
        benchmark_version: str = BENCHMARK_VERSION,
    ) -> BenchmarkResult | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("cache root must be a JSON object")
            result = BenchmarkResult.from_mapping(payload)
            if result.gpu_uuid != gpu_uuid:
                return None
            if result.benchmark_version != benchmark_version:
                return None
            return result
        except (OSError, ValueError, json.JSONDecodeError, BenchmarkError) as error:
            self._logger(f"[benchmark] ignoring invalid cache: {error}")
            return None

    def save(self, result: BenchmarkResult) -> None:
        result.validate()
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(
                    result.to_cache_payload(),
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                ) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except (OSError, ValueError) as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise BenchmarkError(
                "BENCHMARK_CACHE_WRITE_FAILED",
                f"could not save {BENCHMARK_FILE_NAME}: {error}",
            ) from error
