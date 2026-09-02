"""Immutable, local allowlist for platform-managed GPU workloads."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .errors import WorkerConfigurationError


@dataclass(frozen=True)
class WorkloadSpec:
    workload: str
    image: str
    timeout_seconds: int
    max_output_bytes: int
    execution_proof: bool = False


GPU_STANDARD_BENCHMARK = "GPU_STANDARD_BENCHMARK"


_GPU_PROBE_IMAGE = (
    "nvidia/cuda@sha256:"
    "6e6bcc811b807465076444f7018330976d56d7a6fc41574b4bc3babf411a7a98"
)

_WORKLOADS = MappingProxyType(
    {
        "GPU_PYTORCH_TEST": WorkloadSpec(
            workload="GPU_PYTORCH_TEST",
            image=(
                "le3560/gpu-pytorch-poc@sha256:"
                "15f1c509660e25ce4a8a86688aabad9dee87372e584efb175329c4922c5e1fa2"
            ),
            timeout_seconds=120,
            max_output_bytes=64 * 1024,
        ),
        "GPU_PYTORCH_TEST_30S": WorkloadSpec(
            workload="GPU_PYTORCH_TEST_30S",
            image=(
                "le3560/gpu-pytorch-poc-30s@sha256:"
                "d7fe2de03ff8532900c600d9620cd7d57fcedbeb6531b3acc2b53a9f09a71a49"
            ),
            timeout_seconds=90,
            max_output_bytes=64 * 1024,
            execution_proof=True,
        ),
    }
)

# Internal system workloads are deliberately kept out of _WORKLOADS. Only the
# Worker startup path can resolve them, so an EXECUTE received from the Backend
# can never turn the benchmark into a user-dispatchable job.
_SYSTEM_WORKLOADS = MappingProxyType(
    {
        GPU_STANDARD_BENCHMARK: WorkloadSpec(
            workload=GPU_STANDARD_BENCHMARK,
            # Benchmark v1 reuses the already-published immutable PyTorch/CUDA
            # runtime. DockerManager supplies a fixed, Worker-owned program and
            # never accepts an image, command, or arguments from the Backend.
            image=(
                "le3560/gpu-pytorch-poc-30s@sha256:"
                "d7fe2de03ff8532900c600d9620cd7d57fcedbeb6531b3acc2b53a9f09a71a49"
            ),
            timeout_seconds=90,
            max_output_bytes=64 * 1024,
        ),
    }
)


def get_workload_spec(workload: str) -> WorkloadSpec:
    try:
        return _WORKLOADS[workload]
    except (KeyError, TypeError) as error:
        raise WorkerConfigurationError(
            f"workload is not allowlisted: {workload!r}"
        ) from error


def get_gpu_probe_image() -> str:
    return _GPU_PROBE_IMAGE


def get_system_workload_spec(workload: str) -> WorkloadSpec:
    try:
        return _SYSTEM_WORKLOADS[workload]
    except (KeyError, TypeError) as error:
        raise WorkerConfigurationError(
            f"system workload is not allowlisted: {workload!r}"
        ) from error


def supported_workloads() -> tuple[str, ...]:
    return tuple(_WORKLOADS)
