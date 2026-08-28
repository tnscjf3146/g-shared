"""Command-line entry point for the standalone STEP 1 Worker."""

from __future__ import annotations

import argparse
import platform
import sys
from collections.abc import Sequence

from .embedding import APPROVED_MODELS, load_torch, run_embedding
from .errors import WorkerError
from .gpu import GpuUtilizationSampler, NvmlGpuProbe

DEFAULT_INPUTS = (
    "hello world",
    "distributed gpu computing",
    "scheduler test",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an approved sentence-transformers embedding workload on CUDA."
    )
    parser.add_argument("--model", choices=APPROVED_MODELS, default=APPROVED_MODELS[0])
    parser.add_argument("--gpu-index", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument(
        "--repeat",
        type=int,
        default=256,
        help="Repeat the three public test strings so GPU utilization is observable.",
    )
    parser.add_argument(
        "--allow-cpu",
        action="store_true",
        help="Development-only fallback. It never counts as STEP 1 success.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if sys.version_info < (3, 11):
        print(
            f"Worker startup failed: Python 3.11+ required, found {platform.python_version()}",
            file=sys.stderr,
        )
        return 2
    if args.repeat <= 0:
        print("Worker startup failed: --repeat must be greater than zero", file=sys.stderr)
        return 2

    print("Worker startup")
    print(f"Python: {platform.python_version()}")

    try:
        torch_module = load_torch()
        cuda_available = bool(torch_module.cuda.is_available())
        if not cuda_available and not args.allow_cpu:
            raise WorkerError(
                "CUDA unavailable: torch.cuda.is_available() is false; CPU fallback is disabled"
            )

        device = "cuda" if cuda_available else "cpu"
        inputs = DEFAULT_INPUTS * args.repeat

        if cuda_available:
            with NvmlGpuProbe(index=args.gpu_index) as probe:
                before = probe.snapshot()
                print("\nGPU:")
                print(before.name)
                print("\nVRAM:")
                print(f"Total: {before.total_vram_mb} MB")
                print(f"Free: {before.free_vram_mb} MB")
                print(f"Utilization: {before.utilization_percent}%")
                print("\nCUDA:")
                print("AVAILABLE")
                print(f"PyTorch build CUDA: {torch_module.version.cuda}")
                print(f"\nModel:\n{args.model}")
                print("\nEmbedding test started")
                print("\nDevice:")
                print("device=cuda")

                sampler = GpuUtilizationSampler(probe)
                sampler.start()
                try:
                    result = run_embedding(
                        args.model,
                        inputs,
                        device=device,
                        batch_size=args.batch_size,
                        torch_module=torch_module,
                    )
                finally:
                    peak_utilization = sampler.stop()
                after = probe.snapshot()

                print(f"\nModel load: {result.model_load_ms:.2f} ms")
                print(f"Execution: {result.runtime_ms:.2f} ms")
                print(f"Inputs: {result.input_count}")
                print(f"Embedding shape: {result.embedding_shape}")
                print(f"Peak GPU utilization: {peak_utilization}%")
                print(f"Free VRAM after: {after.free_vram_mb} MB")
                print("\nEmbedding test completed")
                print("POC SUCCESS: YES (confirm utilization rise in external nvidia-smi)")
                return 0

        print("\nGPU:\nNONE")
        print("\nCUDA:\nUNAVAILABLE")
        print(f"\nModel:\n{args.model}")
        print("\nEmbedding test started")
        print("\nDevice:\ndevice=cpu (development fallback)")
        result = run_embedding(
            args.model,
            inputs,
            device="cpu",
            batch_size=args.batch_size,
            torch_module=torch_module,
        )
        print(f"\nModel load: {result.model_load_ms:.2f} ms")
        print(f"Execution: {result.runtime_ms:.2f} ms")
        print(f"Embedding shape: {result.embedding_shape}")
        print("\nEmbedding test completed")
        print("POC SUCCESS: NO (CPU fallback is not accepted)")
        return 3
    except (WorkerError, ValueError, RuntimeError) as exc:
        print(f"Worker failed: {exc}", file=sys.stderr)
        print("POC SUCCESS: NO", file=sys.stderr)
        return 2

