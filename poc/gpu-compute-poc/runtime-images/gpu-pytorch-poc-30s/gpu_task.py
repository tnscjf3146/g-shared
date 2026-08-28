"""Timed PyTorch CUDA workload executed by the STEP 2.5 proof container."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping


RESULT_MARKER = "GPU_TASK_RESULT="
SCHEMA_VERSION = 1
TARGET_DURATION_SECONDS = 30
MIN_MATRIX_SIZE = 2048
MAX_MATRIX_SIZE = 8192
MATRIX_ALIGNMENT = 256
VRAM_BUDGET_RATIO = 0.20
FLOAT32_BYTES = 4
MATRIX_COUNT = 3


class GpuTaskError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class TaskResult:
    job_id: str
    gpu_model: str
    runtime_ms: int
    result_checksum: str
    matrix_size: int
    iterations: int
    target_duration_seconds: int

    def to_payload(self) -> dict[str, object]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "status": "SUCCESS",
            "cudaAvailable": True,
            "jobId": self.job_id,
            "gpuModel": self.gpu_model,
            "runtimeMs": self.runtime_ms,
            "resultChecksum": self.result_checksum,
            "matrixSize": self.matrix_size,
            "iterations": self.iterations,
            "targetDurationSeconds": self.target_duration_seconds,
        }


def select_matrix_size(free_vram_bytes: int) -> int:
    if free_vram_bytes <= 0:
        raise GpuTaskError("INSUFFICIENT_VRAM", "free CUDA memory is unavailable")

    budget = int(free_vram_bytes * VRAM_BUDGET_RATIO)
    maximum = math.isqrt(budget // (MATRIX_COUNT * FLOAT32_BYTES))
    aligned = (maximum // MATRIX_ALIGNMENT) * MATRIX_ALIGNMENT
    matrix_size = min(MAX_MATRIX_SIZE, aligned)
    if matrix_size < MIN_MATRIX_SIZE:
        raise GpuTaskError(
            "INSUFFICIENT_VRAM",
            "not enough free CUDA memory for the minimum matrix workload",
        )
    return matrix_size


def run_cuda_task(
    torch_module: Any,
    *,
    job_id: str,
    target_duration_seconds: int = TARGET_DURATION_SECONDS,
    clock: Callable[[], float] = time.perf_counter,
) -> TaskResult:
    if not job_id.strip():
        raise GpuTaskError("JOB_ID_MISSING", "GPU_POC_JOB_ID is required")
    if target_duration_seconds <= 0:
        raise GpuTaskError("CUDA_TASK_FAILED", "target duration must be positive")
    if not bool(torch_module.cuda.is_available()):
        raise GpuTaskError(
            "CUDA_UNAVAILABLE",
            "torch.cuda.is_available() returned false inside the container",
        )

    free_vram_bytes, _ = torch_module.cuda.mem_get_info()
    matrix_size = select_matrix_size(int(free_vram_bytes))
    gpu_model = str(torch_module.cuda.get_device_name(0))

    left = torch_module.ones(
        (matrix_size, matrix_size), device="cuda", dtype=torch_module.float32
    )
    right = torch_module.ones(
        (matrix_size, matrix_size), device="cuda", dtype=torch_module.float32
    )

    # Warm-up initializes CUDA kernels before the measured interval.
    result = left @ right
    torch_module.cuda.synchronize()

    started = clock()
    iterations = 0
    finished = clock()
    while finished - started < target_duration_seconds:
        result = left @ right
        torch_module.cuda.synchronize()
        iterations += 1
        finished = clock()
    runtime_ms = max(1, round((finished - started) * 1000))

    if iterations <= 0:
        raise GpuTaskError("CUDA_TASK_FAILED", "matrix multiplication did not run")
    sample_values = [
        float(result[0, 0].item()),
        float(result[matrix_size // 2, matrix_size // 2].item()),
        float(result[-1, -1].item()),
    ]
    expected = float(matrix_size)
    if not all(math.isfinite(value) and value == expected for value in sample_values):
        raise GpuTaskError("CUDA_TASK_FAILED", "matrix multiplication verification failed")

    checksum_source = json.dumps(
        {
            "gpuModel": gpu_model,
            "iterations": iterations,
            "jobId": job_id,
            "matrixSize": matrix_size,
            "samples": sample_values,
            "targetDurationSeconds": target_duration_seconds,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checksum = hashlib.sha256(checksum_source).hexdigest()
    return TaskResult(
        job_id,
        gpu_model,
        runtime_ms,
        checksum,
        matrix_size,
        iterations,
        target_duration_seconds,
    )


def _failure_payload(error: GpuTaskError) -> dict[str, object]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "FAILED",
        "errorCode": error.error_code,
        "errorMessage": str(error),
    }


def main(
    *,
    torch_module: Any | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    try:
        environment = os.environ if environ is None else environ
        job_id = environment.get("GPU_POC_JOB_ID", "")
        if not job_id.strip():
            raise GpuTaskError("JOB_ID_MISSING", "GPU_POC_JOB_ID is required")
        if torch_module is None:
            import torch as torch_module
        payload = run_cuda_task(torch_module, job_id=job_id)
    except GpuTaskError as error:
        print(RESULT_MARKER + json.dumps(_failure_payload(error), separators=(",", ":")))
        return 20
    except Exception:
        error = GpuTaskError("CUDA_TASK_FAILED", "unexpected CUDA task failure")
        print(RESULT_MARKER + json.dumps(_failure_payload(error), separators=(",", ":")))
        return 21

    print(RESULT_MARKER + json.dumps(payload.to_payload(), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
