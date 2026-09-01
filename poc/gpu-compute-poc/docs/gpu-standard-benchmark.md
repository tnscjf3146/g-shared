# GPU standard benchmark registration

## Scope

The Worker measures a standard CUDA score before its first Backend REGISTER.
This supplies a numeric feature for a future scheduler; it does not implement
XGBoost, ranking, runtime prediction, attestation, or tamper resistance.

Startup flow:

```text
worker-config load
  -> NVML GPU inspection
  -> Docker/Linux/container CUDA validation
  -> benchmark-result.json validation
     -> cache hit: reuse result
     -> cache miss: run GPU_STANDARD_BENCHMARK once and save result
  -> open WebSocket
  -> REGISTER with benchmark
  -> heartbeat / IDLE
```

The WebSocket is not opened until the benchmark succeeds, so a benchmarking
Worker cannot receive a normal Job. Failures such as `BENCHMARK_FAILED`,
`BENCHMARK_CUDA_UNAVAILABLE`, and `BENCHMARK_TIMEOUT` terminate startup without
REGISTER and without an infinite retry loop.

## Benchmark v1 contract

| Field | Value |
|---|---|
| version | `gpu-benchmark-v1` |
| operation | PyTorch CUDA matrix multiplication |
| dtype | FP16 |
| matrix size | 4096 x 4096 |
| warm-up | approximately 3 seconds |
| measurement | approximately 20 seconds |
| execution timeout | 90 seconds |
| image pull timeout | 1800 seconds |
| score | measured TFLOPS |

Every measured multiplication uses the same `2 * 4096^3` FLOP estimate. The
Worker computes:

```text
throughputTflops = (2 * 4096^3 * iterations) / elapsedSeconds / 1e12
benchmarkScore = throughputTflops
```

CUDA is synchronized during warm-up and after every measured multiplication.
The workload does not sleep and does not resize the matrix according to VRAM.

The internal catalog reuses the already-published immutable
`le3560/gpu-pytorch-poc-30s@sha256:d7fe...a71a49` PyTorch/CUDA runtime and
overrides its entrypoint with one fixed Worker-owned program. The Backend
cannot supply or change the image, entrypoint, command, arguments, environment,
or mounts. A dedicated image Dockerfile using the same program is kept under
`runtime-images/gpu-standard-benchmark` for a future publication.

The container keeps the existing hardening: `--network none`, `--read-only`,
`--cap-drop ALL`, `no-new-privileges`, PID/memory limits, a small hardened tmpfs,
and cleanup after success, failure, cancellation, or timeout.

## Cache

`benchmark-result.json` is next to `gpu-worker-agent.exe` in a PyInstaller
onedir distribution. In normal source execution it is in the Worker project
directory, matching `worker-config.json` lookup behavior.

Example:

```json
{
  "benchmarkVersion": "gpu-benchmark-v1",
  "gpuUuid": "GPU-...",
  "gpuModel": "NVIDIA GeForce RTX 4070 Laptop GPU",
  "vramMb": 8188,
  "matrixSize": 4096,
  "precision": "FP16",
  "warmupSeconds": 3,
  "measurementSeconds": 20,
  "iterations": 123,
  "elapsedMs": 20031,
  "throughputTflops": 12.34,
  "benchmarkScore": 12.34,
  "measuredAt": "2026-09-01T00:00:00Z"
}
```

The cache is reused only when GPU UUID and benchmark version both match and all
fields pass validation. Missing JSON, corrupt JSON, a changed UUID/version,
missing fields, wrong fixed parameters, non-positive numbers, NaN, or infinity
causes a cache miss. Saving uses a temporary file followed by an atomic replace.

## REGISTER and Worker API

REGISTER adds one backward-compatible nested object:

```json
{
  "type": "REGISTER",
  "workerId": "worker-001",
  "gpuModel": "NVIDIA GeForce RTX 4070 Laptop GPU",
  "totalVramMb": 8188,
  "freeVramMb": 7800,
  "gpuUtilization": 0,
  "cudaAvailable": true,
  "supportedWorkloads": ["GPU_PYTORCH_TEST", "GPU_PYTORCH_TEST_30S"],
  "hourlyPrice": 600,
  "workerStatus": "IDLE",
  "benchmark": {
    "version": "gpu-benchmark-v1",
    "score": 12.34,
    "throughputTflops": 12.34,
    "matrixSize": 4096,
    "precision": "FP16",
    "measuredAt": "2026-09-01T00:00:00Z"
  }
}
```

`GET /api/workers` and `GET /api/workers/{workerId}` return the same benchmark
shape. The Registry stores Java numeric types and `Instant`, not strings.
Legacy REGISTER messages without benchmark are accepted and exposed as
`benchmark: null`; the current Worker never sends such a registration.

These values are supplier-reported. Schema validation prevents malformed
values but cannot prove that an altered Worker performed the measurement.

## RTX 4050 Laptop validation (PowerShell)

Use the directory containing `gpu-worker-agent.exe`:

```powershell
Set-Location 'C:\path\to\gpu-worker-agent'
Remove-Item -LiteralPath '.\benchmark-result.json' -ErrorAction SilentlyContinue
Get-Content '.\worker-config.json'
```

Start `nvidia-smi -l 1` in a second terminal, then run the Worker:

```powershell
nvidia-smi -l 1
```

```powershell
.\gpu-worker-agent.exe
```

Confirm the logs show cache miss, `running gpu-benchmark-v1`, completion, cache
save, and only then REGISTER/IDLE. Confirm sustained GPU activity for roughly
20 seconds, then inspect the cache and Backend:

```powershell
Get-Content '.\benchmark-result.json' | ConvertFrom-Json | Format-List
Invoke-RestMethod 'http://127.0.0.1:8080/api/workers/worker-4050' |
  ConvertTo-Json -Depth 5
docker ps -a --filter 'name=gpu-poc-benchmark-0'
```

The final Docker command should show no retained benchmark container. For the
second run, leave the cache in place, stop and restart the Worker, and confirm
`[benchmark] cache hit score=...` appears with no `running` line. The Backend
must receive the identical score and the Worker must reach IDLE.

## RTX 4070 Laptop validation (PowerShell)

Repeat the RTX 4050 procedure using the RTX 4070 Worker ID:

```powershell
Set-Location 'C:\path\to\gpu-worker-agent'
Remove-Item -LiteralPath '.\benchmark-result.json' -ErrorAction SilentlyContinue
.\gpu-worker-agent.exe
Get-Content '.\benchmark-result.json' | ConvertFrom-Json | Format-List
Invoke-RestMethod 'http://127.0.0.1:8080/api/workers/worker-4070' |
  ConvertTo-Json -Depth 5
```

Observe GPU utilization separately with `nvidia-smi -l 1`. On the second run,
keep `benchmark-result.json`; confirm a cache-hit log, no benchmark-run log, the
same score in the API, and final IDLE state. Both GPU models must report
`matrixSize=4096`, `precision=FP16`, `warmupSeconds=3`, and
`measurementSeconds=20`; only measured iterations/throughput should differ.

## Future scheduler features

GPU features:

- `benchmarkScore`
- `vramGb`

Job features:

- `totalTokens`
- `sampleCount`
- `avgSequenceLength`
- `maxSequenceLength`
- `epochs`
- `batchSize`
- `baseModel` / `modelSize`
- `workloadType`

Target:

- `runtimeSec`
