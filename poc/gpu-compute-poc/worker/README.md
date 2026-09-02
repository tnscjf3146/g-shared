# GPU Worker — STEP 1 CUDA Runtime and STEP 2 Backend Client

## 목표와 성공 gate

승인된 `BAAI/bge-small-en-v1.5` 모델을 `device="cuda"`로 실행한다. 아래 두 조건을 모두
실제 NVIDIA GPU PC에서 확인해야 STEP 1 성공이다.

1. Worker 로그에 `device=cuda`와 `POC SUCCESS: YES`가 출력된다.
2. 별도 터미널의 `nvidia-smi`에서 해당 프로세스와 GPU utilization 상승이 관찰된다.

CPU fallback 결과는 자동 테스트나 개발 확인에만 쓸 수 있고 성공으로 인정하지 않는다.

## 사전 조건

- Python 3.11 이상
- NVIDIA GPU와 최신 호환 드라이버
- `nvidia-smi` 정상 실행
- 최초 모델 다운로드를 위한 인터넷 연결 또는 준비된 Hugging Face cache

`nvcc`는 사전 빌드 PyTorch wheel을 실행할 때 필수 조건이 아니다. CUDA 지원 PyTorch 설치 명령은
GPU PC 드라이버에 맞춰 [PyTorch 공식 selector](https://pytorch.org/get-started/locally/)에서 다시 확인한다.

## 설치

Windows PowerShell 예시(공식 CUDA 12.6 wheel 기본값):

```powershell
cd C:\gpu-compute-poc\worker
.\scripts\install-cuda.ps1 -Python py -TorchVersion 2.12.1 -CudaWheel cu126
```

`py`가 여러 버전을 관리한다면 먼저 Python 3.11+ 가상환경을 직접 만들고 그 Python 경로를
`-Python`에 넘긴다. Linux/WSL은 다음과 같다.

```bash
cd /path/to/gpu-compute-poc/worker
CUDA_WHEEL=cu126 TORCH_VERSION=2.12.1 ./scripts/install-cuda.sh
```

설치 스크립트 마지막의 `cuda_available=True`를 확인한다. False라면 실행하지 말고 드라이버와
PyTorch wheel 조합을 먼저 수정한다.

## 실행

터미널 A에서 200ms 간격으로 GPU를 관찰한다.

```powershell
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv -lms 200
```

또는 전체 utilization을 관찰한다.

```powershell
nvidia-smi dmon -s pucm -d 1
```

터미널 B에서 Worker를 실행한다.

```powershell
cd C:\gpu-compute-poc\worker
.\.venv\Scripts\python.exe -m gpu_worker --repeat 256 --batch-size 64
```

작업이 너무 빨라 utilization을 확인하기 어렵다면 `--repeat 2048` 이상으로 높인다. 테스트 문자열은
공개된 세 문장만 반복하며 임의 사용자 코드는 실행하지 않는다.

## 자동 테스트

외부 ML 패키지나 GPU 없이도 핵심 제어 로직을 검증한다.

```powershell
cd C:\gpu-compute-poc\worker
python -m unittest discover -s tests -v
```

검증 범위:

- NVML GPU 이름/VRAM/utilization 변환과 종료
- 잘못된 GPU index 거부
- CUDA unavailable 거부
- 모델 생성과 encode에 `cuda` 명시
- 실행시간 측정 전후 CUDA synchronize
- CPU에 남은 모델 거부
- allowlist 밖 모델 거부

## 개발용 CPU fallback

```powershell
python -m gpu_worker --allow-cpu --repeat 1
```

정상 실행되어도 종료 코드 3과 `POC SUCCESS: NO`가 의도된 결과다.

## STEP 2 Backend 연결

STEP 2 client는 GPU Runtime과 WebSocket transport를 분리한다. 연결 전에 PyTorch CUDA availability와
NVML 초기화를 확인하며, GPU 이름·VRAM·utilization은 하드코딩하지 않고 실제 snapshot에서 읽는다.

```powershell
cd C:\gpu-compute-poc\worker
.\.venv\Scripts\python.exe -m gpu_worker.client `
  --backend-url ws://127.0.0.1:8080/ws/workers `
  --worker-id worker-001 `
  --hourly-price 600 `
  --gpu-index 0 `
  --supported-workload EMBEDDING
```

동작 순서는 다음과 같다.

1. CUDA/NVML과 Docker Linux/GPU 환경을 검증한다.
2. 실행 파일 옆의 `benchmark-result.json`을 현재 GPU UUID와 benchmark version으로 검증한다.
3. cache miss이면 내부 `GPU_STANDARD_BENCHMARK`를 실행하고 결과를 원자적으로 저장한다.
4. Benchmark가 성공한 뒤에만 Backend로 outbound WebSocket 연결을 만든다.
5. 실제 GPU snapshot과 benchmark를 포함한 `REGISTER`를 한 번 보낸다.
6. 5초마다 새 NVML snapshot으로 `HEARTBEAT`를 보낸다.
7. Backend가 연결을 닫거나 transport 오류가 발생하면 재연결하지 않고 종료한다.

Windows WDDM 환경에서 유지 중인 NVML handle이 무효화되는 경우 snapshot 시 probe를 한 번만
재초기화한다. 이는 GPU 계측 복구이며 WebSocket 자동 reconnect가 아니다.

STEP 2 client 자동 테스트를 포함한 전체 Worker 테스트:

```powershell
cd C:\gpu-compute-poc\worker
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

# STEP 2.5 Docker GPU agent

The Worker checks NVIDIA/NVML, Docker CLI/Engine, Linux container mode and GPU
access from a digest-pinned CUDA probe before connecting. A failed check prints
`SETUP_REQUIRED` and does not REGISTER. It does not install/start Docker or
restart Windows.

The distributable directory is built by the platform operator, not the GPU
supplier:

```powershell
cd C:\gpu-compute-poc\worker
.\scripts\build-agent.ps1
```

The supplier copies `dist\gpu-worker-agent\`, edits one file and double-clicks
the executable. No PowerShell command is required.

1. Rename `worker-config.example.json` to `worker-config.json` in the same
   directory as `gpu-worker-agent.exe`.
2. Fill in `backendUrl`, `workerId`, `workerToken` and `hourlyPrice`.
3. Double-click `gpu-worker-agent.exe`.

```json
{
  "backendUrl": "wss://<backend-host>/ws/workers",
  "workerId": "worker-001",
  "workerToken": "<worker-token>",
  "hourlyPrice": 600,
  "gpuIndex": 0,
  "supportedWorkloads": ["GPU_PYTORCH_TEST", "GPU_PYTORCH_TEST_30S"]
}
```

The file is looked up next to the running executable, not in the current
working directory, so Explorer double-click and shell shortcuts both work. Keys
starting with `_` are ignored, so the example file's comment can stay. A missing
file, malformed JSON or a missing required setting prints one readable line and
exits with code 2 instead of a traceback. `worker-config.json` holds a token and
is git-ignored; only `worker-config.example.json` belongs in the repository.

The earlier command-line form still works unchanged, and operators can also use
environment variables. The precedence is command-line argument, then environment
variable, then `worker-config.json`.

```powershell
$env:GPU_WORKER_TOKEN='<worker-token>'
.\gpu-worker-agent.exe `
  --backend-url wss://<backend-host>/ws/workers `
  --worker-id worker-001 `
  --hourly-price 600 `
  --supported-workload GPU_PYTORCH_TEST
```

| Setting | `worker-config.json` | Environment variable | Command line |
|---|---|---|---|
| Backend URL | `backendUrl` | `GPU_WORKER_BACKEND_URL` | `--backend-url` |
| Worker ID | `workerId` | `GPU_WORKER_ID` | `--worker-id` |
| Worker token | `workerToken` | `GPU_WORKER_TOKEN` | not accepted |
| Hourly price | `hourlyPrice` | `GPU_WORKER_HOURLY_PRICE` | `--hourly-price` |
| GPU index | `gpuIndex` | `GPU_WORKER_GPU_INDEX` | `--gpu-index` |
| Workloads | `supportedWorkloads` | `GPU_WORKER_SUPPORTED_WORKLOADS` (comma separated) | `--supported-workload` (repeatable) |

The token has no command-line flag so it never appears in a process list or
shell history. The token is sent only in the WebSocket Authorization header. The Backend sends
only a workload ID; the fixed Worker catalog resolves the immutable Docker image.
The Worker performs image inspect/pull, hardened container execution, CUDA result
validation and `EXECUTE_RESULT` automatically while heartbeat continues.

Before opening the WebSocket, every ready Worker must have a valid
`gpu-benchmark-v1` result. A cache is reused only when both GPU UUID and version
match. Missing, damaged, stale, non-finite, or non-positive results trigger one
new benchmark attempt. Failure exits without REGISTER; there is no infinite
retry. `GPU_STANDARD_BENCHMARK` is an internal catalog entry and cannot be
advertised or dispatched through the Job API. See
[the benchmark specification and GPU validation guide](../docs/gpu-standard-benchmark.md).

`GPU_PYTORCH_TEST_30S` uses a separate immutable image and a Worker-generated
container name, labels, and job environment value. During execution,
`docker ps` shows `gpu-poc-job-<jobId>`. `docker inspect` exposes only the
Worker-generated `gpu-poc.job-id`, `gpu-poc.worker-id`, and
`gpu-poc.workload` labels. The Backend still sends only `jobId` and `workload`.
