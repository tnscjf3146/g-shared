# STEP 2.5 Remote GPU Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 서로 다른 인터넷 환경의 Backend가 특정 Windows GPU Worker에 고정된 `GPU_PYTORCH_TEST`를 요청하고, Worker가 allowlisted Docker image를 pull/run하여 실제 PyTorch CUDA 결과를 같은 jobId로 반환하는 흐름을 구현·검증한다.

**Architecture:** 기존 STEP 2 raw WebSocket과 WorkerRegistry를 확장하되 Backend는 workload ID만 전송한다. Worker는 내장된 immutable image allowlist를 Environment Manager, Docker Manager, Job Manager 경계로 실행하며, Backend는 명시적 workerId job dispatch와 in-memory terminal result만 관리한다.

**Tech Stack:** Java 21, Spring Boot 4.1.1, Gradle 9.3.1, Python 3.12, `websockets` 17.1, `nvidia-ml-py` 13.610.43, Docker Desktop WSL2, PyTorch 2.12.1 CUDA 12.6 runtime image, JUnit 5, Python `unittest`

**Spec:** `docs/superpowers/specs/2026-08-27-step2-5-remote-gpu-execution-design.md`

## Global Constraints

- STEP 1과 STEP 2는 PASS 상태를 유지하고 기존 5초 heartbeat, 15초 Clock 기반 timeout, last-connection-wins를 회귀시켜서는 안 된다.
- Worker가 Backend에 outbound connection을 만들며 Backend는 Worker PC에 접속하지 않는다.
- Backend EXECUTE에는 `jobId`와 `workload`만 포함한다. image, command, args, env, mount, URL을 추가하지 않는다.
- `GPU_PYTORCH_TEST` image와 startup GPU probe image는 Worker source에 repository@digest로 고정한다.
- Catalog cryptographic signature와 remote update는 구현하지 않는다. lookup interface만 독립 module로 둔다.
- Docker Desktop/WSL2 설치, Docker Engine 시작, 관리자 권한 처리, UAC 및 재부팅 자동화는 구현하지 않는다.
- 환경 미충족 시 Worker는 local `SETUP_REQUIRED` 원인과 전용 exit code를 표시하고 WebSocket connector를 호출하지 않는다.
- Docker/Python/CUDA 명령은 공급자가 직접 실행하지 않는다. 플랫폼 운영자와 Worker만 실행한다.
- Docker subprocess는 shell 없이 argument list로 호출한다.
- Scheduler, retry/reconnect, chunking, LoRA, model/dataset/cache, P2P, Blockchain, persistence를 추가하지 않는다.
- Git commit과 Git push는 사용자 요청 전 수행하지 않는다. Task 4의 Docker Hub image push는 Git push와 별개이며, 명시적으로 인증된 플랫폼 Docker Hub namespace에만 수행한다.
- 각 구현 task는 failing test → 최소 구현 → targeted test → 관련 전체 회귀 순서로 진행한다.

## File Map

```text
runtime-images/gpu-pytorch-poc/
  Dockerfile                         # CUDA/PyTorch runtime image
  .dockerignore
  gpu_task.py                        # 실제 CUDA matmul과 JSON result
  README.md                          # 플랫폼 build/push/run 절차
  tests/test_gpu_task.py             # 순수 계산 정책/결과 schema 테스트

backend/src/main/java/com/gpucompute/poc/backend/
  api/JobController.java
  api/JobRequest.java
  api/JobResponse.java
  config/ApprovedWorkloadPolicy.java
  config/JobSecurityConfig.java
  domain/JobStatus.java
  job/InMemoryJobRegistry.java
  job/JobRecord.java
  job/JobService.java
  protocol/ExecuteMessage.java
  protocol/ExecuteResultMessage.java
  protocol/ExecuteResultStatus.java
  protocol/WorkerMessageDecoder.java
  protocol/WorkerMessageType.java
  protocol/WorkerMessageValidator.java
  registry/WorkerRecord.java
  registry/WorkerRegistry.java
  registry/JobReservationResult.java
  registry/JobCompletionResult.java
  websocket/WorkerMessageSender.java
  websocket/WorkerWebSocketHandler.java

worker/
  gpu-worker-agent.spec              # self-contained Windows executable recipe
  scripts/build-agent.ps1            # repeatable Windows bundle build
  gpu_worker/
    environment.py                   # startup readiness decision
    workloads.py                     # fixed workload/probe allowlist
    docker_manager.py                # Docker inspect/pull/run/cleanup
    job_manager.py                   # 단일 job lifecycle
    job_result_store.py              # local terminal result diagnostic
    protocol.py                      # EXECUTE decode/result encode
    transport.py                     # receiver/heartbeat/single writer
    messages.py                      # 동적 workerStatus heartbeat
    client.py                        # environment gate 및 component wiring
    errors.py                        # setup/docker/job error types
```

---

### Task 1: `gpu-pytorch-poc` Dockerfile 및 CUDA task 작성

**Files:**

- Create: `runtime-images/gpu-pytorch-poc/Dockerfile`
- Create: `runtime-images/gpu-pytorch-poc/.dockerignore`
- Create: `runtime-images/gpu-pytorch-poc/gpu_task.py`
- Create: `runtime-images/gpu-pytorch-poc/tests/test_gpu_task.py`
- Create: `runtime-images/gpu-pytorch-poc/README.md`

**Interfaces:**

- `select_matrix_size(free_vram_bytes: int) -> int`: 세 FP32 matrix 합계가 free VRAM 20% 이하가 되도록 2048~8192를 반환하고 2048도 불가능하면 `GpuTaskError`를 발생시킨다.
- `run_cuda_task(torch_module, iterations: int = 8) -> TaskResult`: CUDA 확인, tensor 생성, matmul, synchronize, sample 검산, SHA-256 생성.
- `TaskResult.to_payload() -> dict[str, object]`: `schemaVersion`, `status`, `gpuModel`, `runtimeMs`, `resultChecksum`, `matrixSize`, `iterations` 반환.
- stdout 마지막 줄: `GPU_TASK_RESULT=` 뒤에 compact JSON 한 개.

- [ ] **Step 1: matrix size와 result schema failing tests 작성**

  `test_gpu_task.py`에 다음 경계를 명시한다.

  Test methods: `test_select_matrix_size_caps_at_8192`,
  `test_select_matrix_size_respects_twenty_percent_budget`,
  `test_select_matrix_size_rejects_unsafe_vram`,
  `test_success_payload_has_sha256_checksum_and_positive_runtime`,
  `test_cuda_unavailable_returns_nonzero_structured_failure`.

- [ ] **Step 2: failing test 확인**

  Run:

  ```powershell
  cd C:\gpu-compute-poc\runtime-images\gpu-pytorch-poc
  C:\Users\SSAFY\miniforge3\python.exe -m unittest discover -s tests -v
  ```

  Expected: `gpu_task` module 또는 정의된 symbol 부재로 FAIL.

- [ ] **Step 3: 최소 CUDA workload 구현**

  deterministic CUDA input, 8회 matmul, 시작/종료 `torch.cuda.synchronize()`, finite/sample 검증,
  canonical sample bytes SHA-256를 구현한다. exception은 secret/traceback을 stdout result에 포함하지 않고
  `CUDA_UNAVAILABLE`, `CUDA_TASK_FAILED` 중 하나의 structured failure로 변환한다.

- [ ] **Step 4: Dockerfile 작성**

  최초 base는 실제 존재가 확인된 `pytorch/pytorch:2.12.1-cuda12.6-cudnn9-runtime`을 사용한다.
  `/opt/gpu-task/gpu_task.py`만 copy하고 numeric non-root user, `PYTHONDONTWRITEBYTECODE=1`, JSON
  entrypoint를 설정한다. compiler, dataset, model, Docker CLI를 추가하지 않는다.

- [ ] **Step 5: pure tests 통과 확인**

  Run: Task 1 Step 2와 동일.

  Expected: 모든 runtime unit test PASS.

**완료 조건:** image source가 실제 CUDA matmul, 동기화, 검산, checksum과 strict JSON result를 정의하며 host unit test가 PASS한다.

---

### Task 2: 로컬 Docker build 및 base digest 고정

**Files:**

- Modify: `runtime-images/gpu-pytorch-poc/Dockerfile`
- Modify: `runtime-images/gpu-pytorch-poc/README.md`

**Interfaces:** Task 1의 Docker build context와 `gpu-pytorch-poc:0.1-local` tag를 생성한다.

- [ ] **Step 1: Docker/Engine 상태 확인**

  ```powershell
  docker version
  docker info --format '{{.OSType}}'
  ```

  Expected: client/server 모두 응답하고 OSType은 `linux`. 실패하면 설치/시작을 자동화하지 않고 작업을 중단해 원인을 보고한다.

- [ ] **Step 2: base image full digest 확보**

  ```powershell
  docker pull pytorch/pytorch:2.12.1-cuda12.6-cudnn9-runtime
  docker image inspect pytorch/pytorch:2.12.1-cuda12.6-cudnn9-runtime --format '{{index .RepoDigests 0}}'
  ```

  출력된 `pytorch/pytorch@sha256:` 전체 값을 Dockerfile `FROM`에 고정한다. 짧은 UI digest나 tag만 남기지 않는다.

- [ ] **Step 3: local image build**

  ```powershell
  cd C:\gpu-compute-poc
  docker build --pull=false --tag gpu-pytorch-poc:0.1-local runtime-images/gpu-pytorch-poc
  ```

  Expected: exit code 0.

- [ ] **Step 4: image metadata 검사**

  ```powershell
  docker image inspect gpu-pytorch-poc:0.1-local --format '{{json .Config.Entrypoint}} {{.Config.User}} {{.Size}}'
  ```

  Expected: 고정 Python entrypoint, non-root numeric user, 기록 가능한 image size.

- [ ] **Step 5: image 내부 CPU/CUDA failure schema smoke test**

  `--gpus` 없이 실행하여 non-zero이면서 마지막 stdout marker가 `CUDA_UNAVAILABLE` structured result인지 확인한다.

**완료 조건:** full base digest가 Dockerfile에 고정되고 local image build/metadata/failure schema 검사가 통과한다.

---

### Task 3: `docker run --gpus all` 실제 CUDA 검증

**Files:**

- Modify: `runtime-images/gpu-pytorch-poc/README.md`
- Create during validation: `docs/step2-5-image-validation.md`

**Interfaces:** Task 2 image가 실제 RTX 4070에서 `GPU_TASK_RESULT` SUCCESS를 출력한다.

- [ ] **Step 1: 외부 GPU 관찰 시작**

  별도 terminal에서 `nvidia-smi -l 1` 또는 `nvidia-smi dmon -s pucm -d 1`을 실행한다.

- [ ] **Step 2: hardened container 실행**

  ```powershell
  docker run --rm --gpus all --network none --read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 --memory 4g --tmpfs /tmp:rw,noexec,nosuid,size=64m gpu-pytorch-poc:0.1-local
  ```

  Expected: exit code 0, `torch.cuda.is_available=true`, RTX 4070 model, matrix size 2048~8192,
  runtimeMs > 0, 64자리 checksum.

- [ ] **Step 3: 결과를 독립 검증**

  stdout 마지막 marker의 JSON을 parse하여 schemaVersion 1, SUCCESS, exit 0과 field boundary를 확인한다.

- [ ] **Step 4: GPU 사용 증거 기록**

  `docs/step2-5-image-validation.md`에 image ID, PyTorch/CUDA version, GPU model, matrix size,
  iterations, runtime, checksum, peak utilization/memory를 기록한다.

**완료 조건:** 단순 utilization이 아니라 container 내부 CUDA matmul/검산/checksum이 성공하고 외부 GPU 사용 증거가 기록된다.

---

### Task 4: Docker Hub image push

**Files:**

- Modify: `runtime-images/gpu-pytorch-poc/README.md`
- Modify: `docs/step2-5-image-validation.md`
- Create: `docs/step2-5-image-sbom.spdx.json`
- Create: `docs/step2-5-image-vulnerabilities.sarif`

**External prerequisite:** 플랫폼 운영자가 Docker Hub에 로그인하고 `GPU_POC_DOCKERHUB_ACCOUNT` 환경 변수에 승인된 namespace를 설정한다. 빈 값, 개인 임시 namespace 또는 인증되지 않은 namespace에는 push하지 않는다.

**Interfaces:** local `gpu-pytorch-poc:0.1-local`을 승인 namespace의 `gpu-pytorch-poc:0.1`로 push하고 remote linux/amd64 manifest digest를 다음 task에 제공한다.

- [ ] **Step 1: repository 값 검증**

  ```powershell
  if ([string]::IsNullOrWhiteSpace($env:GPU_POC_DOCKERHUB_ACCOUNT)) { throw 'GPU_POC_DOCKERHUB_ACCOUNT is required' }
  $imageRepository = "$env:GPU_POC_DOCKERHUB_ACCOUNT/gpu-pytorch-poc"
  docker info
  ```

- [ ] **Step 2: vulnerability report 생성**

  ```powershell
  docker scout cves --only-severity critical,high --format sarif --output docs/step2-5-image-vulnerabilities.sarif gpu-pytorch-poc:0.1-local
  ```

  발견 항목 수와 remediation 가능 여부를 validation 문서에 기록한다. platform-managed image로서
  수용할 수 없는 critical issue가 있으면 push하지 않고 base image를 수정해 Task 2부터 재검증한다.

- [ ] **Step 3: SBOM 생성**

  ```powershell
  docker scout sbom --format spdx --output docs/step2-5-image-sbom.spdx.json gpu-pytorch-poc:0.1-local
  ```

  Expected: exit code 0과 non-empty SPDX document.

- [ ] **Step 4: immutable release tag 부여**

  ```powershell
  docker tag gpu-pytorch-poc:0.1-local "$imageRepository`:0.1"
  ```

- [ ] **Step 5: Docker Hub push**

  ```powershell
  docker push "$imageRepository`:0.1"
  ```

  Expected: exit code 0과 registry digest 출력.

- [ ] **Step 6: remote manifest 확인**

  ```powershell
  docker buildx imagetools inspect "$imageRepository`:0.1"
  ```

  Expected: linux/amd64 manifest와 full `sha256:` digest.

**완료 조건:** vulnerability/SBOM artifact가 생성되고 승인된 Docker Hub repository의 0.1 tag와 remote manifest digest가 validation 문서에 기록된다.

---

### Task 5: Image digest 확인 및 Worker WorkloadCatalog 고정

**Files:**

- Create: `worker/gpu_worker/workloads.py`
- Create: `worker/tests/test_workloads.py`
- Modify: `backend/src/main/resources/application.yml`
- Modify: `docs/step2-5-image-validation.md`

**Interfaces:**

```python
@dataclass(frozen=True)
class WorkloadSpec:
    workload: str
    image: str
    timeout_seconds: int
    max_output_bytes: int

def get_workload_spec(workload: str) -> WorkloadSpec
def get_gpu_probe_image() -> str
```

- [ ] **Step 1: catalog failing tests 작성**

  Test methods: `test_gpu_pytorch_test_uses_repository_at_full_sha256_digest`,
  `test_unknown_workload_is_rejected`, `test_catalog_does_not_accept_backend_image_override`,
  `test_gpu_probe_image_is_fixed_by_digest`.

- [ ] **Step 2: FAIL 확인**

  ```powershell
  cd C:\gpu-compute-poc\worker
  .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_workloads.py" -v
  ```

- [ ] **Step 3: remote workload digest 재확인**

  Task 4의 `$imageRepository`:0.1을 pull한 후
  `docker image inspect "$imageRepository`:0.1" --format '{{join .RepoDigests "\n"}}'`로
  Docker Hub 출력과 일치하는 full digest를 선택한다.

- [ ] **Step 4: startup probe digest 확보**

  `nvidia/cuda:12.6.3-base-ubuntu22.04` linux/amd64 image를 pull하고 full RepoDigest를 얻는다. Worker에는
  tag가 아니라 그 exact `nvidia/cuda@sha256:` 값을 고정한다.

- [ ] **Step 5: fixed catalog 최소 구현**

  `GPU_PYTORCH_TEST`와 probe image를 module-level immutable mapping/value로 기록한다. Backend config에는
  result 교차검증용 workload digest만 같은 값으로 기록한다. signature, remote URL, runtime override를
  추가하지 않는다.

- [ ] **Step 6: targeted test 통과**

  Task 5 Step 2를 다시 실행한다.

**완료 조건:** Worker source에 workload/probe full digest가 고정되고 unknown workload/override가 테스트로 거부된다.

---

### Task 6: Backend Job API 및 outbound EXECUTE

**Files:**

- Create: `backend/src/main/java/com/gpucompute/poc/backend/api/JobController.java`
- Create: `backend/src/main/java/com/gpucompute/poc/backend/api/JobRequest.java`
- Create: `backend/src/main/java/com/gpucompute/poc/backend/api/JobResponse.java`
- Create: `backend/src/main/java/com/gpucompute/poc/backend/domain/JobStatus.java`
- Create: `backend/src/main/java/com/gpucompute/poc/backend/config/ApprovedWorkloadPolicy.java`
- Create: `backend/src/main/java/com/gpucompute/poc/backend/job/JobRecord.java`
- Create: `backend/src/main/java/com/gpucompute/poc/backend/job/InMemoryJobRegistry.java`
- Create: `backend/src/main/java/com/gpucompute/poc/backend/job/JobService.java`
- Create: `backend/src/main/java/com/gpucompute/poc/backend/protocol/ExecuteMessage.java`
- Create: `backend/src/main/java/com/gpucompute/poc/backend/registry/JobReservationResult.java`
- Create: `backend/src/main/java/com/gpucompute/poc/backend/websocket/WorkerMessageSender.java`
- Modify: `backend/src/main/java/com/gpucompute/poc/backend/registry/WorkerRecord.java`
- Modify: `backend/src/main/java/com/gpucompute/poc/backend/registry/WorkerRegistry.java`
- Test: `backend/src/test/java/com/gpucompute/poc/backend/api/JobControllerTest.java`
- Test: `backend/src/test/java/com/gpucompute/poc/backend/job/JobServiceTest.java`
- Test: `backend/src/test/java/com/gpucompute/poc/backend/registry/WorkerRegistryTest.java`

**Interfaces:**

```java
record ExecuteMessage(String type, UUID jobId, String workload) {}
JobReservationResult reserveJob(String workerId, UUID jobId, String workload)
JobRecord dispatch(String workerId, String workload)
Optional<JobRecord> findById(UUID jobId)
String expectedImageDigest(String workload)
```

- [ ] **Step 1: API/Registry failing tests 작성**

  ONLINE/IDLE/supported worker의 202, unknown worker 404, OFFLINE/BUSY/unsupported workload 409,
  동시 두 reservation 중 하나만 성공, JSON에 image/command가 없음을 검증한다.

- [ ] **Step 2: targeted FAIL 확인**

  ```powershell
  cd C:\gpu-compute-poc\backend
  .\gradlew.bat test --tests '*JobControllerTest' --tests '*JobServiceTest' --tests '*WorkerRegistryTest' --no-daemon
  ```

- [ ] **Step 3: WorkerRecord 원자 reservation 구현**

  REST 미노출 `activeJobId`, `activeJobSessionId`를 추가한다. `ConcurrentHashMap.compute` 안에서 ONLINE,
  IDLE, active session, CUDA, workload, activeJobId null을 검사한다. WebSocket send는 compute 밖에서 한다.

- [ ] **Step 4: JobService와 single-session sender 구현**

  Backend가 UUID를 생성하고 exact active session으로 type, jobId, workload 세 field만 보낸다. 예시
  jobId는 `f271e9f5-0d9f-4fbe-bac1-8812647a02ba`이다. send 실패는 같은 session identity일 때만
  reservation을 해제하고 RESULT_UNKNOWN으로 저장한다.

- [ ] **Step 5: POST/GET API 구현**

  `POST /api/jobs`, `GET /api/jobs/{jobId}`만 추가한다. Scheduler나 Worker 선택 로직을 넣지 않는다.
  `ApprovedWorkloadPolicy`는 application.yml의 고정 workload/digest map을 읽어 알려진 workload만 허용한다.

- [ ] **Step 6: targeted 및 STEP 2 Backend 회귀**

  Step 2 명령 후 `\.\gradlew.bat test --no-daemon`을 실행한다.

**완료 조건:** 명시적 workerId job만 원자 reserve되고 active session으로 image 정보 없는 EXECUTE가 전송된다.

---

### Task 7: Worker EnvironmentManager와 REGISTER gate

**Files:**

- Create: `worker/gpu_worker/environment.py`
- Create: `worker/tests/test_environment.py`
- Modify: `worker/gpu_worker/client.py`
- Modify: `worker/gpu_worker/errors.py`
- Modify: `worker/gpu_worker/runtime.py`
- Modify: `worker/README.md`

**Interfaces:**

```python
class SetupStatus(Enum): READY = "READY"; SETUP_REQUIRED = "SETUP_REQUIRED"; FATAL = "FATAL"
@dataclass(frozen=True)
class EnvironmentReport: status: SetupStatus; error_code: str | None; message: str
class DockerEnvironmentProbe(Protocol):
    async def inspect(self) -> EnvironmentReport
class EnvironmentManager:
    async def inspect(self) -> EnvironmentReport
```

- [ ] **Step 1: readiness decision failing tests 작성**

  NVIDIA GPU/Driver 실패, Docker CLI 없음, Engine 미응답, Linux backend 아님, GPU probe 실패가 각각
  local 원인으로 매핑되고 connector가 호출되지 않는지 검증한다. READY에서만 connector 1회 호출을
  검증한다.

- [ ] **Step 2: FAIL 확인**

  ```powershell
  cd C:\gpu-compute-poc\worker
  .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_environment.py" -v
  .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_client.py" -v
  ```

- [ ] **Step 3: EnvironmentManager 최소 구현**

  기존 NVML snapshot을 재사용하고 DockerEnvironmentProbe 결과를 조합한다. 어떤 실패에서도 installer,
  service start, UAC 또는 reboot command를 실행하지 않는다. STEP 2.5 Agent용 provider는 host PyTorch를
  import하지 않고 NVML/Driver와 성공한 container GPU probe를 근거로 `cudaAvailable=true`를 만든다.
  기존 STEP 1/2 `CudaGpuStatusProvider`는 변경 없이 유지한다.

- [ ] **Step 4: client startup gate 구현**

  READY 전에 WebSocket을 열지 않는다. SETUP_REQUIRED는 console과 Worker data directory의
  `setup-status.json`에 원자 기록하고 전용 exit code 10으로 종료한다. FATAL은 exit code 2를 유지한다.

- [ ] **Step 5: tests 통과**

  Task 7 Step 2와 전체 Python unittest를 실행한다.

**완료 조건:** 모든 필수 환경이 READY일 때만 REGISTER 경로에 진입하고 미충족 원인이 local에 결정적으로 표시된다.

---

### Task 8: Worker DockerManager

**Files:**

- Create: `worker/gpu_worker/docker_manager.py`
- Create: `worker/tests/test_docker_manager.py`
- Modify: `worker/gpu_worker/environment.py`
- Modify: `worker/gpu_worker/errors.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class ImageResolution: image: str; cache_hit: bool
@dataclass(frozen=True)
class ContainerExecution: exit_code: int; stdout: str; stderr: str
class DockerManager:
    async def inspect_environment(self) -> EnvironmentReport
    async def ensure_image(self, spec: WorkloadSpec) -> ImageResolution
    async def run(self, job_id: UUID, spec: WorkloadSpec) -> ContainerExecution
    async def cancel_active(self) -> None
```

- [ ] **Step 1: exact command/error failing tests 작성**

  CLI/Engine probe, fixed probe image pull/run, workload inspect cache hit, cache miss digest pull, digest mismatch,
  hardened run argument list, timeout cleanup, output 1 MiB cap, non-zero exit mapping을 검증한다.

- [ ] **Step 2: FAIL 확인**

  ```powershell
  cd C:\gpu-compute-poc\worker
  .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_docker_manager.py" -v
  ```

- [ ] **Step 3: subprocess adapter와 environment probe 구현**

  `asyncio.create_subprocess_exec`만 사용한다. `docker version`, `docker info`, probe digest inspect/pull,
  fixed security options와 probe digest를 넣은 `docker run --rm --gpus all`의 마지막 command
  `nvidia-smi`를 실행하며 Engine을 시작하지 않는다.

- [ ] **Step 4: image ensure와 hardened run 구현**

  workload digest inspect 성공 시 pull을 생략한다. miss이면 exact digest만 pull하고 RepoDigest를 재검증한다.
  run에는 `--rm --gpus all --network none --read-only --cap-drop ALL --security-opt no-new-privileges
  --pids-limit 128 --memory 4g --tmpfs /tmp:rw,noexec,nosuid,size=64m`만 사용한다.

- [ ] **Step 5: cleanup 구현**

  UUID 기반 label/name을 사용해 timeout, cancellation, connection loss의 `finally`에서 exact active
  container만 stop/remove한다. broad prune를 사용하지 않는다.

- [ ] **Step 6: targeted tests 통과**

  Task 8 Step 2를 다시 실행한다.

**완료 조건:** DockerManager가 fixed digest만 inspect/pull/run하고 모든 실패에서 bounded output과 exact cleanup을 보장한다.

---

### Task 9: Worker JobManager와 bidirectional transport

**Files:**

- Create: `worker/gpu_worker/protocol.py`
- Create: `worker/gpu_worker/job_manager.py`
- Create: `worker/gpu_worker/job_result_store.py`
- Create: `worker/tests/test_protocol.py`
- Create: `worker/tests/test_job_manager.py`
- Modify: `worker/gpu_worker/transport.py`
- Modify: `worker/gpu_worker/messages.py`
- Modify: `worker/gpu_worker/client.py`
- Modify: `worker/tests/test_transport.py`
- Modify: `worker/tests/test_messages.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class ExecuteRequest: job_id: UUID; workload: str
class JobManager:
    def worker_status(self) -> str
    async def submit(self, request: ExecuteRequest) -> None
    async def shutdown(self) -> None
async def run_connection(config, source, job_manager, connector=connect) -> None
```

- [ ] **Step 1: protocol/job state failing tests 작성**

  exact EXECUTE schema, extra image/command 거부, IDLE→BUSY→IDLE, same active jobId no-op, BUSY 중 다른
  jobId WORKER_BUSY, unknown workload, Docker failure mapping, local result 기록을 검증한다.

- [ ] **Step 2: transport concurrency failing tests 작성**

  REGISTER가 먼저이며 receiver, 5초 heartbeat producer, single writer가 함께 동작하는지 확인한다.
  Docker 작업이 block돼도 BUSY heartbeat가 계속 queue에 들어가고 concurrent socket send가 없는지 검증한다.

- [ ] **Step 3: FAIL 확인**

  ```powershell
  cd C:\gpu-compute-poc\worker
  .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_protocol.py" -v
  .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_job_manager.py" -v
  .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_transport.py" -v
  .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_messages.py" -v
  ```

- [ ] **Step 4: strict decoder와 JobManager 최소 구현**

  UUID/workload/type 외 field를 거부한다. asyncio lock으로 단일 active job을 claim하고 DockerManager 결과를
  schema-valid EXECUTE_RESULT로 변환한다. 결과는 send queue 전에 `results/{jobId}.json`에 atomic replace로
  기록한다.

- [ ] **Step 5: transport를 receiver/heartbeat/writer/supervisor로 분리**

  모든 outbound frame은 하나의 queue/writer를 통과한다. connection loss는 active container cleanup을
  요청하고 자동 reconnect/replay 없이 non-zero 종료한다.

- [ ] **Step 6: targeted 및 Python 전체 회귀**

  Step 3 명령 후 `python -m unittest discover -s tests -v`와 `python -m pip check`를 실행한다.

**완료 조건:** Worker가 heartbeat를 멈추지 않고 EXECUTE 한 개를 처리하며 중복/연결 상실을 안전하게 처리한다.

---

### Task 10: EXECUTE_RESULT와 Backend terminal Job 상태

**Files:**

- Create: `backend/src/main/java/com/gpucompute/poc/backend/protocol/ExecuteResultMessage.java`
- Create: `backend/src/main/java/com/gpucompute/poc/backend/protocol/ExecuteResultStatus.java`
- Create: `backend/src/main/java/com/gpucompute/poc/backend/registry/JobCompletionResult.java`
- Modify: `backend/src/main/java/com/gpucompute/poc/backend/protocol/WorkerMessageType.java`
- Modify: `backend/src/main/java/com/gpucompute/poc/backend/protocol/WorkerMessageDecoder.java`
- Modify: `backend/src/main/java/com/gpucompute/poc/backend/protocol/WorkerMessageValidator.java`
- Modify: `backend/src/main/java/com/gpucompute/poc/backend/websocket/WorkerWebSocketHandler.java`
- Modify: `backend/src/main/java/com/gpucompute/poc/backend/registry/WorkerRegistry.java`
- Modify: `backend/src/main/java/com/gpucompute/poc/backend/job/InMemoryJobRegistry.java`
- Modify: `backend/src/main/java/com/gpucompute/poc/backend/job/JobService.java`
- Test: `backend/src/test/java/com/gpucompute/poc/backend/protocol/ExecuteResultMessageTest.java`
- Test: `backend/src/test/java/com/gpucompute/poc/backend/job/JobCompletionTest.java`
- Modify tests: Registry, handler, liveness, integration tests

**Interfaces:**

```java
JobCompletionResult completeJob(UUID jobId, String sessionId, ExecuteResult result)
void markResultUnknown(String workerId, String sessionId, UUID displacedJobId)
```

- [ ] **Step 1: success/failure validation tests 작성**

  SUCCESS 필수/금지 field, checksum 64 hex, exit code 0, positive runtime/matrix/iterations, expected digest,
  FAILED errorCode/message/nullable exit code와 extra field 거부를 검증한다.

- [ ] **Step 2: session/result race failing tests 작성**

  expected session result만 terminal 반영, stale/unknown/다른 worker result 무시 또는 protocol close,
  duplicate terminal result 미변경, result와 timeout 경합, session-A 교체 후 늦은 result가 B에 영향 없음,
  disconnect 시 RESULT_UNKNOWN을 검증한다.

- [ ] **Step 3: FAIL 확인**

  ```powershell
  cd C:\gpu-compute-poc\backend
  .\gradlew.bat test --tests '*ExecuteResultMessageTest' --tests '*JobCompletionTest' --tests '*WorkerRegistryTest' --tests '*WorkerWebSocketHandlerTest' --no-daemon
  ```

- [ ] **Step 4: decoder/validator/handler 구현**

  EXECUTE_RESULT를 inbound WorkerMessage로 decode하고 session binding을 JobService에 전달한다. Handler는
  job state를 직접 변경하지 않는다.

- [ ] **Step 5: 원자 completion과 displaced job 처리 구현**

  activeJobId/session 일치 시 job terminal 저장, activeJobId clear, workerStatus IDLE을 worker 단위로
  원자 전환한다. disconnect, timeout, last-connection-wins 결과가 반환한 displaced job만 RESULT_UNKNOWN으로
  만든다. socket close는 compute 밖에서 유지한다.

- [ ] **Step 6: targeted 및 Backend 전체 회귀**

  Step 3 명령 후 `\.\gradlew.bat clean test --no-daemon`을 실행한다.

**완료 조건:** Backend가 같은 jobId와 active session의 결과만 SUCCESS/FAILED로 확정하고 모든 연결 race에서 거짓 성공을 만들지 않는다.

---

### Task 11: 전체 자동 테스트, security gate 및 문서화

**Files:**

- Create: `backend/src/main/java/com/gpucompute/poc/backend/config/JobSecurityConfig.java`
- Create: `backend/src/test/java/com/gpucompute/poc/backend/config/JobSecurityConfigTest.java`
- Modify: `backend/src/main/resources/application.yml`
- Modify: `worker/gpu_worker/client.py`
- Modify: `worker/gpu_worker/transport.py`
- Modify: `worker/tests/test_client.py`
- Modify: `worker/tests/test_transport.py`
- Modify: `backend/README.md`
- Modify: `worker/README.md`
- Modify: `worker/pyproject.toml`
- Create: `worker/gpu-worker-agent.spec`
- Create: `worker/scripts/build-agent.ps1`
- Modify: `README.md`
- Modify: `docs/poc-plan.md`

**Interfaces:** internet deployment은 reverse proxy TLS와 operator/worker bearer secret을 사용하며 secret을 JSON/URL/log에 넣지 않는다.

- [ ] **Step 1: security failing tests 작성**

  `/api/jobs` 무인증 요청 거부, 올바른 operator token 허용, WebSocket handshake token 부재/불일치 거부,
  token과 REGISTER workerId 불일치 거부, secret 로그 미노출을 검증한다.

- [ ] **Step 2: 최소 security gate 구현**

  환경 변수로 주입된 PoC operator token과 workerId별 token hash를 검사한다. local test profile에는 고정
  test credential을 사용한다. Worker는 `GPU_WORKER_TOKEN` 환경 변수에서 credential을 읽어 WebSocket
  handshake `Authorization: Bearer` header에만 넣는다. URL, REGISTER JSON, log에는 넣지 않는다. WSS
  certificate termination은 reverse proxy가 담당한다.

- [ ] **Step 3: Backend fresh test**

  ```powershell
  cd C:\gpu-compute-poc\backend
  .\gradlew.bat clean test --no-daemon
  ```

  Expected: 신규/기존 전체 PASS, XML report failure/error 0.

- [ ] **Step 4: Python fresh test**

  ```powershell
  cd C:\gpu-compute-poc\worker
  .\.venv\Scripts\python.exe -m unittest discover -s tests -v
  .\.venv\Scripts\python.exe -m pip check
  ```

  Expected: 신규/기존 전체 PASS와 broken dependency 0.

- [ ] **Step 5: self-contained Windows Worker bundle 생성 및 smoke test**

  `worker/pyproject.toml`의 build-only extra에 `pyinstaller==6.22.2`를 고정하고 spec file이 `client.py`,
  `websockets`, `nvidia-ml-py`와 Worker modules를 one-directory bundle로 포함하게 한다. build script는
  개발 `.venv`에서 PyInstaller를 실행해 `dist/gpu-worker-agent/gpu-worker-agent.exe`를 만든다.
  Python이 없는 clean PowerShell 환경에서 `--help`와 SETUP_REQUIRED smoke test를 실행해 공급자가
  Python/venv/pip를 구성할 필요가 없음을 확인한다.

- [ ] **Step 6: runtime image tests/build 재검증**

  Task 1 unit test와 Task 2 build를 다시 실행하고 image ID가 catalog release와 대응하는지 확인한다.

- [ ] **Step 7: 제외 범위 및 문서 검사**

  `rg`로 scheduler, retry/reconnect, LoRA, arbitrary image/command field, Docker installer 실행 코드가
  추가되지 않았는지 확인한다. README에는 환경 사전조건과 SETUP_REQUIRED 원인을 명시하되 STEP 2.5를
  실제 E2E 전 PASS로 바꾸지 않는다.

**완료 조건:** Backend/Python/runtime 자동 테스트가 fresh PASS하고 인터넷 control plane 최소 인증과 범위 제한이 문서화된다.

---

### Task 12: 서로 다른 인터넷 환경의 실제 RTX 4070 E2E

**Files:**

- Create: `docs/step2-5-integration-validation.md`
- Modify after PASS only: `README.md`
- Modify after PASS only: `docs/poc-plan.md`

**Interfaces:** Network A Backend의 public WSS/HTTPS와 Network B RTX 4070 Worker의 outbound 443 연결.

- [ ] **Step 1: Network A Backend 배포**

  Java 21 Backend를 TLS reverse proxy 뒤에 실행하고 `/ws/workers` upgrade와 `/api/jobs` operator auth를
  확인한다. public endpoint, token 또는 IP는 validation 문서에 평문 기록하지 않는다.

- [ ] **Step 2: Network B 환경 preflight**

  Worker 실행 전 NVIDIA GPU/Driver, Docker CLI/Engine, probe image container GPU access가 READY인지 Worker
  자체 출력으로 확인한다. 공급자는 Docker 명령을 직접 실행하지 않는다.

- [ ] **Step 3: 실제 Worker REGISTER/heartbeat**

  Worker를 한 번 실행하고 Backend에서 RTX 4070, ONLINE/IDLE, GPU_PYTORCH_TEST, 5초 heartbeat 갱신을
  확인한다.

- [ ] **Step 4: Remote Job 제출**

  Backend operator가 `POST /api/jobs`로 worker-001/GPU_PYTORCH_TEST를 요청하고 반환된 UUID를 기록한다.

- [ ] **Step 5: GPU 실행과 결과 확인**

  Worker가 BUSY, Docker run, CUDA matmul, checksum, exit 0, EXECUTE_RESULT를 수행하는 동안 외부
  `nvidia-smi`에서 GPU utilization/memory를 관찰한다. GET job의 동일 UUID SUCCESS와 Worker IDLE을 확인한다.

- [ ] **Step 6: 연결 종료/timeout 검증**

  복원 가능한 시험에서 job 중 active connection을 차단하여 15초 후 OFFLINE, workerStatus BUSY 보존,
  job RESULT_UNKNOWN, container cleanup, no reconnect를 확인한다.

- [ ] **Step 7: 증거 기록**

  Backend/Worker UTC timestamp, session ID, jobId, image digest, GPU model, PyTorch/CUDA version, matrix size,
  iterations, runtime, checksum, exit code, peak utilization/memory, 최종 상태를 문서화한다.

**완료 조건:** mock이 아닌 서로 다른 network의 실제 RTX 4070 Worker가 동일 jobId SUCCESS를 반환하고 IDLE로 복귀한다.

---

### Task 13: 첫 job cache miss 및 두 번째 job cache hit 검증

**Files:**

- Modify: `docs/step2-5-integration-validation.md`
- Modify after all checklist PASS: `README.md`
- Modify after all checklist PASS: `docs/poc-plan.md`

**Interfaces:** 두 job은 같은 worker/workload/imageDigest를 사용하고 서로 다른 Backend UUID를 사용한다.

- [ ] **Step 1: exact workload image cache 정리**

  RTX 4070 PC에서 실행 중 container가 없음을 확인하고 Task 5에 고정된 exact workload digest만
  `docker image rm`으로 제거한다. probe image와 unrelated image는 제거하지 않는다. 이 image는 Docker
  Hub pull로 복구 가능하다.

- [ ] **Step 2: Worker 재시작과 REGISTER**

  EnvironmentManager는 별도 probe image로 READY를 검증하므로 workload image가 없어도 ONLINE/IDLE로
  REGISTER해야 한다.

- [ ] **Step 3: 첫 job cache miss**

  첫 UUID job을 제출하고 `imageCacheHit=false`, exact digest pull, CUDA SUCCESS, IDLE 복귀를 확인한다.
  Docker pull output과 local RepoDigest를 기록한다.

- [ ] **Step 4: 두 번째 job cache hit**

  Worker를 종료하지 않고 두 번째 UUID job을 제출한다. `docker pull` subprocess가 호출되지 않고
  `imageCacheHit=true`, 동일 imageDigest, CUDA SUCCESS, IDLE 복귀인지 확인한다.

- [ ] **Step 5: 최종 전체 회귀 및 checklist 판정**

  Task 11의 Backend/Python/runtime fresh test를 다시 실행한다. 설계 16절의 모든 항목을 증거와
  일대일 대조하고 하나라도 누락되면 STEP 2.5를 PASS로 표시하지 않는다.

- [ ] **Step 6: PASS일 때만 상태 문서 변경**

  모든 항목이 충족되면 README와 poc-plan을 `STEP 2.5: PASS`로 변경한다. STEP 3 코드는 작성하지 않는다.

**완료 조건:** 첫 job은 실제 pull을 수행하고 두 번째 job은 local digest/layer cache를 재사용하며 두 job 모두 같은 workload image에서 valid CUDA SUCCESS를 반환한다.

## Final Verification Commands

```powershell
cd C:\gpu-compute-poc\backend
.\gradlew.bat clean test --no-daemon

cd C:\gpu-compute-poc\worker
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check

cd C:\gpu-compute-poc\runtime-images\gpu-pytorch-poc
C:\Users\SSAFY\miniforge3\python.exe -m unittest discover -s tests -v

cd C:\gpu-compute-poc
git diff --check
git status --short --branch
```

최종 보고에는 자동 테스트 수, 실제 GPU/container 결과, 두 cache 판정, 실패 시나리오, 생성/수정 파일과
미완료 조건을 포함한다. Git commit/push는 수행하지 않는다.
