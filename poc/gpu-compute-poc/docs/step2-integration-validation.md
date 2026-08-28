# STEP 2 Worker-Backend Integration Validation

## 최종 판정

- 테스트 날짜: 2026-08-27 (Asia/Seoul)
- 판정: **STEP 2 PASS**
- STEP 3 상태: 시작하지 않음

## 환경

| 항목 | 결과 |
|---|---|
| OS | Windows 11, build 26200 |
| Java | OpenJDK 21.0.10 |
| Spring Boot | 4.1.1 |
| Gradle Wrapper | 9.3.1 |
| Python | 3.12.12, `worker/.venv` |
| PyTorch | 2.12.1+cu126 |
| PyTorch CUDA | 12.6 |
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU |
| VRAM | 8188 MiB |
| NVIDIA Driver | 596.36 |
| `torch.cuda.is_available()` | `True` |
| nvidia-ml-py | 13.610.43 |
| websockets | 17.1 |

## 자동 테스트

```text
Backend: .\gradlew.bat clean test --no-daemon
Result: BUILD SUCCESSFUL, 59 tests

Worker: .\.venv\Scripts\python.exe -m unittest discover -s tests -v
Result: OK, 19 tests (기존 STEP 1 테스트 9개 포함)

Worker: .\.venv\Scripts\python.exe -m pip check
Result: No broken requirements found.
```

15초 liveness 경계 테스트는 주입된 `Clock`으로 14초에는 ONLINE, 정확히 15초에는 OFFLINE을
결정적으로 확인한다. 실제 15초 sleep은 사용하지 않았다. heartbeat와 timeout 동시 실행,
timeout과 새 REGISTER 동시 실행도 workerId별 원자 상태만 남는지 검증한다.

실제 loopback WebSocket 통합 테스트는 production 15초 설정을 변경하지 않고 test context에서만
500ms timeout을 사용해 REGISTER, HEARTBEAT, timeout close 1008, OFFLINE, 새 연결 REGISTER 복구를
검증한다.

## 실제 RTX Worker 연결 결과

실행 명령:

```powershell
# Backend
$env:JAVA_HOME='C:\reasoning-game\.tools\java-gradle\Library'
cd C:\gpu-compute-poc\backend
.\gradlew.bat bootRun

# Worker
cd C:\gpu-compute-poc\worker
.\.venv\Scripts\python.exe -m gpu_worker.client `
  --backend-url ws://127.0.0.1:8080/ws/workers `
  --worker-id worker-001 `
  --hourly-price 600 `
  --gpu-index 0 `
  --supported-workload EMBEDDING
```

Backend REGISTER 로그:

```text
Worker registered workerId=worker-001 gpu="NVIDIA GeForce RTX 4070 Laptop GPU" cuda=true connectionStatus=ONLINE workerStatus=IDLE
```

REST에서 확인한 실제 값:

| 필드 | 결과 |
|---|---|
| workerId | `worker-001` |
| gpuModel | `NVIDIA GeForce RTX 4070 Laptop GPU` |
| totalVramMb | 8188 |
| freeVramMb | 7949 |
| gpuUtilization | 0% (연결 상태 계측 시점) |
| cudaAvailable | `true` |
| supportedWorkloads | `["EMBEDDING"]` |
| hourlyPrice | 600, 정수 |
| connectionStatus | `ONLINE` |
| workerStatus | `IDLE` |

두 REST snapshot의 `lastHeartbeat`:

```text
2026-08-27T11:49:06.071111800Z
2026-08-27T11:49:11.659611700Z
```

약 5초 간격으로 증가했다. Worker PID를 종료한 뒤 REST는 `connectionStatus=OFFLINE`으로 바뀌고
`workerStatus=IDLE`을 보존했다.

## Last-Connection-Wins 실제 결과

동일 `workerId=worker-duplicate`로 session-A와 session-B를 순서대로 연결했다.

```text
Worker session replaced workerId=worker-duplicate oldSessionId=e0f711cc-7492-4bc8-8936-81cb8534717a newSessionId=5628cc14-2350-4b20-a970-cac529bf9267
```

- session-A client는 `1008 replaced by newer connection`을 받고 자동 reconnect 없이 종료했다.
- session-A 종료 callback 이후에도 REST는 session-B로 인해 `ONLINE`을 유지했다.
- session-B의 heartbeat가 계속 갱신됐다.
- session-B를 종료한 뒤에만 `OFFLINE`으로 전환됐다.

## 발견한 문제와 해결

이 노트북의 Windows WDDM/Driver 조합에서는 하나의 NVML handle을 5초 이상 유지할 때 두 번째
`nvmlDeviceGetUtilizationRates`가 `NVMLError_Unknown`을 반환했다. 같은 시점의 `nvidia-smi`와 새로 연
NVML probe는 정상 동작해 WebSocket이나 CUDA 문제가 아닌 장시간 유지된 NVML session 문제로
분리했다.

`CudaGpuStatusProvider`가 snapshot 실패 시 기존 probe를 닫고 한 번만 다시 열어 재측정하도록
최소 수정했다. 회귀 테스트를 추가했고 실제 5초 간격의 연속 snapshot 및 Backend heartbeat가
정상화됐다. 재측정도 실패하면 오류를 숨기지 않고 Worker가 non-zero로 종료한다. WebSocket 자동
reconnect는 구현하지 않았다.

## STEP 2 체크리스트

| 조건 | 판정 |
|---|---|
| Spring Boot Backend 정상 실행 | PASS |
| 실제 GPU Worker outbound WebSocket 연결 | PASS |
| REGISTER 정상 처리 | PASS |
| 실제 RTX 4070/NVML/CUDA 정보 전달 | PASS |
| connectionStatus와 workerStatus 분리 | PASS |
| `GET /api/workers` | PASS |
| `GET /api/workers/{workerId}` | PASS |
| 5초 HEARTBEAT 및 GPU 상태 갱신 | PASS |
| 15초 liveness timeout 및 heartbeat 단독 복구 방지 | PASS |
| Worker 종료 감지와 OFFLINE 처리 | PASS |
| last-connection-wins 및 stale disconnect 방어 | PASS |
| Backend/Python 자동 테스트 전체 통과 | PASS |
| 자동 reconnect/EXECUTE/STEP 3 기능 미구현 유지 | PASS |

모든 STEP 2 완료 조건을 충족했다. STEP 3는 별도 승인 전까지 구현하지 않는다.
