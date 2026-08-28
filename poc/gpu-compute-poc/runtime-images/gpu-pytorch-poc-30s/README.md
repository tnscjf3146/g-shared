# Timed GPU PyTorch PoC Runtime Image

Platform-managed image for the fixed `GPU_PYTORCH_TEST_30S` workload. It uses
the same digest-pinned PyTorch 2.12.1 / CUDA 12.6 base as the short workload,
but performs synchronized CUDA matrix multiplications until 30 seconds of
measured compute time have elapsed. It never sleeps.

The program requires the Worker-generated `GPU_POC_JOB_ID` environment value
and returns that exact ID with `iterations`, `matrixSize`,
`targetDurationSeconds`, `runtimeMs`, and a checksum derived from CUDA result
samples. The Worker rejects a result whose job ID does not match its active job.

Published immutable image:

- Tag: `le3560/gpu-pytorch-poc-30s:0.1`
- Digest: `sha256:d7fe2de03ff8532900c600d9620cd7d57fcedbeb6531b3acc2b53a9f09a71a49`

GPU suppliers do not build, pull, or run this image manually. The Worker owns
the fixed digest allowlist and all Docker lifecycle operations.
