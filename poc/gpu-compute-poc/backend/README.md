# STEP 2 — Spring Boot Worker Connection Backend

Java 21 기반 Spring Boot Backend로, 외부 Python GPU Worker가 `/ws/workers`에 outbound raw JSON
WebSocket 연결을 만든다. Registry는 in-memory이며 DB나 Redis를 사용하지 않는다.

## 실행

```powershell
$env:JAVA_HOME='C:\reasoning-game\.tools\java-gradle\Library'
cd C:\gpu-compute-poc\backend
.\gradlew.bat bootRun
```

Java 21이 PATH와 JAVA_HOME에 설정된 환경에서는 첫 줄의 로컬 경로 설정을 생략할 수 있다.

## Endpoint

- WebSocket: `ws://127.0.0.1:8080/ws/workers`
- Worker 목록: `GET http://127.0.0.1:8080/api/workers`
- Worker 상세: `GET http://127.0.0.1:8080/api/workers/{workerId}`

REST 응답은 `connectionStatus`, `workerStatus`, 마지막 GPU 상태와 `lastHeartbeat`를 포함하지만
내부 `WebSocketSession`은 노출하지 않는다.

## 상태와 liveness

- `connectionStatus`: `ONLINE` 또는 `OFFLINE`
- `workerStatus`: `IDLE` 또는 `BUSY`
- Worker heartbeat 주기: 5초
- Backend timeout: 마지막 REGISTER/HEARTBEAT 이후 15초
- scan 주기: 1초

timeout은 workerId별 원자 연산으로 active session을 무효화한 뒤 socket을 닫는다. timeout 뒤의
HEARTBEAT만으로 ONLINE으로 복구할 수 없으며 새 연결과 REGISTER가 필요하다.

동일 workerId가 다시 REGISTER하면 새 session이 active session으로 원자 교체되고 이전 session은
1008로 종료된다. 이전 session의 뒤늦은 disconnect는 새 연결을 OFFLINE으로 바꾸지 않는다.

## 자동 테스트

```powershell
$env:JAVA_HOME='C:\reasoning-game\.tools\java-gradle\Library'
cd C:\gpu-compute-poc\backend
.\gradlew.bat clean test --no-daemon
```

15초 경계 단위 테스트는 주입된 `Clock`을 사용하므로 실제 15초 sleep이 없다. 실제 socket 통합
테스트는 별도의 짧은 test timeout 설정으로 protocol과 scheduler 연결만 빠르게 확인한다.

## 실제 Worker 연결

Backend 실행 후 별도 터미널에서 [Worker STEP 2 실행법](../worker/README.md#step-2-backend-연결)을
따른다. 실행 결과는 [STEP 2 통합 검증 기록](../docs/step2-integration-validation.md)에 있다.
# STEP 2.5 Job API and authentication

Production/default startup requires environment-provided bearer credentials:

```powershell
$env:GPU_POC_OPERATOR_TOKEN='<random-operator-token>'
$env:GPU_POC_WORKER_CREDENTIALS='worker-001=<random-worker-token>'
.\gradlew.bat bootRun --no-daemon
```

`POST /api/jobs` accepts only `workerId` and `workload`; the approved workloads
are `GPU_PYTORCH_TEST` and `GPU_PYTORCH_TEST_30S`. `GET /api/jobs/{jobId}` returns the in-memory
terminal result. Both endpoints require `Authorization: Bearer <operator-token>`.
The WebSocket handshake requires the worker token, and REGISTER must use the
workerId bound to that token. Put TLS/WSS termination in a reverse proxy for
internet deployment. Do not put secrets in URLs or logs.

For `GPU_PYTORCH_TEST_30S`, the Worker sends `EXECUTE_STARTED` after Docker has
created and started the fixed container. The job then exposes `RUNNING`,
`containerId`, `containerName`, and `imageDigest`. A terminal SUCCESS also
contains the timed CUDA details and checksum. These are execution proof fields,
not Docker options supplied by the Backend.
