"""Approved sentence-transformers embedding runtime."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Sequence

from .errors import CudaUnavailableError, DependencyError, DeviceVerificationError

APPROVED_MODELS = ("BAAI/bge-small-en-v1.5",)


@dataclass(frozen=True)
class EmbeddingRunResult:
    model_name: str
    device: str
    input_count: int
    embedding_shape: tuple[int, ...]
    model_load_ms: float
    runtime_ms: float


def load_torch() -> Any:
    try:
        import torch
    except ImportError as exc:
        raise DependencyError(
            "PyTorch is not installed; install a CUDA-enabled wheel from pytorch.org"
        ) from exc
    return torch


def _default_model_factory(model_name: str, device: str) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise DependencyError("sentence-transformers is not installed") from exc
    return SentenceTransformer(model_name, device=device)


def run_embedding(
    model_name: str,
    inputs: Sequence[str],
    *,
    device: str = "cuda",
    batch_size: int = 64,
    torch_module: Any | None = None,
    model_factory: Callable[[str, str], Any] | None = None,
    clock: Callable[[], float] = perf_counter,
) -> EmbeddingRunResult:
    """Load an approved model and time only its encode operation."""
    if model_name not in APPROVED_MODELS:
        raise ValueError(f"model is not approved: {model_name}")
    if not inputs:
        raise ValueError("at least one input is required")
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    torch_module = torch_module or load_torch()
    if device.startswith("cuda") and not torch_module.cuda.is_available():
        raise CudaUnavailableError(
            "torch.cuda.is_available() is false; CUDA execution is required for STEP 1"
        )

    factory = model_factory or _default_model_factory
    load_started = clock()
    model = factory(model_name, device)
    model_load_ms = (clock() - load_started) * 1000

    actual_device = str(model.device)
    if actual_device != device and not (
        device == "cuda" and actual_device.startswith("cuda:")
    ):
        raise DeviceVerificationError(
            f"model device mismatch: requested={device}, actual={actual_device}"
        )

    if device.startswith("cuda"):
        torch_module.cuda.synchronize()
    execution_started = clock()
    embeddings = model.encode(
        list(inputs),
        batch_size=batch_size,
        device=device,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    if device.startswith("cuda"):
        torch_module.cuda.synchronize()
    runtime_ms = (clock() - execution_started) * 1000

    shape = tuple(int(dimension) for dimension in embeddings.shape)
    if not shape or shape[0] != len(inputs):
        raise RuntimeError(
            f"unexpected embedding shape {shape} for {len(inputs)} inputs"
        )

    return EmbeddingRunResult(
        model_name=model_name,
        device=actual_device,
        input_count=len(inputs),
        embedding_shape=shape,
        model_load_ms=model_load_ms,
        runtime_ms=runtime_ms,
    )

