# STEP 2.5 Remote GPU Execution Validation

Validation dates: 2026-08-27 same-machine, 2026-08-28 cross-network (Asia/Seoul)

Status: **PASS**

## Implemented path

The Backend sends only `jobId` and `workload`. The Worker resolves
`GPU_PYTORCH_TEST` through its immutable in-process catalog, checks the exact
Docker digest, pulls on a cache miss, starts a hardened `--gpus all` container,
validates the structured CUDA result and returns `EXECUTE_RESULT`.

No Backend field can provide an image, command, arguments or mount. The
container has no network, a read-only root filesystem, all capabilities dropped,
no-new-privileges, PID/memory limits and a bounded tmpfs.

## Same-machine real RTX 4070 E2E evidence

- Worker forms: Python source process and self-contained Windows `.exe`
- GPU / VRAM: NVIDIA GeForce RTX 4070 Laptop GPU / 8188 MiB
- Driver: 596.36
- Runtime image: `le3560/gpu-pytorch-poc@sha256:15f1c509660e25ce4a8a86688aabad9dee87372e584efb175329c4922c5e1fa2`
- Container PyTorch / CUDA: 2.12.1 / 12.6
- Matrix: 8192 x 8192 FP32, 8 matrix multiplications
- Checksum: `b05651beb760e6a8dd52f8b501ef7028c273bae8a593ff956f23c1ea7c2fc14c`
- Exit code: 0
- Direct-container peak: 100% GPU, 1199 MiB
- Worker termination: `connectionStatus=OFFLINE`, last `workerStatus=IDLE`

First job: `737ec616-4c5c-47b4-8c43-2a333ab83f3b`, SUCCESS, 772 ms,
`imageCacheHit=false`.

Second job without restarting: `f5e706a9-2ea6-4080-b30c-92601af32559`,
SUCCESS, 793 ms, `imageCacheHit=true`, same checksum.

Authenticated self-contained `.exe`: unauthenticated job POST returned 401;
authenticated job `5a90a9b5-16ef-4b79-8137-c702ffda0997` returned SUCCESS,
794 ms, cache hit, same checksum.

## Cross-network two-PC evidence (2026-08-28)

Executed on two PCs on different networks. The Backend was published through a
Cloudflare WSS tunnel; the Worker reached it only outbound.

- Network separation: Backend and GPU Worker on different networks
- Transport: Cloudflare WSS tunnel forwarding WebSocket upgrades to `/ws/workers`
- Registration: `worker-001` reported `connectionStatus=ONLINE`,
  `workerStatus=IDLE`
- GPU: NVIDIA GeForce RTX 4070 Laptop GPU
- Dispatch: Backend sent `GPU_PYTORCH_TEST` to the named Worker
- Execution: real Docker CUDA container work on the supplier PC
- `status`: SUCCESS
- `runtimeMs`: 765
- `containerExitCode`: 0
- `resultChecksum`:
  `b05651beb760e6a8dd52f8b501ef7028c273bae8a593ff956f23c1ea7c2fc14c`
- After completion the Worker returned to `workerStatus=IDLE`

The checksum matches the same-machine runs above, so the deterministic CUDA
result is identical across networks.

## Cross-network two-PC validation procedure

Run the Network A Backend behind an HTTPS/WSS reverse proxy or tunnel on port
443. It must forward WebSocket upgrades for `/ws/workers`.

Network A:

```powershell
cd C:\gpu-compute-poc\backend
$env:GPU_POC_OPERATOR_TOKEN='<random-operator-token>'
$env:GPU_POC_WORKER_CREDENTIALS='worker-001=<random-worker-token>'
$env:JAVA_HOME='<Java-21-home>'
.\gradlew.bat bootRun --no-daemon
```

Network B supplier PC receives the built `worker/dist/gpu-worker-agent/`
directory. With NVIDIA Driver, Docker Desktop/WSL2 and Linux Docker Engine ready,
the supplier runs only the Worker; no Docker or pip command is run manually.
The supplier fills in `worker-config.json` next to the executable and
double-clicks `gpu-worker-agent.exe`; no PowerShell command is required:

```json
{
  "backendUrl": "wss://<public-host>/ws/workers",
  "workerId": "worker-001",
  "workerToken": "<random-worker-token>",
  "hourlyPrice": 600,
  "gpuIndex": 0,
  "supportedWorkloads": ["GPU_PYTORCH_TEST"]
}
```

The equivalent command-line form is still supported and unchanged:

```powershell
cd C:\gpu-worker-agent
$env:GPU_WORKER_TOKEN='<random-worker-token>'
.\gpu-worker-agent.exe `
  --backend-url wss://<public-host>/ws/workers `
  --worker-id worker-001 `
  --hourly-price 600 `
  --gpu-index 0 `
  --supported-workload GPU_PYTORCH_TEST
```

The Worker must print `Environment: READY`; otherwise it prints
`SETUP_REQUIRED [reason]` and does not REGISTER. It never installs Docker,
starts Docker Desktop or reboots Windows.

Network A submits two jobs:

```powershell
$headers = @{ Authorization = 'Bearer <random-operator-token>' }
$body = @{ workerId='worker-001'; workload='GPU_PYTORCH_TEST' } | ConvertTo-Json
$first = Invoke-RestMethod -Method Post -Uri https://<public-host>/api/jobs `
  -Headers $headers -ContentType 'application/json' -Body $body
Invoke-RestMethod -Uri "https://<public-host>/api/jobs/$($first.jobId)" -Headers $headers
$second = Invoke-RestMethod -Method Post -Uri https://<public-host>/api/jobs `
  -Headers $headers -ContentType 'application/json' -Body $body
Invoke-RestMethod -Uri "https://<public-host>/api/jobs/$($second.jobId)" -Headers $headers
```

Required evidence: first cache miss, second cache hit, both SUCCESS/exit 0 with
the RTX 4070 model and 64-character checksum, heartbeat timestamps advancing
during Docker work, IDLE after jobs, and OFFLINE after disconnect or timeout.

## Current verdict

Local real-GPU implementation validation: PASS.

Strict STEP 2.5 cross-network validation: PASS (2026-08-28, Cloudflare WSS
tunnel, RTX 4070 Laptop GPU, `containerExitCode=0`, matching checksum).

Overall STEP 2.5: PASS.

## Timed execution proof extension (2026-08-28)

The original `GPU_PYTORCH_TEST` image and digest remain unchanged. The separate
`GPU_PYTORCH_TEST_30S` workload is pinned to:

`le3560/gpu-pytorch-poc-30s@sha256:d7fe2de03ff8532900c600d9620cd7d57fcedbeb6531b3acc2b53a9f09a71a49`

Local real RTX 4070 E2E evidence:

- First job `bfe675b4-157d-4c7d-b1a9-aade524b9904`: SUCCESS,
  `imageCacheHit=false`, 30,085 ms, 348 iterations, matrix size 8192,
  checksum `1023b58f4b77ce9ffbea3f78c1c17956b7921d968e416c1f7b66fd86b38ea1e9`.
- Second job `a698aa63-773d-4ddb-922c-03dd6324b4eb`: SUCCESS,
  `imageCacheHit=true`, 30,071 ms, 343 iterations, matrix size 8192,
  checksum `47ae00eb2e8e78b87409cac90e13d6b4c6596209df47958aacdb7d85da0afb0c`.
- Peak observed GPU utilization / memory: 100% / 1199 MiB.
- During the first job, Docker showed container
  `gpu-poc-job-bfe675b4-157d-4c7d-b1a9-aade524b9904` and container ID
  `ab892cd7bf1ab337789ebfb1fd5462c9982d838eb4b0258b8de441633e09555b`.
- Docker inspect showed matching `gpu-poc.job-id`, `gpu-poc.worker-id`, and
  `gpu-poc.workload` labels.
- Heartbeat advanced while the second job remained BUSY; observed GPU
  utilization was 98% then 100%. The Worker returned to IDLE and became OFFLINE
  after its active WebSocket closed.

The Backend job ID, running container, labels, container result job ID, CUDA
checksum, and terminal Backend job record therefore formed one matching proof
chain. The rebuilt no-argument PyInstaller bundle registered from
`worker-config.json` with both workloads. The final verified bundle is available
at `worker/dist/gpu-worker-agent/`.

## Two-GPU-laptop Railway demonstration

Set Railway variables with independent secrets (do not reuse the literal
placeholders):

```text
GPU_POC_OPERATOR_TOKEN=<operator-token>
GPU_POC_WORKER_CREDENTIALS=worker-001=<worker-001-token>,worker-002=<worker-002-token>
```

Place this `worker-config.json` next to the bundle on laptop 1:

```json
{
  "backendUrl": "wss://<railway-host>/ws/workers",
  "workerId": "worker-001",
  "workerToken": "<worker-001-token>",
  "hourlyPrice": 600,
  "gpuIndex": 0,
  "supportedWorkloads": ["GPU_PYTORCH_TEST", "GPU_PYTORCH_TEST_30S"]
}
```

Laptop 2 uses the same file with `workerId` set to `worker-002` and its own
token. Start only `gpu-worker-agent.exe` on each supplier laptop. From the
request PC:

```powershell
$base = 'https://<railway-host>'
$headers = @{ Authorization = 'Bearer <operator-token>' }
Invoke-RestMethod -Uri "$base/api/workers" -Headers $headers |
  Select-Object workerId,connectionStatus,workerStatus,gpuModel,lastHeartbeat

function New-GpuJob([string]$workerId) {
  $body = @{ workerId=$workerId; workload='GPU_PYTORCH_TEST_30S' } |
    ConvertTo-Json -Compress
  Invoke-RestMethod -Method Post -Uri "$base/api/jobs" -Headers $headers `
    -ContentType 'application/json' -Body $body
}

$worker1Job = New-GpuJob 'worker-001'
$worker2Job = New-GpuJob 'worker-002'
$worker1Job.jobId
$worker2Job.jobId
```

For explicit parallel dispatch, use two PowerShell background jobs:

```powershell
$send = {
  param($base, $token, $workerId)
  $h = @{ Authorization = "Bearer $token" }
  $b = @{ workerId=$workerId; workload='GPU_PYTORCH_TEST_30S' } |
    ConvertTo-Json -Compress
  Invoke-RestMethod -Method Post -Uri "$base/api/jobs" -Headers $h `
    -ContentType 'application/json' -Body $b
}
$request1 = Start-Job $send -ArgumentList $base,'<operator-token>','worker-001'
$request2 = Start-Job $send -ArgumentList $base,'<operator-token>','worker-002'
$created = Receive-Job -Job $request1,$request2 -Wait -AutoRemoveJob
$created | Select-Object jobId,workerId,status
```

Poll one job and its named Worker every second:

```powershell
$jobId = '<job-id>'
do {
  $job = Invoke-RestMethod -Uri "$base/api/jobs/$jobId" -Headers $headers
  $worker = Invoke-RestMethod -Uri "$base/api/workers/$($job.workerId)" -Headers $headers
  [pscustomobject]@{
    JobId=$job.jobId; JobStatus=$job.status; Worker=$job.workerId
    WorkerStatus=$worker.workerStatus; Heartbeat=$worker.lastHeartbeat
    Container=$job.containerName
  }
  if ($job.status -notin 'SUCCESS','FAILED','RESULT_UNKNOWN') {
    Start-Sleep -Seconds 1
  }
} while ($job.status -notin 'SUCCESS','FAILED','RESULT_UNKNOWN')
$job | ConvertTo-Json -Depth 5
```

On each supplier laptop, while its job is RUNNING:

```powershell
$jobId = '<same-job-id-shown-on-request-PC>'
$container = "gpu-poc-job-$jobId"
do {
  $running = docker ps --filter "name=$container" --format '{{.Names}} {{.Status}}'
  if (-not $running) { Start-Sleep -Seconds 1 }
} until ($running)
$running
docker inspect $container --format '{{json .Config.Labels}}'
docker logs --follow $container
```

Run `nvidia-smi -l 1` in another terminal while the commands above are active.
The Worker removes the container after collecting its result. Evidence is
complete when both Backend jobs are SUCCESS
with the same job ID shown in the container name/label/result, exit code 0,
28–35 second runtime, non-empty checksum, container ID/name, and each Worker has
returned to IDLE.

## Final automated results

- Backend clean test: 79 passed, 0 failures/errors/skipped
- Worker test: 70 passed
- Runtime image tests: 10 passed (5 original + 5 timed image)
- Python dependency check: no broken requirements

## Configuration-file agent re-validation (2026-08-28)

The rebuilt PyInstaller bundle was re-validated after the double-click execution
change. Both runs used the same bundle, a loopback Backend with bearer
credentials and the real RTX 4070.

Zero-argument run: `worker-config.json` placed beside `gpu-worker-agent.exe`,
launched from `C:\Windows\System32` so the working directory could not supply
the path. The agent printed `Config: ...\dist\gpu-worker-agent\worker-config.json`
and `Environment: READY`, registered `ONLINE`/`IDLE` with the RTX 4070 snapshot,
and job `d8a6ebcf-1d06-45cd-98cc-4f3480eb5628` returned SUCCESS, 757 ms,
`containerExitCode=0`, `imageCacheHit=true`, checksum
`b05651beb760e6a8dd52f8b501ef7028c273bae8a593ff956f23c1ea7c2fc14c`, and the
Worker returned to `IDLE`.

Command-line regression: the same bundle was started with the original flags
while the file held `hourlyPrice` 600 and a deliberately wrong token, and
`GPU_WORKER_TOKEN` held the real token. REGISTER reported `hourlyPrice` 900 from
`--hourly-price`, and the handshake succeeded on the environment token, so the
documented precedence (command line, then environment variable, then file) holds.
Job `dacd0984-2d32-43eb-befd-91cf184c68b2` returned SUCCESS, 737 ms,
`containerExitCode=0`, the same checksum, and `IDLE` afterwards.

Missing configuration: with no file and no arguments the agent printed one
readable line naming `worker-config.json`, its expected path and
`worker-config.example.json`, then exited with code 2 and no traceback.
