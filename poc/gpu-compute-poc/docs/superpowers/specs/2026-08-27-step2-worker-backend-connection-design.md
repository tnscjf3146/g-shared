# STEP 2 Worker-Backend Persistent Connection Design

- 상태: Approved and implemented
- 날짜: 2026-08-27
- 선행 조건: STEP 1 CUDA Hardware Validation PASS

## 1. 목표

STEP 2는 외부 Python GPU Worker가 Spring Boot Backend에 outbound WebSocket 연결을 만들고,
실제 CUDA/NVML GPU 정보를 등록한 뒤 5초 주기의 heartbeat로 상태를 갱신하는 control-plane의
최소 기반을 구현한다.

검증할 전체 흐름은 다음과 같다.

```text
Python GPU Worker
  -> WebSocket connection
  -> REGISTER with live NVML/CUDA data
  -> HEARTBEAT every 5 seconds
  -> Backend in-memory registry update
  -> REST status query
  -> disconnect or liveness timeout
  -> connectionStatus=OFFLINE
```

Backend는 Worker PC에 inbound 연결을 시도하지 않는다. Worker가 항상 Backend로 연결한다.

## 2. 범위

### 2.1 포함

- Java 21과 Spring Boot 기반 Backend
- raw JSON WebSocket endpoint `/ws/workers`
- 실제 Python GPU Worker의 outbound WebSocket client
- 기존 `NvmlGpuProbe`와 PyTorch CUDA 검사를 재사용한 실시간 GPU 정보 수집
- REGISTER와 HEARTBEAT 메시지
- 5초 heartbeat 주기
- 15초 liveness timeout
- `ConcurrentHashMap` 기반 in-memory Worker Registry
- 연결 상태와 실행 상태의 분리
- 동일 workerId의 last-connection-wins 처리
- active session identity 기반 disconnect 처리
- Worker 목록 및 상세 REST API
- Backend 및 Python 자동 테스트
- RTX 4070 Laptop GPU를 이용한 실제 통합 검증

### 2.2 제외

다음 기능은 STEP 2에서 타입, 필드, 미래용 빈 구조를 포함해 추가하지 않는다.

- EXECUTE 메시지 및 원격 workload 실행
- 자동 reconnect 또는 retry
- Scheduler
- Job chunking 및 재할당
- P2P data plane
- Blockchain 및 정산
- DB, Redis 또는 다른 영속 저장소
- Docker Runtime
- Model Cache
- Base Model Registry
- runtimeImage
- LoRA/QLoRA training 및 inference 실행 구현

`supportedWorkloads`만 미래의 `LORA_TRAINING`, `LORA_INFERENCE` 값을 프로토콜 변경 없이
수용할 수 있도록 확장 가능한 문자열 집합으로 설계한다.

## 3. 기술 선택

### 3.1 Backend

- Java 21
- Spring Boot 4.1.1
- Gradle Wrapper 9.3.1
- Spring Web
- Spring WebSocket
- Jackson JSON
- JUnit 5 및 Spring Boot Test

Spring Boot 4.1.1은 Java 21과 Gradle 9.x를 지원한다.
공식 요구사항은 <https://docs.spring.io/spring-boot/system-requirements.html>을 기준으로 한다.

### 3.2 Worker

- Python 3.12.12의 기존 `worker/.venv`
- 기존 CUDA PyTorch 및 `nvidia-ml-py`
- 기존 `gpu_worker.gpu.NvmlGpuProbe`
- asyncio 기반 WebSocket client library
- Python 표준 `json`, `argparse`, `logging`

기존 `python -m gpu_worker` STEP 1 embedding CLI는 동작과 인자를 변경하지 않는다. STEP 2
연결 client는 별도 `python -m gpu_worker.client` entry point로 제공한다.

### 3.3 WebSocket 방식

STOMP broker나 reactive WebFlux를 사용하지 않고 Spring의 raw `TextWebSocketHandler`를 사용한다.
현재 protocol은 REGISTER와 HEARTBEAT 두 message type뿐이므로 raw JSON이 가장 작은 구현이며,
향후 EXECUTE를 별도 단계에서 추가할 때도 transport와 runtime의 경계를 유지할 수 있다.

## 4. 상태 모델

연결 상태와 실행 상태는 독립적으로 관리한다.

### 4.1 ConnectionStatus

```text
ONLINE
OFFLINE
```

- REGISTER가 active session으로 수락되면 `ONLINE`이다.
- active session이 종료되거나 liveness timeout이 발생하면 `OFFLINE`이다.
- HEARTBEAT만으로 `OFFLINE`에서 `ONLINE`으로 복구할 수 없다.
- 복구에는 새 WebSocket 연결과 유효한 REGISTER가 모두 필요하다.

### 4.2 WorkerStatus

```text
IDLE
BUSY
```

- 현재 GPU 작업이 없으면 `IDLE`이다.
- GPU 작업 실행 중이면 `BUSY`이다.
- STEP 2는 작업을 실행하지 않으므로 실제 Worker의 기본 보고값은 `IDLE`이다.
- disconnect 또는 liveness timeout 시 마지막 값을 그대로 보존한다.
- Worker 가용성은 `workerStatus`가 아니라 `connectionStatus`를 먼저 확인해야 한다.

예를 들어 작업 중 연결이 끊기면 다음 상태가 유효하다.

```text
connectionStatus=OFFLINE
workerStatus=BUSY
```

이는 연결 종료 시점의 마지막 실행 상태를 보존한 결과이며 Worker가 사용 가능하다는 의미가 아니다.

## 5. WebSocket 메시지 계약

WebSocket endpoint는 `/ws/workers`이며 UTF-8 JSON text frame만 처리한다. Backend는 STEP 2에서
ACK 또는 ERROR application message를 전송하지 않는다. 성공은 연결 유지와 Backend 로그 및
REST 상태로 관찰한다.

### 5.1 REGISTER

Worker는 WebSocket 연결 직후, HEARTBEAT보다 먼저 REGISTER를 한 번 전송한다.

```json
{
  "type": "REGISTER",
  "workerId": "worker-001",
  "gpuModel": "NVIDIA GeForce RTX 4070 Laptop GPU",
  "totalVramMb": 8188,
  "freeVramMb": 7800,
  "gpuUtilization": 0,
  "cudaAvailable": true,
  "supportedWorkloads": [
    "EMBEDDING"
  ],
  "hourlyPrice": 600,
  "workerStatus": "IDLE"
}
```

`connectionStatus`와 `lastHeartbeat`는 Backend가 결정하므로 Worker가 보내지 않는다.

필드 의미와 검증은 다음과 같다.

| 필드 | 타입 | 검증 |
|---|---|---|
| `type` | string | 정확히 `REGISTER` |
| `workerId` | string | null 또는 blank 불가 |
| `gpuModel` | string | null 또는 blank 불가 |
| `totalVramMb` | integer | `> 0` |
| `freeVramMb` | integer | `0 <= freeVramMb <= totalVramMb` |
| `gpuUtilization` | integer | `0 <= gpuUtilization <= 100` |
| `cudaAvailable` | boolean | 필수 |
| `supportedWorkloads` | array of string | null/empty 불가, 각 값은 blank 불가, 중복 제거 |
| `hourlyPrice` | integer | `>= 0`, 최소 화폐 단위 |
| `workerStatus` | string | `IDLE` 또는 `BUSY` |

`hourlyPrice`는 부동소수점을 사용하지 않는다. Backend는 Java `long`, Worker는 Python `int`로
관리한다. 단위는 배포 환경이 선택한 최소 화폐 단위이며 STEP 2는 환율이나 통화 변환을 하지 않는다.

`supportedWorkloads`는 Backend에서 특정 workload enum으로 제한하지 않고 `Set<String>`으로
관리한다. 현재 실제 Worker는 `EMBEDDING`을 보고하지만 `LORA_TRAINING`과 `LORA_INFERENCE` 같은
미래 값도 Backend 변경 없이 보존하고 반환할 수 있다.

REGISTER가 수락되면 Backend는 다음 값을 설정한다.

```text
connectionStatus=ONLINE
workerStatus=<REGISTER value>
lastHeartbeat=<Backend receive time>
activeSession=<current WebSocket session>
```

### 5.2 HEARTBEAT

Worker는 REGISTER 이후 5초마다 실제 NVML snapshot으로 HEARTBEAT를 보낸다.

```json
{
  "type": "HEARTBEAT",
  "workerId": "worker-001",
  "freeVramMb": 7650,
  "gpuUtilization": 4,
  "workerStatus": "IDLE"
}
```

| 필드 | 타입 | 검증 |
|---|---|---|
| `type` | string | 정확히 `HEARTBEAT` |
| `workerId` | string | null 또는 blank 불가 |
| `freeVramMb` | integer | `0 <= freeVramMb <=` 등록된 `totalVramMb` |
| `gpuUtilization` | integer | `0 <= gpuUtilization <= 100` |
| `workerStatus` | string | `IDLE` 또는 `BUSY` |

HEARTBEAT는 active session identity와 workerId가 모두 일치하고 현재 connectionStatus가
`ONLINE`일 때만 다음 필드를 갱신한다.

```text
freeVramMb
gpuUtilization
workerStatus
lastHeartbeat
```

GPU 모델, 전체 VRAM, CUDA availability, supported workloads, hourly price는 REGISTER 값이 유지된다.

## 6. Backend 구조

### 6.1 Protocol layer

JSON의 `type`을 먼저 읽은 뒤 REGISTER 또는 HEARTBEAT DTO로 역직렬화한다. DTO validation은
WebSocket handler와 Registry에 섞지 않고 protocol validator가 담당한다.

### 6.2 Worker Registry

Registry는 `ConcurrentHashMap<String, WorkerRecord>`를 사용한다. WorkerRecord는 다음 정보를 가진다.

```text
workerId
gpuModel
totalVramMb
freeVramMb
gpuUtilization
cudaAvailable
supportedWorkloads
hourlyPrice
connectionStatus
workerStatus
lastHeartbeat
activeSession
```

REST response는 별도 DTO로 변환하며 `activeSession`을 포함하지 않는다. OFFLINE Worker도 삭제하지
않고 마지막 관찰 상태로 남긴다.

등록, heartbeat, disconnect, timeout은 모두 `ConcurrentHashMap.compute` 또는 이에 준하는
workerId 단위 원자 연산으로 수행한다. WebSocket close 같은 I/O는 map compute 구간 밖에서 실행한다.

### 6.3 Session binding

WebSocket handler는 `sessionId -> workerId` binding을 별도 concurrent map으로 관리한다.

- 연결 직후에는 binding이 없다.
- REGISTER 성공 후 binding을 생성한다.
- 같은 session에서 두 번째 REGISTER는 거부한다.
- disconnect 시 binding을 제거한 뒤 Registry에 session identity 기반 종료를 요청한다.
- 교체된 이전 session의 binding은 해당 session의 disconnect callback까지 유지한다.

### 6.4 REST API

#### `GET /api/workers`

- 모든 ONLINE/OFFLINE Worker를 반환한다.
- 결과는 `workerId` 오름차순으로 정렬한다.
- WebSocket session은 노출하지 않는다.

#### `GET /api/workers/{workerId}`

- 등록 이력이 있으면 현재 또는 마지막 상태를 반환한다.
- 등록 이력이 없으면 HTTP 404를 반환한다.

REST timestamp는 UTC ISO-8601 형식이다.

## 7. Last-Connection-Wins

동일 workerId에 기존 active session이 있을 때 새 session의 REGISTER가 도착하면 새 연결이 우선한다.

```text
worker-001 / session-A REGISTER
  -> activeSession=A, ONLINE

worker-001 / session-B REGISTER
  -> atomically replace activeSession with B
  -> reset registered fields and lastHeartbeat from session-B REGISTER
  -> close session-A after registry update

session-A disconnect callback
  -> activeSession is B, so no state change

session-B disconnect callback
  -> activeSession matches B
  -> connectionStatus=OFFLINE
  -> activeSession cleared
```

Registry 교체가 먼저 완료되고 기존 session close가 나중에 실행되어야 한다. 이 순서와 session
identity 비교가 이전 session의 늦은 disconnect가 새 연결을 OFFLINE으로 만드는 race를 방지한다.

## 8. Heartbeat Liveness

### 8.1 Timing

- Worker heartbeat interval: 5초
- Backend liveness timeout: 마지막 heartbeat 또는 REGISTER 수신 후 15초
- Backend liveness scan interval: 1초
- timeout 조건: `lastHeartbeat + 15초 <= Backend current time`

REGISTER 시각을 최초 lastHeartbeat로 사용하므로 Worker는 연결 직후 15초의 liveness window를 갖는다.

### 8.2 Timeout transition

liveness scanner는 ONLINE Worker만 검사한다. timeout 조건을 만족하면 workerId 단위 원자 연산으로
다음을 수행한다.

```text
connectionStatus=OFFLINE
workerStatus=<preserve last value>
lastHeartbeat=<preserve last value>
activeSession=null
```

원자 연산이 반환한 timed-out session은 map compute 구간 밖에서 `1008 POLICY_VIOLATION`과
`heartbeat timeout` 사유로 닫는다. session을 무효화하고 닫기 때문에 이후 늦게 도착한 HEARTBEAT는
상태를 갱신하거나 ONLINE으로 복구할 수 없다.

복구는 다음 절차만 허용한다.

```text
new WebSocket connection
  -> valid REGISTER
  -> connectionStatus=ONLINE
```

Worker 자동 reconnect는 STEP 2에서 구현하지 않는다. Backend가 timeout으로 socket을 닫으면
Worker client는 연결 종료를 로그로 남기고 non-zero로 종료한다.

### 8.3 Concurrency rules

- heartbeat가 timeout 원자 연산보다 먼저 갱신되면 새 lastHeartbeat를 기준으로 ONLINE을 유지한다.
- timeout 원자 연산이 먼저 OFFLINE으로 전환하면 뒤늦은 heartbeat는 무시되고 session이 닫힌다.
- 새 REGISTER가 timeout과 경합하면 Registry의 active session identity와 갱신된 lastHeartbeat를
  기준으로 새 session을 보호한다.
- timeout 처리 중 반환된 이전 session을 닫을 때도 새 active session을 닫지 않는다.

Backend 시간은 주입 가능한 `Clock`으로 읽어 timeout 테스트에서 wall-clock sleep을 사용하지 않는다.

## 9. Worker 구조

Worker transport와 GPU runtime을 분리한다.

### 9.1 GPU runtime/provider

- PyTorch CUDA availability 확인
- 기존 `NvmlGpuProbe`를 이용한 GPU snapshot
- GPU 모델, total/free VRAM, utilization 제공
- transport나 JSON/WebSocket에 의존하지 않음

CUDA가 unavailable이거나 NVML 초기화가 실패하면 WebSocket에 연결하거나 REGISTER를 보내지 않고
명확한 오류와 non-zero exit code로 종료한다. GPU 값은 하드코딩하지 않는다.

### 9.2 Message factory

- GPU snapshot과 Worker 설정을 REGISTER payload로 변환
- 매 heartbeat 시점의 새 snapshot을 HEARTBEAT payload로 변환
- JSON schema나 socket lifecycle을 GPU probe에 섞지 않음

### 9.3 WebSocket client

- CLI 설정을 검증한다.
- Backend에 outbound 연결한다.
- 연결 직후 REGISTER를 한 번 전송한다.
- REGISTER 완료 후 5초마다 HEARTBEAT를 보낸다.
- server close와 Ctrl+C를 처리한다.
- 자동 reconnect를 하지 않는다.
- EXECUTE를 수신하거나 GPU workload를 실행하지 않는다.

Worker CLI는 다음 값을 받는다.

```text
--backend-url ws://127.0.0.1:8080/ws/workers
--worker-id worker-001
--hourly-price 600
--gpu-index 0
--supported-workload EMBEDDING
```

`--supported-workload`는 반복 지정 가능한 문자열 옵션이며 기본값은 `EMBEDDING`이다.

## 10. 오류 처리

| 오류 | 처리 |
|---|---|
| binary frame | `1003 NOT_ACCEPTABLE`로 종료 |
| 알 수 없는 message type | `1003 NOT_ACCEPTABLE`로 종료 |
| JSON parsing 실패 | `1007 BAD_DATA`로 종료 |
| 필드 validation 실패 | `1007 BAD_DATA`로 종료 |
| REGISTER 전 HEARTBEAT | `1008 POLICY_VIOLATION`로 종료 |
| 같은 session의 두 번째 REGISTER | `1008 POLICY_VIOLATION`로 종료 |
| HEARTBEAT workerId 불일치 | `1008 POLICY_VIOLATION`로 종료 |
| stale/non-active session HEARTBEAT | 상태 변경 없이 `1008 POLICY_VIOLATION`로 종료 |
| heartbeat timeout | 원자적으로 OFFLINE 전환 후 `1008 POLICY_VIOLATION`로 session 종료 |
| 예상하지 못한 Backend 오류 | `1011 SERVER_ERROR`로 종료 |
| Worker CUDA/NVML 오류 | 연결 전 non-zero 종료 |
| Worker WebSocket 연결 오류 | non-zero 종료, retry 없음 |

Backend 로그는 최소한 다음 event를 구조화된 key-value 형태로 남긴다.

```text
Worker registered workerId=worker-001 gpu="NVIDIA GeForce RTX 4070 Laptop GPU" cuda=true connectionStatus=ONLINE workerStatus=IDLE
Worker session replaced workerId=worker-001 oldSessionId=... newSessionId=...
Worker offline workerId=worker-001 reason=DISCONNECTED
Worker offline workerId=worker-001 reason=HEARTBEAT_TIMEOUT
```

정상 heartbeat는 로그 과다를 피하기 위해 DEBUG level로 기록한다.

## 11. 자동 테스트

### 11.1 Backend tests

1. REGISTER message 역직렬화
2. REGISTER의 각 validation boundary
3. Worker Registry 신규 등록 및 ONLINE/session/lastHeartbeat 저장
4. 중복 workerId REGISTER 시 새 session으로 원자 교체
5. session-A 교체 후 늦은 disconnect가 session-B를 OFFLINE으로 바꾸지 않음
6. active session-B disconnect 시 OFFLINE 전환 및 workerStatus 보존
7. active session HEARTBEAT의 free VRAM/utilization/workerStatus/lastHeartbeat 갱신
8. stale 또는 workerId 불일치 HEARTBEAT가 Registry를 변경하지 않음
9. 15초 미만에는 ONLINE 유지
10. 정확히 15초가 되면 OFFLINE 전환
11. timeout 시 workerStatus와 lastHeartbeat 보존, active session 제거
12. timeout 후 늦은 HEARTBEAT만으로 ONLINE 복구 불가
13. timeout 후 새 session REGISTER로 ONLINE 복구
14. timeout과 heartbeat 경합 시 원자적 결과 보장
15. WebSocket close status별 protocol 오류 처리
16. `GET /api/workers`가 session을 제외하고 workerId 순으로 반환
17. `GET /api/workers/{workerId}` 상세 반환
18. 미등록 worker 상세 요청 시 404

Registry 시간 테스트는 fake `Clock`을 사용한다. Handler 테스트는 Spring의 WebSocket session test
double을 사용하며 실제 network 통합 테스트와 분리한다.

### 11.2 Python tests

1. 실제 `GpuSnapshot`을 REGISTER payload로 변환
2. HEARTBEAT마다 새 GPU snapshot을 읽어 payload 생성
3. CUDA unavailable이면 연결 전에 실패
4. 연결 직후 REGISTER가 HEARTBEAT보다 먼저 전송됨
5. 5초 heartbeat scheduling
6. server close 시 retry 없이 종료
7. workerId, hourlyPrice, supportedWorkloads CLI validation
8. 기존 STEP 1 테스트 9개 회귀 통과

Python transport 테스트는 injectable GPU provider, socket connector, async wait를 사용하여 GPU나 실제
Backend 없이 순서와 payload를 검증한다. 실제 GPU/Backend 연결은 다음 통합 검증에서 별도로 수행한다.

## 12. 실제 통합 검증

### 12.1 정상 등록과 heartbeat

1. Java 21로 Spring Boot Backend를 실행한다.
2. `worker/.venv` Python으로 실제 RTX 4070 Worker client를 실행한다.
3. Backend 로그에서 실제 GPU 모델과 `cuda=true` REGISTER를 확인한다.
4. `GET /api/workers`에서 `connectionStatus=ONLINE`, `workerStatus=IDLE`과 실제 GPU 값을 확인한다.
5. 5초 이상 기다리고 `lastHeartbeat` 증가와 최신 free VRAM/utilization을 확인한다.

### 12.2 연결 종료

1. 실제 Worker process를 종료한다.
2. Backend가 active session disconnect를 처리하는지 확인한다.
3. REST에서 `connectionStatus=OFFLINE`과 보존된 `workerStatus`를 확인한다.

### 12.3 Last-connection-wins

1. 동일 workerId로 session-A Worker를 실행한다.
2. 동일 workerId로 session-B Worker를 실행한다.
3. Backend가 session-B를 active로 교체하고 session-A를 닫는지 확인한다.
4. session-A disconnect 이후에도 Worker가 ONLINE인지 확인한다.
5. session-B 종료 후 OFFLINE인지 확인한다.

### 12.4 Liveness timeout

Backend integration test에서 실제 WebSocket connection으로 REGISTER한 뒤 socket은 유지하되 heartbeat를
보내지 않는다. 15초 timeout 후 REST가 OFFLINE을 반환하고 socket이 닫히는지 확인한다. 이어서 기존
session에서 HEARTBEAT를 보내 상태를 복구할 수 없고, 새 연결의 REGISTER만 ONLINE으로 복구하는지
확인한다.

Mock Worker 검증만으로 STEP 2를 PASS 처리하지 않는다. 최소 한 번은 이 PC의 실제 RTX 4070 Worker가
Backend에 연결해 REGISTER와 HEARTBEAT를 보내고 REST에 나타난 증거가 필요하다.

## 13. 완료 조건

- [ ] Spring Boot Backend 정상 실행
- [ ] 실제 GPU Worker outbound WebSocket 연결
- [ ] REGISTER 정상 처리
- [ ] 실제 RTX 4070 GPU/NVML/CUDA 정보 전달
- [ ] connectionStatus와 workerStatus 분리
- [ ] `GET /api/workers` 조회
- [ ] `GET /api/workers/{workerId}` 조회
- [ ] 5초 HEARTBEAT와 GPU 상태 갱신
- [ ] 마지막 heartbeat 후 15초 liveness timeout
- [ ] timeout 후 늦은 heartbeat로 ONLINE 복구 불가
- [ ] 새 연결 및 REGISTER로만 ONLINE 복구
- [ ] active session disconnect 시 OFFLINE
- [ ] last-connection-wins 및 stale disconnect 안전성
- [ ] 기존 STEP 1 테스트 9개 포함 자동 테스트 전체 PASS
- [ ] Worker process 종료 후 OFFLINE 실제 확인

모든 조건이 충족되어야 STEP 2를 PASS로 판정한다. STEP 2 PASS 이후에도 STEP 3 코드는 별도 승인 전
구현하지 않는다.
