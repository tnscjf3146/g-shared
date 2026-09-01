# GPU Standard Benchmark Runtime

Source for the internal `GPU_STANDARD_BENCHMARK` workload. Benchmark v1 runs
fixed 4096 x 4096 FP16 PyTorch CUDA matrix multiplications, with a 3-second
warm-up and a 20-second measured interval. It never sleeps.

Build from the repository root so the Dockerfile and Worker use the same fixed
program:

```powershell
docker build -f runtime-images/gpu-standard-benchmark/Dockerfile -t le3560/gpu-standard-benchmark:0.1 .
```

Until this dedicated image is published, the Worker executes the exact same
fixed program with `python -c` in the existing digest-pinned PyTorch runtime.
The Backend cannot provide or override the image, entrypoint, command, args, or
mounts.
