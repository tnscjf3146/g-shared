"""Fixed CUDA program executed inside the immutable benchmark runtime image."""

from __future__ import annotations


# This program is Worker-owned and constant. It is passed to the pinned runtime
# with ``python -c`` because the PoC does not yet publish a separate benchmark
# image. No part of it is derived from a Backend message or supplier input.
BENCHMARK_PROGRAM = r'''import json
import math
import time

BENCHMARK_VERSION = "gpu-benchmark-v1"
MATRIX_SIZE = 4096
PRECISION = "FP16"
WARMUP_SECONDS = 3
MEASUREMENT_SECONDS = 20
RESULT_MARKER = "GPU_BENCHMARK_RESULT="


class BenchmarkProgramError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def run_benchmark(torch_module, clock=time.perf_counter):
    if not bool(torch_module.cuda.is_available()):
        raise BenchmarkProgramError(
            "BENCHMARK_CUDA_UNAVAILABLE",
            "torch.cuda.is_available() returned false inside the container",
        )

    gpu_model = str(torch_module.cuda.get_device_name(0))
    vram_mb = round(torch_module.cuda.get_device_properties(0).total_memory / (1024 * 1024))
    left = torch_module.ones(
        (MATRIX_SIZE, MATRIX_SIZE), device="cuda", dtype=torch_module.float16
    )
    right = torch_module.ones(
        (MATRIX_SIZE, MATRIX_SIZE), device="cuda", dtype=torch_module.float16
    )

    warmup_started = clock()
    result = None
    while clock() - warmup_started < WARMUP_SECONDS:
        result = left @ right
        torch_module.cuda.synchronize()

    torch_module.cuda.synchronize()
    started = clock()
    iterations = 0
    finished = started
    while finished - started < MEASUREMENT_SECONDS:
        result = left @ right
        torch_module.cuda.synchronize()
        iterations += 1
        finished = clock()

    elapsed_seconds = finished - started
    elapsed_ms = max(1, round(elapsed_seconds * 1000))
    if iterations <= 0 or elapsed_seconds <= 0 or result is None:
        raise BenchmarkProgramError("BENCHMARK_FAILED", "CUDA matrix multiplication did not run")
    sample = float(result[0, 0].item())
    if not math.isfinite(sample) or sample != float(MATRIX_SIZE):
        raise BenchmarkProgramError("BENCHMARK_FAILED", "CUDA matrix result verification failed")

    throughput = (2 * MATRIX_SIZE**3 * iterations) / elapsed_seconds / 1e12
    if not math.isfinite(throughput) or throughput <= 0:
        raise BenchmarkProgramError("BENCHMARK_FAILED", "invalid benchmark throughput")
    throughput = round(throughput, 6)
    return {
        "benchmarkVersion": BENCHMARK_VERSION,
        "matrixSize": MATRIX_SIZE,
        "precision": PRECISION,
        "warmupSeconds": WARMUP_SECONDS,
        "measurementSeconds": MEASUREMENT_SECONDS,
        "iterations": iterations,
        "elapsedMs": elapsed_ms,
        "throughputTflops": throughput,
        "benchmarkScore": throughput,
        "gpuModel": gpu_model,
        "vramMb": vram_mb,
    }


def main():
    try:
        import torch
        payload = run_benchmark(torch)
        exit_code = 0
    except BenchmarkProgramError as error:
        payload = {
            "benchmarkVersion": BENCHMARK_VERSION,
            "status": "FAILED",
            "errorCode": error.code,
            "errorMessage": str(error),
        }
        exit_code = 20
    except Exception:
        payload = {
            "benchmarkVersion": BENCHMARK_VERSION,
            "status": "FAILED",
            "errorCode": "BENCHMARK_FAILED",
            "errorMessage": "unexpected CUDA benchmark failure",
        }
        exit_code = 21
    print(RESULT_MARKER + json.dumps(payload, separators=(",", ":"), allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
'''


def main() -> int:
    namespace = {"__name__": "__main__"}
    try:
        exec(compile(BENCHMARK_PROGRAM, "<gpu-standard-benchmark>", "exec"), namespace)
    except SystemExit as error:
        return int(error.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
