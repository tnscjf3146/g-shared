# STEP 2.5 Remote GPU Execution 설계

- 상태: Approved with MVP amendments
- 날짜: 2026-08-27
- 선행 조건: STEP 2 PASS
- 대상 OS: Windows 10/11, NVIDIA GPU 1개

## 1. 목표

STEP 2.5는 서로 다른 인터넷 환경의 Spring Boot Backend와 Windows GPU 공급자 PC 사이에서 다음
End-to-End 흐름을 검증한다.

```text
Backend가 특정 ONLINE/IDLE Worker에 GPU_PYTORCH_TEST 요청
  -> 기존 outbound WebSocket으로 EXECUTE 전송
  -> Worker가 승인된 고정 Docker image를 확인하고 필요할 때만 pull
  -> Worker가 제한된 Docker container를 --gpus all로 실행
  -> container 안의 PyTorch가 실제 CUDA matrix multiplication 수행
  -> Worker가 구조화된 결과를 검증하고 EXECUTE_RESULT 전송
  -> Backend가 같은 jobId의 결과를 저장하고 조회에 제공
  -> Worker가 IDLE로 복귀
```

GPU 공급자는 Worker 설치와 실행만 담당한다. 공급자가 `docker pull`, `docker run`, `pip install`,
Python virtual environment 구성 또는 CUDA 작업 명령을 직접 실행하지 않는다.

STEP 2의 다음 불변 조건은 그대로 유지한다.

- Worker가 Backend에 outbound WebSocket connection을 만든다.
- `/ws/workers` raw JSON protocol을 사용한다.
- REGISTER, 5초 HEARTBEAT, 15초 liveness timeout을 유지한다.
- `connectionStatus`와 `workerStatus`를 분리한다.
- last-connection-wins와 active session identity 기반 stale disconnect 방어를 유지한다.
- heartbeat/timeout/register/disconnect의 worker 단위 원자성을 유지한다.
- 자동 reconnect는 구현하지 않는다.

## 2. 범위

### 2.1 포함

- Backend의 명시적 Worker 대상 job 제출 및 in-memory 결과 조회
- WebSocket `EXECUTE`, `EXECUTE_RESULT` message
- Worker Environment Manager, Docker Manager, Job Manager
- NVIDIA GPU/Driver, Docker CLI/Engine 및 container GPU 접근 자동 검사
- 조건 미충족 시 ONLINE REGISTER 전 local `SETUP_REQUIRED` 처리
- Worker에 내장된 workload-to-image allowlist
- Docker Hub의 digest 고정 image pull과 local cache 재사용
- 제한된 container 안의 실제 PyTorch CUDA matrix multiplication
- container exit code/stdout/result 검증과 정리
- 정상/실패 job lifecycle 및 결과 전송
- Backend, Worker, runtime image 자동 테스트
- 서로 다른 인터넷 환경에서 RTX 4070 Laptop GPU 실제 통합 테스트

### 2.2 제외

- LoRA/QLoRA training 또는 inference
- Base Model, Dataset, IPFS/Kubo
- Scheduler와 자동 Worker 선택
- Job chunking, retry, 재할당
- P2P, Blockchain, 정산
- Multi GPU
- Model Registry, Model Cache
- 사용자 지정 image, command, environment variable, mount 또는 script
- Worker 자동 reconnect
- Backend/Worker job persistence 또는 재시작 복구
- Docker Desktop/WSL2 자동 설치 또는 초기 설정
- 관리자 권한 획득, UAC 처리 또는 자동 재부팅
- WorkloadCatalog cryptographic signature 및 원격 update
- 실패한 결과의 자동 재전송

## 3. 접근 방식 비교와 결정

### 3.1 선택: Worker 내장 Allowlist + 사전 설치된 Docker Desktop WSL2

Windows Worker 배포본에 고정 `WorkloadCatalog`를 포함한다. Backend는 workload ID만 전송하고,
Worker가 workload ID를 고정 image digest와 고정 실행 정책으로 해석한다. Docker Desktop은 Windows의
WSL2 backend를 사용한다.

이 방식은 Backend가 image나 command를 제어하지 못하게 하면서 향후 catalog에
`LORA_TRAINING`을 추가할 수 있다. Docker 공식 문서에 따르면 Windows Docker Desktop의 NVIDIA GPU
지원은 WSL2 backend에서만 제공되므로 STEP 2.5의 Windows GPU 경로는 WSL2로 고정한다.

### 3.2 제외: Backend가 image reference를 EXECUTE에 포함

운영자가 image를 바꾸기 쉽지만 Backend 침해, 입력 검증 누락 또는 protocol 오용이 공급자 PC의
임의 container 실행으로 이어진다. tag allowlist만 사용해도 mutable tag가 다른 image를 가리킬 수
있으므로 채택하지 않는다.

### 3.3 제외: Worker가 WSL 안에 독립 Docker Engine 설치

Docker Desktop 의존을 줄일 수 있지만 WSL distribution provisioning, daemon lifecycle, Windows 시작
연동, socket 권한 및 GPU runtime 구성이 공급자별로 달라진다. 이번 Windows MVP에는 불필요한 운영
표면이므로 채택하지 않는다.

## 4. 전체 아키텍처

```text
Backend
  WorkerWebSocketHandler
    - REGISTER / HEARTBEAT 유지
    - EXECUTE_RESULT decode 및 JobService 위임
    - Docker를 직접 호출하지 않음
  WorkerRegistry
    - activeSession / connectionStatus / workerStatus
    - activeJobId와 dispatch session identity
  JobController
    - POST /api/jobs
    - GET /api/jobs/{jobId}
  JobService + InMemoryJobRegistry
    - 명시적 Worker dispatch
    - job/result 상태와 session 상관관계
  ApprovedWorkloadPolicy
    - workload/digest 결과 검증 사본

                  WSS /ws/workers

Windows GPU Worker Agent
  GPU Monitor
    - NVML GPU/VRAM/utilization/driver 확인
  Backend Client
    - REGISTER / HEARTBEAT 송신
    - EXECUTE 수신 / EXECUTE_RESULT 송신
  Environment Manager
    - NVIDIA/Driver/Docker/Engine/container GPU readiness 검사
  Workload Catalog
    - workload -> immutable image digest + fixed run policy
  Docker Manager
    - inspect / pull / run / collect / cleanup
  Job Manager
    - 단일 job 상태와 IDLE/BUSY 전환

Approved Runtime Image
  gpu-pytorch-poc:0.1@sha256:RELEASE_MANIFEST_DIGEST
    - Python / PyTorch / CUDA runtime / gpu_task.py
```

`RELEASE_MANIFEST_DIGEST`는 wire message나 운영자 입력값이 아니라 설계 표기의 논리적 이름이다.
플랫폼 CI가 image를 push한 뒤 얻은 실제 64자리 SHA-256 digest를 Worker source의 고정 allowlist와
Backend result policy에 기록한다. 실제 digest가 없는 build는 release할 수 없다. 이번 MVP는 catalog
signature와 원격 update를 구현하지 않지만 향후 검증 provider를 추가할 수 있도록 catalog lookup을
별도 module로 유지한다.

## 5. Backend 설계

### 5.1 명시적 Worker 대상 Job API

Scheduler를 추가하지 않기 위해 요청자가 Worker를 직접 지정한다.

#### `POST /api/jobs`

```json
{
  "workerId": "worker-001",
  "workload": "GPU_PYTORCH_TEST"
}
```

Backend가 UUID `jobId`를 생성하고 성공적으로 active session에 전송하면 HTTP 202를 반환한다.

```json
{
  "jobId": "f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
  "workerId": "worker-001",
  "workload": "GPU_PYTORCH_TEST",
  "status": "DISPATCHED"
}
```

요청에는 image, command, argument, environment variable, URL, file path 또는 mount를 넣을 수 없다.
알 수 없는 JSON 필드는 거부한다.

#### `GET /api/jobs/{jobId}`

in-memory job의 현재 상태와 terminal result를 반환한다. 미등록 job은 404이다. STEP 2.5에서는
Backend 재시작 후 job 정보가 사라지는 것을 허용한다.

Backend job 상태는 다음으로 제한한다.

```text
DISPATCHED
SUCCESS
FAILED
RESULT_UNKNOWN
```

- `DISPATCHED`: active session으로 EXECUTE frame 전송 완료
- `SUCCESS`/`FAILED`: 같은 dispatch session에서 유효한 EXECUTE_RESULT 수신
- `RESULT_UNKNOWN`: result 수신 전 active session disconnect, timeout 또는 last-connection-wins 교체

실제 실행 시작을 확인하는 별도 ACK를 만들지 않으므로 `RUNNING` 상태를 추측해 제공하지 않는다.

### 5.2 원자적 dispatch reservation

WorkerRecord에 REST 미노출 내부 필드 `activeJobId`와 `activeJobSessionId`를 추가한다. job dispatch는
workerId 단위 원자 연산으로 다음 조건을 한 번에 확인하고 reserve한다.

```text
connectionStatus == ONLINE
activeSession != null
workerStatus == IDLE
activeJobId == null
cudaAvailable == true
supportedWorkloads contains requested workload
```

reservation이 성공한 session identity를 JobRecord에도 저장한다. WebSocket send는 map compute 밖에서
직렬화하여 수행한다. send 실패 시 같은 session identity인 경우에만 reservation을 해제하고 job을
`RESULT_UNKNOWN`으로 만든다.

HEARTBEAT가 잘못 `IDLE`을 보고해도 `activeJobId`가 남아 있으면 두 번째 job을 dispatch하지 않는다.
유효한 EXECUTE_RESULT 또는 active session 상실만 reservation을 끝낸다.

### 5.3 결과 상관관계와 session 안전성

EXECUTE_RESULT는 다음 조건을 모두 만족할 때만 반영한다.

- message를 보낸 session이 현재 workerId의 active session이다.
- jobId가 존재하고 `DISPATCHED`이다.
- JobRecord의 workerId와 dispatch session identity가 현재 session과 일치한다.
- result schema와 success/failure 필드 조합이 유효하다.

성공적으로 반영할 때 job terminal 상태 저장, `activeJobId` 제거, `workerStatus=IDLE` 전환을 worker
단위로 원자 처리한다. 늦은 stale result는 새 connection이나 새 job 상태를 변경하지 않는다.

### 5.4 STEP 2 동시성 규칙 확장

- Docker pull/run 중에도 Worker heartbeat task가 독립적으로 계속 실행되어야 한다.
- heartbeat와 timeout 경합 규칙은 STEP 2와 동일하다.
- job 중 active session이 disconnect/timeout되면 connection은 OFFLINE, 마지막 workerStatus는 보존하고
  해당 job은 `RESULT_UNKNOWN`으로 전환한다.
- job 중 session-B가 REGISTER하면 last-connection-wins로 B가 active가 된다. session-A job은
  `RESULT_UNKNOWN`이며 B에는 이전 job을 이전하지 않는다.
- session-A의 늦은 disconnect, heartbeat, result는 session-B를 변경하지 않는다.

WorkerRegistry와 InMemoryJobRegistry 사이의 후속 상태 반영은 JobService가 조정한다. active session
교체/무효화 결정은 기존처럼 WorkerRegistry의 workerId 단위 원자 연산이 먼저 수행되며, 반환된
displaced jobId를 JobService가 terminal 처리한다. WebSocket close와 result send 같은 I/O는 원자
연산 밖에서 수행한다.

## 6. Worker 내부 모듈 구조

### 6.1 GPU Monitor

기존 `NvmlGpuProbe`를 재사용해 GPU 모델, total/free VRAM, utilization, NVIDIA Driver를 읽는다.
STEP 1 embedding CLI와 STEP 2 `CudaGpuStatusProvider`는 회귀 호환을 유지한다.

STEP 2.5 배포 Agent는 host에 별도 PyTorch 설치를 요구하지 않는다. 새 driver-level provider는 NVML과
Windows `nvcuda.dll`의 CUDA Driver API `cuInit`/`cuDeviceGetCount`로 실제 CUDA device를 검증하고,
PyTorch CUDA availability는 승인된 container 안에서 추가 검증한다.
따라서 공급자용 Windows Worker 배포본은 embedded Python과 고정 dependency를 포함한 self-contained
installer/bundle로 제공하며 공급자가 `pip install` 또는 virtual environment를 구성하지 않는다.

REGISTER의 `cudaAvailable=true`는 NVIDIA GPU/Driver가 CUDA container 실행 전제조건을 만족했다는
의미이다. container의 `torch.cuda.is_available()`는 매 process 최초 GPU runtime probe와 실제 job
결과로 별도 검증한다.

### 6.2 Backend Client

기존 단일 connection transport를 다음 독립 task로 확장한다.

- receiver: text frame을 decode하고 EXECUTE를 Job Manager에 전달
- heartbeat producer: 5초마다 최신 NVML 상태와 Job Manager의 workerStatus 생성
- outbound writer: REGISTER, HEARTBEAT, EXECUTE_RESULT를 하나의 queue에서 직렬 송신
- connection supervisor: server close, protocol error, Ctrl+C를 처리하고 자동 reconnect 없이 종료

Docker subprocess는 event loop를 막지 않는 별도 thread/executor에서 실행한다. outbound writer를 하나로
제한해 heartbeat와 result의 동시 WebSocket send를 피한다.

### 6.3 Environment Manager

host 준비 상태만 담당하고 WebSocket message나 Docker job 결과를 만들지 않는다.

```text
inspect_host()
  -> NVIDIA GPU / Driver / NVML
  -> Docker CLI availability
  -> Docker Engine response
  -> fixed digest CUDA probe container의 NVIDIA GPU 접근
  -> READY 또는 명시적 SETUP_REQUIRED
```

Environment Manager는 Docker Desktop/WSL2를 설치·시작·설정하지 않는다. probe image는
`GPU_PYTORCH_TEST` workload image와 분리된 고정 digest의 소형 NVIDIA CUDA image이며 Backend가
선택하거나 변경할 수 없다. 따라서 startup에서 container GPU를 실제 검증하면서도 첫 workload job의
image cache miss를 검증할 수 있다.

### 6.4 Workload Catalog

Worker package 내부의 read-only catalog이며 Backend가 수정할 수 없다. Backend도 배포 시 고정된
workload ID와 digest를 read-only policy로 가져 결과를 교차 검증하지만, EXECUTE에는
workload ID만 넣는다. 두 사본이 다르면 Worker가 자신의 local catalog를 우선하며 digest가 다른 결과는
Backend에서 실패 처리되므로 임의 image 실행으로 이어지지 않는다.

```text
GPU_PYTORCH_TEST
  imageRepository: platform-owned Docker Hub repository
  imageTag: 0.1 (표시 및 운영 추적용)
  imageDigest: sha256:<CI-produced immutable digest>
  entrypointMode: GPU_PYTORCH_TEST
  timeoutSeconds: 300
  maxOutputBytes: 1048576
  resultSchemaVersion: 1
```

향후 `LORA_TRAINING`은 별도 catalog entry로만 추가한다. 기존 entry의 image, command 또는 정책을
Backend 입력으로 override할 수 없다.

### 6.5 Docker Manager

Docker CLI를 argument array로 호출하며 shell string을 만들지 않는다. 책임은 다음으로 제한한다.

- Engine readiness 확인
- pinned digest의 local image 존재 확인
- 없을 때 pinned digest pull
- pull 후 RepoDigest가 catalog digest와 일치하는지 확인
- 고정된 보안 option으로 container 실행
- stdout/stderr 크기 제한, timeout, exit code 수집
- 정상/오류/취소 경로의 container 정리

### 6.6 Job Manager

process 안에서 한 번에 한 job만 소유한다.

- EXECUTE validation 및 catalog lookup
- 원자적 `IDLE -> BUSY` claim
- Docker Manager 호출
- container output 검증과 EXECUTE_RESULT 생성
- terminal result를 local diagnostic file에 원자 기록
- result queue 제출 후 `BUSY -> IDLE`
- connection 상실 시 실행 container 취소/정리

local diagnostic result는 자동 재전송하지 않는다. 이는 result send 실패 시 성공을 잃어버리지 않기
위한 운영 증거이며 Backend의 job persistence나 retry가 아니다.

## 7. Worker 상태 변화

### 7.1 Setup 상태

Setup 상태는 connection 상태와 별개인 Worker process의 startup 상태이다.

```text
CHECKING
PREPARING
READY
SETUP_REQUIRED
FATAL
```

`READY`일 때만 Backend에 연결하고 REGISTER한다. NVIDIA Driver, Docker 설치/Engine 또는 container GPU
접근 조건이 충족되지 않으면 `SETUP_REQUIRED`를 local console/log/status file과 전용 exit code로
명확히 표시하고 WebSocket에 연결하지 않는다. 따라서 Backend Registry에 해당 process가 ONLINE으로
나타나지 않는다.

STEP 2.5에서는 준비되지 않은 미등록 장비를 Backend에 보고하는 별도 enrollment protocol을 추가하지
않는다. 기존 Registry의 `connectionStatus=ONLINE|OFFLINE` 의미를 setup 상태로 오염시키지 않기 위한
결정이다.

### 7.2 Connection 및 실행 상태

```text
READY + WebSocket/REGISTER 성공
  -> connectionStatus=ONLINE, workerStatus=IDLE

유효한 EXECUTE claim
  -> connectionStatus=ONLINE, workerStatus=BUSY

성공/실패 result 생성 및 queue 제출
  -> connectionStatus=ONLINE, workerStatus=IDLE

active connection 종료/timeout
  -> connectionStatus=OFFLINE
  -> workerStatus는 마지막 IDLE 또는 BUSY 보존
```

Job Manager가 상태의 단일 작성자이며 heartbeat는 해당 snapshot만 읽는다.

## 8. Docker 환경 검사 전략

### 8.1 현실적인 Windows 경계

Windows Docker GPU는 Docker Desktop WSL2 backend로 제한한다. Docker Desktop/WSL2 설치·초기 설정은
공급자의 사전 조건이다. 해당 작업은 관리자 권한이나 재부팅이 필요할 수 있으므로 Worker가 숨기거나
자동화하지 않고 local SETUP_REQUIRED 원인으로 안내한다.

### 8.2 검사 순서

1. NVML로 NVIDIA GPU와 Driver를 검사한다.
2. `docker version`으로 Docker CLI 존재 여부를 검사한다.
3. `docker info`로 Engine 응답과 Linux container mode를 검사한다.
4. Worker에 고정된 probe image digest가 없으면 자동 pull한다.
5. `docker run --rm --gpus all <fixed-probe-digest> nvidia-smi`를 실행한다.
6. exit code 0, NVIDIA GPU model 발견, Driver 접근을 모두 확인하면 `READY`로 진행한다.
7. 하나라도 실패하면 원인별 `SETUP_REQUIRED`를 local에 기록하고 Backend에 연결하지 않는다.

Worker는 Docker Engine을 자동 시작하지 않는다. Docker가 없거나 Engine이 정지했거나 WSL2/Linux
container 설정이 잘못된 경우 공급자가 Docker Desktop에서 조치한 뒤 Worker를 다시 실행한다.

### 8.3 GPU runtime 검사 시점

startup에서는 소형 고정 probe image의 `nvidia-smi`로 Docker container GPU device 접근을 검증한다.
최초 workload job에서는 별도의 `gpu-pytorch-poc` image 안에서 `torch.cuda.is_available()`와 GPU model을
다시 확인한다. 두 검사는 각각 Docker GPU 연결과 실제 PyTorch CUDA runtime을 증명한다.

## 9. Docker Hub Image 관리와 Cache

### 9.1 플랫폼 build/release

플랫폼 CI만 runtime image를 build하고 Docker Hub의 플랫폼 소유 repository에 push한다. 공급자와
Backend는 image를 build하거나 push하지 않는다.

image에는 다음만 포함한다.

- Python
- CUDA 지원 PyTorch
- 필요한 CUDA runtime library
- `gpu_task.py`

Base Model, Dataset, LoRA artifact, IPFS client, shell utility 및 Docker CLI는 포함하지 않는다. base image와
Python dependency도 release에서 고정한다. CI는 container test, vulnerability scan, SBOM 생성 후 실제
RepoDigest를 Worker catalog release에 반영한다.

### 9.2 Pull 및 cache 판정

1. `docker image inspect`로 정확한 catalog digest가 local에 있는지 확인한다.
2. 있으면 `imageCacheHit=true`로 하고 pull command를 실행하지 않는다.
3. 없으면 `docker image pull repository@sha256:digest`를 실행한다.
4. pull 후 local RepoDigest가 catalog와 정확히 일치하지 않으면 image를 실행하지 않는다.
5. container도 tag가 아닌 같은 digest reference로 실행한다.

Docker는 digest pull을 immutable version pinning으로 지원하며 image layer를 content-addressable cache로
재사용한다. Docker Hub 제한 또는 인증 실패는 `IMAGE_PULL_FAILED`로 구분한다. private repository를
사용할 경우 credential은 Worker installer의 OS credential store에 배치하고 JSON message, CLI argument,
stdout 또는 Backend 로그에 노출하지 않는다.

## 10. Container 실행과 CUDA 작업

### 10.1 고정 실행 정책

개념적 실행은 다음과 같으며 실제 구현은 shell을 거치지 않는 argument list를 사용한다.

```text
docker run
  --rm
  --gpus all
  --network none
  --read-only
  --cap-drop ALL
  --security-opt no-new-privileges
  --pids-limit 128
  --memory 4g
  --label platform.job-id=<Backend-generated UUID>
  repository@sha256:digest
```

- `--privileged`, host bind mount, Docker socket mount, port publish, arbitrary device, arbitrary environment
  variable을 사용하지 않는다.
- container name/label은 Backend가 생성한 UUID에서만 만든다.
- 300초 timeout과 1 MiB stdout/stderr cap을 적용한다.
- 정상 종료에는 `--rm`, timeout/connection loss에는 label/name 기반 stop 후 remove를 `finally`에서 수행한다.

### 10.2 실제 PyTorch CUDA 작업

`gpu_task.py`는 다음 순서로 실행한다.

1. `torch.cuda.is_available()`가 true인지 확인한다.
2. `torch.cuda.get_device_name(0)`과 free/total memory를 읽는다.
3. free VRAM의 최대 20% 안에서 입력 2개와 결과 1개를 수용하도록 FP32 square matrix size를 계산하되
   2048 이상, 8192 이하로 제한한다. 2048도 안전하지 않으면 명시적으로 실패한다.
4. deterministic seed/known input tensor를 CUDA device에 생성한다.
5. 여러 번 matrix multiplication을 수행해 짧지만 실질적인 CUDA compute를 만든다.
6. 시작/종료 구간에 `torch.cuda.synchronize()`를 호출해 runtime을 정확히 측정한다.
7. finite 값과 알려진 sample 결과를 검사한다.
8. canonical sample bytes의 SHA-256 checksum을 생성한다.
9. marker가 붙은 JSON 한 줄을 stdout의 마지막 record로 출력한다.

sleep이나 utilization 수치만으로 성공하지 않는다. success record는 적어도 GPU model, CUDA true,
matrix size, iteration count, runtime, checksum을 포함한다.

```text
GPU_TASK_RESULT={"schemaVersion":1,"status":"SUCCESS",...}
```

Worker는 marker가 정확히 하나이고 schema가 유효하며 exit code가 0일 때만 SUCCESS로 변환한다.

## 11. WebSocket 메시지 계약

기존 REGISTER와 HEARTBEAT schema 및 검증은 유지한다. STEP 2.5 Agent가 READY이면
`supportedWorkloads`에 `GPU_PYTORCH_TEST`를 포함하고 최초 `workerStatus`는 `IDLE`이다.

### 11.1 Backend -> Worker: EXECUTE

```json
{
  "type": "EXECUTE",
  "jobId": "f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
  "workload": "GPU_PYTORCH_TEST"
}
```

| 필드 | 타입 | 검증 |
|---|---|---|
| `type` | string | 정확히 `EXECUTE` |
| `jobId` | string | canonical UUID, 현재 process에서 terminal/active 중복 불가 |
| `workload` | string | non-blank, local Workload Catalog에 존재 |

추가 필드는 거부한다. 특히 image, command, args, env, mount, input URL은 protocol에 존재하지 않는다.

### 11.2 Worker -> Backend: EXECUTE_RESULT SUCCESS

```json
{
  "type": "EXECUTE_RESULT",
  "jobId": "f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
  "status": "SUCCESS",
  "gpuModel": "NVIDIA GeForce RTX 4070 Laptop GPU",
  "runtimeMs": 850,
  "resultChecksum": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "containerExitCode": 0,
  "matrixSize": 8192,
  "iterations": 8,
  "imageCacheHit": false,
  "imageDigest": "sha256:CATALOG_DIGEST"
}
```

SUCCESS 검증:

- `runtimeMs > 0`, `matrixSize >= 2048`, `iterations > 0`
- checksum은 SHA-256 lowercase hex 64자
- `containerExitCode == 0`
- gpuModel non-blank
- imageDigest가 dispatch workload의 catalog digest와 일치
- failure 전용 필드가 없음

### 11.3 Worker -> Backend: EXECUTE_RESULT FAILED

```json
{
  "type": "EXECUTE_RESULT",
  "jobId": "f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
  "status": "FAILED",
  "errorCode": "IMAGE_PULL_FAILED",
  "errorMessage": "approved image pull failed",
  "containerExitCode": null,
  "imageCacheHit": false
}
```

FAILED 검증:

- errorCode는 알려진 Worker error code
- errorMessage는 non-blank UTF-8 최대 2 KiB이며 secret과 원본 credential을 제거
- container가 시작되지 않았으면 exit code는 null, 시작됐으면 실제 integer
- success 전용 gpu/runtime/checksum 필드가 없음

## 12. 오류 처리

### 12.1 Startup/환경 오류

| 상황 | 처리 |
|---|---|
| NVIDIA GPU 없음 | `FATAL/NVIDIA_GPU_NOT_FOUND`, 연결하지 않고 non-zero 종료 |
| NVIDIA Driver/NVML 불가 | `FATAL/NVIDIA_DRIVER_UNAVAILABLE`, 연결하지 않고 종료 |
| Docker CLI 없음 | `SETUP_REQUIRED/DOCKER_NOT_INSTALLED` |
| Docker Engine 미실행/응답 없음 | `SETUP_REQUIRED/DOCKER_ENGINE_UNAVAILABLE` |
| WSL2/Linux container backend 아님 | `SETUP_REQUIRED/DOCKER_BACKEND_INVALID` |
| probe image pull 실패 | `SETUP_REQUIRED/GPU_PROBE_IMAGE_PULL_FAILED` |
| probe container GPU 접근 실패 | `SETUP_REQUIRED/DOCKER_GPU_UNAVAILABLE` |

### 12.2 Job 오류

| 상황 | Worker/Backend 처리 |
|---|---|
| 허용되지 않은 workload | 실행 없이 `FAILED/WORKLOAD_NOT_ALLOWED` |
| 같은 active jobId 재수신 | 두 번째 실행을 시작하지 않는 idempotent no-op |
| BUSY 중 다른 jobId EXECUTE | 두 번째 job에 `FAILED/WORKER_BUSY`, 기존 job 계속 |
| Docker 사용 불가 | `FAILED/DOCKER_UNAVAILABLE` |
| Docker Engine 미실행 | 자동 시작 없이 `FAILED/DOCKER_ENGINE_UNAVAILABLE` |
| Docker GPU 접근 불가 | `FAILED/DOCKER_GPU_UNAVAILABLE` |
| Docker Hub pull 실패/rate limit/auth | `FAILED/IMAGE_PULL_FAILED` |
| pull digest 불일치 | `FAILED/IMAGE_DIGEST_MISMATCH`, 실행 금지 |
| container create/start 실패 | `FAILED/CONTAINER_START_FAILED` |
| container timeout | stop/remove 후 `FAILED/CONTAINER_TIMEOUT` |
| non-zero exit | stderr 정제 후 `FAILED/CONTAINER_EXIT_NON_ZERO` |
| container `torch.cuda.is_available()==false` | `FAILED/CUDA_UNAVAILABLE` |
| matmul/검산 실패 | `FAILED/CUDA_TASK_FAILED` |
| stdout 초과/JSON schema 오류 | `FAILED/INVALID_CONTAINER_RESULT` |
| Worker 연결 중단 | container 취소/정리, Backend job `RESULT_UNKNOWN`, Worker 자동 reconnect 없음 |
| result 송신 실패 | local diagnostic result 보존, process non-zero 종료, Backend `RESULT_UNKNOWN` |

result 송신 실패는 같은 끊어진 socket에 FAILED message를 보낼 수 없으므로 성공으로 위장하지 않는다.
새 Worker process가 시작돼도 자동 replay하지 않으며 운영자는 새 jobId로 다시 요청한다.

## 13. 보안 설계

### 13.1 Image/command allowlist

- Backend가 전달할 수 있는 실행 선택자는 `workload`뿐이다.
- workload는 Worker에 내장된 고정 catalog와 정확히 일치해야 한다.
- image는 repository와 digest를 함께 고정하며 tag만 신뢰하지 않는다.
- entrypoint, arguments, security flags, timeout, output limit은 catalog code가 결정한다.
- Backend JSON의 알 수 없는 필드는 거부한다.
- shell, eval, arbitrary Python, host mount, Docker socket, privileged container를 허용하지 않는다.
- container output은 신뢰하지 않고 size/schema/digest/job correlation을 검증한다.

### 13.2 인터넷 transport 최소 보안

다른 인터넷 환경의 실제 검증은 `wss://`만 사용하고 Backend에는 유효한 TLS certificate를 적용한다.
Worker installer에서 발급한 enrollment token을 WebSocket HTTP `Authorization: Bearer` header로 보내며
Backend는 token과 허용 workerId를 대조한다. token은 JSON payload, URL query 또는 로그에 넣지 않는다.

Job REST API도 외부에 익명 공개하지 않고 PoC operator credential 또는 보호된 관리 network 뒤에서만
접근한다. credential 저장/회전과 production-grade IAM은 후속 단계이지만, 인터넷에 인증 없는 EXECUTE
endpoint를 노출하는 구성은 STEP 2.5 PASS로 인정하지 않는다.

## 14. 자동 테스트 계획

### 14.1 Backend

1. EXECUTE_RESULT success/failure 역직렬화 및 모든 필드 경계
2. 알 수 없는/추가 protocol field 거부
3. POST job의 ONLINE, IDLE, CUDA, supported workload 검증
4. 같은 Worker에 동시 POST 시 하나만 원자 reserve
5. EXECUTE가 reserve된 active session으로만 전송됨
6. send 실패 시 reservation 해제와 `RESULT_UNKNOWN`
7. 같은 jobId result correlation 및 terminal 결과 저장
8. stale session/다른 Worker/unknown job result 거부
9. terminal result 중복 반영 방지
10. 성공/실패 result 시 activeJobId 제거와 IDLE 전환
11. job 중 disconnect/timeout 시 OFFLINE, BUSY 보존, `RESULT_UNKNOWN`
12. job 중 last-connection-wins 후 session-A의 disconnect/result가 session-B에 영향 없음
13. GET job 상세와 unknown job 404
14. 기존 STEP 2 REGISTER/HEARTBEAT/15초 Clock 경계 테스트 전체 회귀

timeout 테스트는 기존 주입 가능한 Clock을 계속 사용하고 실제 15초 sleep을 사용하지 않는다.

### 14.2 Python Worker

1. Environment Manager의 READY/SETUP_REQUIRED/FATAL decision table
2. Docker CLI 없음, Engine 미실행, backend invalid 원인 분류
3. 고정 probe digest pull 및 `--gpus all` 검사 성공/실패
4. 환경 불충족 시 WebSocket connector 미호출
5. catalog lookup과 unknown workload 거부
6. EXECUTE에 image/command 등 추가 필드가 있으면 거부
7. IDLE->BUSY->IDLE 정상/실패 전이
8. active same jobId 중복은 한 번만 실행
9. BUSY 중 다른 jobId는 WORKER_BUSY이고 첫 job은 유지
10. heartbeat가 Docker pull/run 중에도 BUSY로 계속 생성
11. single outbound writer가 heartbeat/result를 직렬화
12. connection loss가 container cancel/cleanup을 호출
13. result send 실패 local result 보존 및 non-zero 종료, reconnect/replay 없음
14. Docker 명령이 shell 없이 exact argument list로 생성됨
15. image cache hit이면 pull 미호출
16. cache miss이면 digest pull 후 검증
17. pull/start/timeout/non-zero/CUDA/result parsing 오류 mapping
18. stdout/stderr cap과 secret redaction
19. 기존 STEP 1/2 Python 테스트 전체 회귀

모든 OS/Docker subprocess, filesystem, Clock/wait 및 WebSocket connector를 주입 가능한 adapter로 만들어
자동 테스트에서 실제 Docker 설치/변경, 실제 300초 timeout 또는 실제 network를 사용하지 않는다.

### 14.3 Runtime image

1. image build 성공 및 `gpu_task.py`만 허용된 entrypoint로 실행
2. CPU-only test double에서 CUDA unavailable structured failure
3. matrix size 계산의 VRAM 경계와 20% budget
4. checksum format, finite/sample validation, output schema
5. timeout/output cap과 non-zero exit 전달
6. vulnerability scan과 SBOM 생성

실제 CUDA 성공은 mock으로 PASS 처리하지 않고 15절의 RTX 4070 통합 테스트에서 확인한다.

## 15. 서로 다른 인터넷 환경의 실제 통합 테스트

### 15.1 준비

- Network A의 Backend를 public HTTPS endpoint 뒤에 배치하고 `/ws/workers`를 WSS로 upgrade한다.
- Network B의 RTX 4070 Laptop GPU PC는 outbound 443만 사용한다. Worker PC에 inbound port forwarding을
  만들지 않는다.
- 플랫폼 CI가 `GPU_PYTORCH_TEST` image를 Docker Hub에 push하고 Worker catalog에 실제 digest를 고정한다.
- Backend/Worker clock과 로그 timestamp를 UTC로 맞춘다.
- GPU PC에서 해당 digest가 없는 상태를 `docker image inspect` 결과로 먼저 기록한다. 기존 unrelated
  image나 Docker 설치를 제거하지 않는다.

### 15.2 최초 실행: pull 검증

1. Backend를 실행하고 GET workers가 대상 Worker를 ONLINE으로 아직 표시하지 않는지 확인한다.
2. 공급자는 Worker만 실행한다.
3. Worker가 host/Docker를 자동 검사·준비하고 REGISTER하는지 확인한다.
4. GET workers에서 실제 RTX 4070, `ONLINE/IDLE`, `GPU_PYTORCH_TEST`를 확인한다.
5. Backend operator가 POST jobs로 해당 workerId에 job을 제출한다.
6. Worker 로그에서 EXECUTE 수신, `BUSY`, cache miss, digest pull을 확인한다.
7. 별도 `nvidia-smi -l 1` 또는 dmon으로 container process GPU memory/utilization을 관찰한다.
8. container 안의 CUDA true, 실제 matrix size/iterations/runtime/checksum과 exit code 0을 확인한다.
9. GET job에서 동일 jobId의 SUCCESS와 Worker의 IDLE 복귀를 확인한다.
10. local image RepoDigest가 catalog digest와 같은지 확인한다.

### 15.3 두 번째 실행: cache 검증

1. 같은 Worker에 새 jobId로 같은 workload를 제출한다.
2. Docker Manager가 local digest를 찾고 pull command를 호출하지 않았음을 audit log로 확인한다.
3. 결과의 `imageCacheHit=true`, exit code 0, 유효한 checksum/runtime, 동일 imageDigest를 확인한다.
   matrix size와 input이 같으면 deterministic checksum도 첫 실행과 일치해야 한다.
4. Docker Hub 전체 layer download가 다시 발생하지 않았음을 확인한다.

### 15.4 연결/실패 검증

- job 중 Worker network를 차단해 Backend가 15초 timeout 후 OFFLINE과 `RESULT_UNKNOWN`을 기록하는지,
  Worker가 container를 정리하고 자동 reconnect하지 않는지 확인한다.
- unknown workload와 BUSY 중 두 번째 job은 실제 container를 시작하지 않는지 확인한다.
- Docker Engine stop, pull URL 차단, GPU runtime unavailable 같은 파괴적 환경 시험은 주 GPU 노트북을
  훼손하지 않고 disposable Windows GPU test machine 또는 복원 가능한 VM snapshot에서 수행한다.
- Docker CLI 없음, Engine 미실행 및 container GPU 접근 실패는 test double과 disposable Windows
  환경에서 SETUP_REQUIRED 및 connector 미호출을 검증한다. 현재 검증 노트북의 기존 Docker를 제거하는
  방식은 사용하지 않는다.

통합 증거에는 Backend/Worker timestamp, jobId, Worker session ID, image digest/cacheHit, container ID,
GPU model, matrix size/iterations, runtime, checksum, exit code, peak GPU utilization/memory와 최종
Worker/job 상태를 기록한다. token, public IP, Docker credential은 기록하지 않는다.

## 16. STEP 2.5 PASS 기준

- [ ] 기존 STEP 1/2 자동 테스트 전체 PASS
- [ ] Spring Boot Backend와 Python Worker 신규 자동 테스트 전체 PASS
- [ ] Windows Worker 배포본 설치 후 공급자 수동 pip/Python 설정 불필요
- [ ] NVIDIA GPU/Driver, Docker CLI/Engine, container GPU 접근 자동 검사
- [ ] Docker/Engine/container GPU 조건 미충족 시 local SETUP_REQUIRED이며 ONLINE 등록되지 않음
- [ ] Docker Desktop/WSL2 설치, Engine 시작, 관리자 권한 및 재부팅을 Worker가 수행하지 않음
- [ ] 서로 다른 인터넷 환경에서 WSS outbound Worker 연결
- [ ] REGISTER 후 `connectionStatus=ONLINE`, `workerStatus=IDLE`
- [ ] Backend가 명시적 workerId에 Remote Job 제출
- [ ] Worker가 EXECUTE 수신 후 `BUSY`
- [ ] 허용되지 않은 workload/image/command 실행 불가
- [ ] 승인 image cache miss 시 digest 기반 자동 pull
- [ ] hardened container 자동 실행 및 정리
- [ ] container 안 `torch.cuda.is_available()==true`
- [ ] 실제 CUDA tensor matrix multiplication, synchronize, 검산, checksum 생성
- [ ] container exit code 0과 schema-valid 결과
- [ ] Worker가 같은 jobId의 EXECUTE_RESULT SUCCESS 전송
- [ ] Backend가 같은 jobId의 SUCCESS 결과 저장/조회
- [ ] Worker가 IDLE로 복귀
- [ ] 두 번째 실행에서 pull 생략, `imageCacheHit=true`, local layer cache 재사용
- [ ] job 중 heartbeat가 5초마다 계속되고 기존 15초 liveness가 유지
- [ ] disconnect/timeout/last-connection-wins에서 stale session이 새 session/job을 변경하지 않음
- [ ] 12절의 최소 실패 상황이 모두 결정적인 자동 또는 격리 환경 테스트로 검증됨
- [ ] 실제 RTX 4070의 GPU utilization과 GPU memory 사용을 외부 도구로 관찰

한 항목이라도 충족하지 못하면 STEP 2.5를 PASS로 표시하지 않는다.

## 17. 공식 근거

- Docker Desktop Windows 설치/권한/WSL2 요구사항:
  <https://docs.docker.com/desktop/setup/install/windows-install/>
- Docker Desktop Windows GPU 지원은 WSL2 backend 전용:
  <https://docs.docker.com/desktop/features/gpu/>
- Microsoft WSL 설치와 재부팅 요구 가능성:
  <https://learn.microsoft.com/en-us/windows/wsl/install>
- Docker image digest pinning과 content-addressable layer cache:
  <https://docs.docker.com/reference/cli/docker/image/pull/>
- Docker run의 `--gpus`, `--rm`, `--read-only`, capability/security option:
  <https://docs.docker.com/reference/cli/docker/container/run>
- Docker Hub pull/auth 제한:
  <https://docs.docker.com/docker-hub/usage/pulls/>

## 18. 설계 자체 검토 결과

- Placeholder: 배포 시 결정될 Docker Hub repository와 digest는 Backend 입력이 아니라 push 후 source에
  고정되는 release artifact로 정의했다.
- 일관성: setup 상태는 기존 connectionStatus에 추가하지 않고 READY일 때만 REGISTER하여 STEP 2의
  ONLINE/OFFLINE 의미를 보존했다.
- 원자성: active session, active job reservation, timeout/disconnect/result의 session identity 규칙을
  worker 단위 원자 연산에 포함하고 socket/Docker I/O는 그 밖으로 분리했다.
- 보안: Backend는 workload ID 외 실행 세부사항을 보내지 못하며 image digest, command, runtime option은
  local fixed catalog만 결정한다. signature는 MVP 범위에서 제외하고 lookup 경계만 분리했다.
- 실행성: fixed probe image로 REGISTER 전 container GPU 접근을 검증하고 workload image는 첫 job까지
  pull하지 않아 cache miss/hit 요구와 양립시켰다.
- 실패 의미: result가 도착하지 않은 job은 성공/실패를 추측하지 않고 `RESULT_UNKNOWN`으로 처리한다.
- 범위: LoRA, scheduler, chunking, retry/reconnect, persistence 및 임의 사용자 workload는 포함하지 않았다.
