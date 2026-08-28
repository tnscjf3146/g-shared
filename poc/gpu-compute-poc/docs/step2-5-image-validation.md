# STEP 2.5 GPU Runtime Image Validation

- Date: 2026-08-27
- Host GPU: NVIDIA GeForce RTX 4070 Laptop GPU
- Host VRAM: 8188 MiB
- NVIDIA Driver: 596.36
- Docker Desktop: 4.84.0
- Docker Engine: 29.6.2, linux/amd64
- Base image: `pytorch/pytorch:2.12.1-cuda12.6-cudnn9-runtime`
- Base digest: `sha256:79c5599719e0b1afdb56ac2d14588b530283752d7ae6ec3c36e18ec9deb8b229`
- Local image: `gpu-pytorch-poc:0.1-local`
- Local manifest list: `sha256:ffa5965693467bca2cd4ead6eb92e5014d9ed515144850dfada482ca73523b3a`
- Entrypoint: `python /opt/gpu-task/gpu_task.py`
- Container user: `10001:10001`
- Image size: 3,872,580,558 bytes
- Docker Hub tag: `le3560/gpu-pytorch-poc:0.1`
- Remote OCI index digest: `sha256:ffa5965693467bca2cd4ead6eb92e5014d9ed515144850dfada482ca73523b3a`
- Pinned linux/amd64 workload manifest: `le3560/gpu-pytorch-poc@sha256:15f1c509660e25ce4a8a86688aabad9dee87372e584efb175329c4922c5e1fa2`
- Pinned linux/amd64 GPU probe: `nvidia/cuda@sha256:6e6bcc811b807465076444f7018330976d56d7a6fc41574b4bc3babf411a7a98`

## CUDA execution

- `torch.cuda.is_available()`: true
- GPU model reported inside container: NVIDIA GeForce RTX 4070 Laptop GPU
- Matrix size: 8192 x 8192 FP32
- Iterations: 8
- CUDA runtime: 775 ms
- Result checksum: `b05651beb760e6a8dd52f8b501ef7028c273bae8a593ff956f23c1ea7c2fc14c`
- Container exit code: 0
- Peak GPU utilization: 100%
- Peak observed GPU memory: 1199 MiB

The workload created CUDA tensors, performed eight matrix multiplications,
called `torch.cuda.synchronize()` around timing, verified three known result
samples, and generated a SHA-256 checksum. GPU utilization alone was not used
as the success condition.

Windows WDDM returned `[Unknown Error]` for two transient `nvidia-smi` fields.
Numeric samples remained available and recorded the peaks above. Future
monitoring parses only samples whose utilization and memory fields are numeric.

## Docker Scout known issue

Docker Scout 1.23.1 was available, but local image indexing did not complete in a
reasonable time and was cancelled. No SARIF vulnerability report or SPDX SBOM
was produced. Per the approved STEP 2.5 scope, this optional tooling issue did
not block the immutable-image CUDA execution validation.
