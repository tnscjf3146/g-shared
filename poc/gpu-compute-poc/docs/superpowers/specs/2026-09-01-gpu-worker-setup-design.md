# GPU Worker 공급자 Setup 자동화 설계

- 상태: Approved
- 날짜: 2026-09-01
- 대상 OS: Windows 10/11 x64
- 공급자 하드웨어: NVIDIA GPU, 현재 PoC는 단일 GPU
- 배포 산출물: `GPUWorkerSetup.exe` 하나
- Worker 배포 형식: 기존 PyInstaller onedir 유지
- Setup 배포 형식: Python + Tkinter, PyInstaller onefile

## 1. 목표

GPU 공급자가 개발 지식, Python, venv 또는 소스 저장소 없이 다음 흐름을 완료하게 한다.

```text
GPUWorkerSetup.exe 실행
  -> Backend URL / Worker ID / Worker Token 최초 1회 입력
  -> URL, WSS, Bearer handshake, Worker ID 형식 사전 검증
  -> 필요한 Windows/WSL2/Docker 환경 준비
  -> Worker onedir payload 안전 설치 또는 업데이트
  -> 기존 Worker의 GPU_STANDARD_BENCHMARK 실행 또는 유효 cache 재사용
  -> 기존 WebSocket REGISTER
  -> Backend 상태에서 ONLINE + IDLE + Benchmark 확인
  -> SETUP_COMPLETED
```

공급자가 직접 수행하는 작업은 Setup 실행, 필요한 경우 UAC 승인과 재부팅, 인증정보 최초 입력으로
제한한다. Setup은 설치, UI, 상태 전이와 remediation만 담당하며 GPU/Docker/Benchmark/REGISTER 판단의
authoritative source는 기존 Worker로 유지한다.

## 2. 범위와 비범위

### 2.1 포함

- Backend URL, Worker ID, 마스킹된 Worker Token 입력 UI
- DPAPI Current User scope의 Token 보호 저장
- 기존 평문 `workerToken`의 읽기 전용 하위 호환 fallback
- WSS와 Bearer WebSocket handshake 사전 검증
- WSL2와 Virtual Machine Platform 점검 및 제한된 UAC remediation
- Docker Desktop 기설치 검증 또는 명시적 약관 동의 후 per-user 설치
- Docker 시작 지연에 대한 bounded retry/backoff
- 기존 Worker onedir payload의 manifest, SHA-256, canonical path 검증
- `%LOCALAPPDATA%\GPUCompute\Worker` 설치, repair, update, rollback
- 기존 Worker의 NVML, Docker GPU probe, WorkloadCatalog, BenchmarkCache, REGISTER 재사용
- Backend Worker 상태 polling 기반 최종 등록 검증
- HKCU Run 기반 current-user AutoStart
- 재부팅 재개를 위한 stable Setup copy와 HKCU RunOnce
- Setup/Worker single-instance 보호와 update/AutoStart race 방지
- fake/mock, packaged smoke, disposable VM, 실제 GPU hardware 검증
- 향후 version policy 및 signed remote update를 위한 인터페이스

### 2.2 제외

- Backend 인증 구조 변경
- 일회용 enrollment code API 구현
- credential validation REST API 추가
- WebSocket REGISTER protocol 변경
- Backend worker status API 변경
- Docker Desktop 무료/유료 라이선스 대상 판단
- BIOS/UEFI 자동 변경
- NVIDIA driver 자동 설치 또는 변경
- 기존 WSL distribution 제거 또는 초기화
- Docker Desktop all-users 설치
- Windows service 또는 machine-wide Worker AutoStart
- Worker workload, Docker container security policy, Benchmark 알고리즘 변경
- `RemoteSignedUpdateSource` 실제 네트워크 다운로드 구현
- 다중 GPU 선택 UI

## 3. 확정된 접근 방식

### 3.1 선택: 독립 Setup orchestrator + 기존 Worker authoritative logic

Setup이 Worker 기능을 복사하지 않고 작은 interface 또는 설치된 Worker의 제한된 probe를 사용한다.
Setup은 일반 사용자 권한으로 실행하고, 관리자 권한은 allowlisted Windows feature 작업에만 사용한다.

### 3.2 제외: Setup에서 GPU/Docker/Benchmark를 다시 구현

두 구현의 판단이 달라질 수 있고 기존 보안 정책과 cache 규칙이 분기되므로 제외한다.

### 3.3 제외: Backend enrollment 및 사전 Worker ID/Token mapping API

현재 PoC의 `workerId + token` 계약을 유지한다. Token handshake는 환경 준비 전에 검사하지만 실제
Worker ID와 Token의 mapping은 정상 Worker REGISTER에서 최종 검증한다.

## 4. 전체 아키텍처

```text
GPUWorkerSetup.exe
  SetupApplication / Tkinter UI
  SetupOrchestrator
  CredentialProvider
    ManualCredentialProvider             # 현재 구현
    EnrollmentCredentialProvider         # interface only
  BackendEndpointConfig
  CredentialPreflightValidator
  SystemProbe / SystemRemediator
  ElevatedHelper                         # enum allowlist only
  WorkerInstaller
    UpdateSource
      EmbeddedPayloadUpdateSource        # 현재 구현
      RemoteSignedUpdateSource            # interface only
    PayloadManifestVerifier
    InstallTransaction / RecoveryManager
    VersionPolicy
  WorkerControlClient
  WorkerRegistrationVerifier
  AutoStartManager / ResumeManager
  SetupStateStore / SecretRedactingLogger

                    installs and launches only from

%LOCALAPPDATA%\GPUCompute\Worker\gpu-worker-agent.exe
  ProtectedCredentialReader
  WorkerInstanceGuard
  LocalWorkerControlServer
  Existing EnvironmentManager
  Existing DockerManager
  Existing WorkloadCatalog
  Existing BenchmarkRunner / BenchmarkCache
  Existing WebSocket REGISTER / HEARTBEAT / job runtime

                    WSS + existing REST status endpoint

Existing Backend
  /ws/workers
  /api/workers/{workerId}
```

### 4.1 설치 및 mutable data 경계

```text
%LOCALAPPDATA%\GPUCompute\
  Worker\                 # installed binary/runtime
  Setup\GPUWorkerSetup.exe
  config\                 # future separation point
  state\setup-state.json
  state\update-transaction.json
  logs\setup.log
```

현재 PoC 호환성을 위해 다음 파일은 Worker 실행 파일 옆에 유지한다.

- `worker-config.json`
- `benchmark-result.json`

업데이트 mutable allowlist는 이 두 파일의 정확한 이름만 허용한다. 이름 패턴이나 임의 확장자로 다른
파일을 보존하지 않는다. Setup 상태와 로그는 Worker directory 밖에 둔다.

## 5. 설정과 Credential

### 5.1 공급자 UI 입력

Setup UI에는 다음 세 항목만 표시한다.

- Backend URL
- Worker ID
- Worker Token

Token은 입력 시 마스킹하고 저장 또는 실패 후 다시 표시하지 않는다.

### 5.2 기본 Worker 설정

- `DEFAULT_HOURLY_PRICE = 600`
- `DEFAULT_GPU_INDEX = 0`
- `supportedWorkloads`는 기존 Worker의 `agent_config.DEFAULT_SUPPORTED_WORKLOADS`를 그대로 사용

가격과 GPU 선택은 별도 settings/default 계층에 둔다. 향후 Backend 가격 설정과 다중 GPU UI가 이
계층을 대체할 수 있어야 한다. Setup은 임의 workload 문자열을 만들거나 WorkloadCatalog와 별개의
Setup 전용 기본값을 정의하지 않는다.

### 5.3 CredentialProvider

```text
CredentialProvider.acquire() -> CredentialInput
ManualCredentialProvider
EnrollmentCredentialProvider # interface only, construction은 가능하되 기능 미구현
```

Credential acquisition과 Setup 상태 전이를 분리한다. 향후 enrollment API가 생겨도 orchestrator의
환경 준비, 설치, Benchmark, REGISTER 흐름은 변경하지 않는다.

### 5.4 DPAPI 저장 정책

- 신규 Setup은 `workerTokenProtected`만 기록한다.
- DPAPI scope는 Current User이다.
- Worker도 동일 사용자 context에서 복호화한다.
- protected field가 존재하지만 복호화 실패 시 평문 fallback을 사용하거나 생성하지 않는다.
- 기존 `workerToken`은 protected field가 전혀 없는 기존 config에서만 읽기 fallback으로 허용한다.
- Token은 CLI, 환경변수, Run/RunOnce, helper, setup-state, 로그로 전달하지 않는다.
- mismatch 시 protected Token을 제거하고 URL과 Worker ID는 보존한다.

## 6. Backend endpoint와 인증 경계

UI의 Backend URL은 production에서 `https://` Backend base URL로 정의한다. endpoint는 문자열 치환이
아니라 하나의 구조화된 `BackendEndpointConfig`에서 URL parser로 생성한다.

```text
BackendEndpointConfig
  public_base_url
  worker_websocket_path
  worker_status_path_template
  websocket_scheme_policy
  status_authentication_mode
  tls_policy
```

- Worker config에는 기존 계약이 요구하는 완성된 WebSocket URL을 기록한다.
- REST status URL은 명시적 path template으로 생성하고 Worker ID는 URL encoding한다.
- port와 reverse-proxy base path는 구조적으로 보존한다.
- userinfo, fragment, 임의 query, path traversal은 거부한다.
- production은 `https/wss`만 허용하며 TLS 검증 disable 옵션을 제공하지 않는다.
- local development의 `http/ws`는 production과 분리된 test/development profile에서만 허용한다.
- status endpoint가 현재 계약처럼 인증이 필요 없으면 Token을 전송하지 않는다.
- 향후 Bearer가 필요하면 declared `status_authentication_mode`에서만 최소 header를 사용한다.
- Authorization header와 Backend response 원문은 로그에 기록하지 않는다.

사전 검증은 URL 형식, WSS 연결 가능성, Bearer handshake 승인, Worker ID 로컬 형식을 확인한다. Token
handshake가 실패하면 WSL/Docker/Benchmark 단계로 진행하지 않는다. Worker ID와 Token의 실제 mapping은
기존 REGISTER에서 검증한다.

## 7. 상태 모델과 데이터 흐름

Setup state와 Worker runtime state를 분리한다.

### 7.1 Setup state

```text
CREDENTIALS_READY
SYSTEM_READY
WSL_READY
DOCKER_READY
WORKER_INSTALLED
BENCHMARK_READY
REGISTER_VERIFIED
SETUP_COMPLETED
```

저장된 READY는 힌트일 뿐이다. Setup 실행마다 실제 상태를 다시 probe한다. setup-state에는 진행 상태,
Setup/Worker 버전, 안전한 오류 코드와 warning만 저장하고 secret은 저장하지 않는다.

`DOCKER_READY`는 Setup이 책임지는 Docker Desktop 설치 여부와 engine lifecycle readiness를 뜻한다.
Linux engine 적합성과 container의 NVIDIA GPU 접근 가능 여부는 `WORKER_INSTALLED` 이후 설치된 Worker의
기존 `EnvironmentManager`와 `DockerManager`가 판정한다. Setup은 이 상태 이름을 근거로 Docker GPU가
검증됐다고 간주하지 않는다.

### 7.2 Worker runtime state

```text
STARTING
IDLE
BUSY
OFFLINE
```

### 7.3 최종 완료 조건

`SETUP_COMPLETED`는 다음을 모두 확인한 뒤에만 기록한다.

- Backend에 해당 Worker ID가 존재
- `connectionStatus == ONLINE`
- `workerStatus == IDLE`
- Backend benchmark가 null이 아님
- Backend benchmark version이 현재 `BENCHMARK_VERSION`과 일치
- Backend benchmark score가 0보다 큼
- 기존 Worker BenchmarkCache가 현재 로컬 GPU UUID와 benchmarkVersion에 대해 유효

현재 REGISTER payload와 `/api/workers/{workerId}` 응답에는 GPU UUID가 없다. 따라서 이번 범위에서
Backend가 저장한 UUID 존재 여부를 polling만으로 검증할 수 없다. 이 설계는 Backend API를 변경하지
않는 대신 Worker authoritative cache의 GPU UUID를 검증하고 Backend의 version/score를 교차 검증한다.
향후 Backend가 GPU UUID를 제공하면 verifier가 Backend UUID와 local UUID까지 일치시킨다. 현재 이
제약은 release report에 명시하고 Backend UUID 검증을 `HARDWARE_VERIFIED`로 과장하지 않는다.

### 7.4 Credential mismatch

REGISTER에서 Worker ID/Token mapping이 실패하면 다음을 원자적으로 수행한다.

- protected Token 삭제
- `REGISTER_VERIFIED` 삭제
- `SETUP_COMPLETED` 삭제
- URL과 Worker ID 유지
- Token UI를 빈 입력으로 복귀
- `WORKER_CREDENTIAL_MISMATCH` 표시

재입력 후 WSL, Docker, 정상 Worker 설치를 다시 수행하지 않는다. Benchmark는 기존 UUID와 version cache
검증이 통과하면 재실행하지 않는다.

## 8. 재부팅 재개

관리자 Windows feature 작업에서 재부팅이 필요하면 자동 재부팅하지 않는다.

1. stable Setup copy를 `%LOCALAPPDATA%\GPUCompute\Setup`에 보관한다.
2. HKCU RunOnce에 인용된 executable path와 `--resume`만 등록한다.
3. Token, Backend 정보, Benchmark 결과를 command line에 포함하지 않는다.
4. 사용자가 재부팅을 선택하도록 안내한다.
5. 재개 후 저장 상태를 신뢰하지 않고 실제 시스템을 다시 probe한다.

RunOnce가 실행되지 않아도 사용자가 Setup을 직접 실행하면 같은 state에서 안전하게 재개한다.

## 9. Docker Desktop 설치와 시작

Docker Desktop이 없을 때만 다음 UI를 표시한다.

- Docker Desktop 이용약관 링크
- 기본 미선택 동의 checkbox
- 사용 환경에 따라 별도 라이선스 조건이 적용될 수 있다는 안내

동의하지 않으면 installer와 시스템 변경을 호출하지 않고 `DOCKER_LICENSE_NOT_ACCEPTED`로 유지한다.
Setup은 사용자의 무료/유료 라이선스 자격을 판단하지 않는다.

동의 후 공식 installer의 현재 지원 옵션을 고정 argument list로 실행한다.

```text
install
--user
--accept-license
--backend=wsl-2
```

`--accept-license`는 명시적 동의가 확인된 실행에서만 추가한다. installer는 공식 HTTPS source에서 받고
Authenticode publisher/signature를 검증한다. installer version과 사용 옵션을 release metadata에
기록하며, 검증 실패 시 실행하지 않는다. Docker 설치 자체는 비관리자로 수행하고 WSL feature 활성화와
권한 경계를 분리한다.

Docker Desktop은 설치 직후나 Windows 로그인 직후 engine이 준비되지 않을 수 있다. AutoStart launcher는
설치 여부를 먼저 확인하고, 지원되는 Docker Desktop start command로 비관리자 시작을 요청한 뒤 설정
가능한 기본 300초 동안 `1, 2, 4, 8, 10초 cap` backoff로 engine readiness만 기다린다.

- Docker 없음: `DOCKER_NOT_INSTALLED`, retry 없이 Setup 필요
- 설치됨, engine 시작 중: `DOCKER_STARTING`, bounded retry
- timeout: `DOCKER_START_TIMEOUT`
- Linux engine/GPU 문제: 기존 Worker의 authoritative probe 결과 사용

Setup은 Docker GPU 판정 로직을 복제하지 않는다.

## 10. UAC helper 보안 경계

일반 Setup은 비관리자로 실행한다. 같은 EXE의 `--elevated-helper` 모드는 enum allowlist만 처리한다.

```text
ENABLE_WSL_FEATURE
ENABLE_VIRTUAL_MACHINE_PLATFORM
UPDATE_WSL
```

각 action은 고정 실행 파일과 고정 인자에 매핑한다. 알 수 없는 action, 추가 positional argument, 임의
PowerShell 또는 command string은 거부한다. helper는 Worker, Backend, Token, Benchmark, config payload를
받거나 읽거나 실행하지 않는다. 일반 Setup은 secret이 아닌 correlation ID로 결과를 연계한다.

## 11. Local control interface 보안

Worker 종료와 상태 확인에는 loopback TCP가 아닌 Windows Named Pipe를 사용한다.

```text
LocalWorkerControl
  STATUS
  GRACEFUL_SHUTDOWN
```

- pipe DACL은 Worker를 실행한 Current User SID만 허용한다.
- pipe 이름에는 raw Worker ID, Token 또는 개인정보를 넣지 않고 install identity hash를 사용한다.
- remote client access를 거부하고 네트워크에 노출하지 않는다.
- request는 크기 제한된 typed message와 정확한 field allowlist로 파싱한다.
- 임의 command, path, shell argument, script 실행 기능을 제공하지 않는다.
- Token이나 Backend credential을 IPC로 전달하지 않는다.
- shutdown 요청 전 Setup은 Backend IDLE, exact PID와 canonical executable path를 다시 확인한다.
- named pipe 연결만을 authorization으로 간주하지 않고 pipe client SID를 확인한다.

Worker가 응답하지 않으면 bounded grace period 후 정확한 PID/path와 IDLE 조건을 다시 검증한다. 그 뒤
프로세스 종료를 시도하되 파일 lock이 남으면 update를 중단한다.

## 12. Single-instance와 동시성

### 12.1 Setup

일반 Setup orchestrator는 다음 두 잠금을 사용한다.

- `Local\GPUComputeSetup-{CurrentUserSidHash}` named mutex
- `%LOCALAPPDATA%\GPUCompute\state\setup.lock` share-none file lock

첫 잠금은 빠른 동일 session 충돌 방지, 두 번째 잠금은 같은 사용자의 다른 Windows session까지 보호한다.
두 번째 Setup은 state나 Worker directory를 변경하기 전에 `SETUP_ALREADY_RUNNING`을 표시하고 종료한다.
OS handle 기반이므로 process crash 후 잠금이 회수된다.

### 12.2 Worker

launcher가 exact canonical path의 기존 Worker를 먼저 확인하고, Worker 자체가 config를 읽은 직후
WebSocket/Docker 동작 전에 current-user instance mutex를 획득한다. 이름은 SID, Worker ID, GPU index의
hash이며 Token을 포함하지 않는다. 이미 실행 중이면 새 Worker는 REGISTER하지 않고
`WORKER_ALREADY_RUNNING` 결과로 종료한다. Setup은 기존 process의 Backend 상태를 검증한다.

### 12.3 Update와 AutoStart

update와 AutoStart는 공통 `install-operation.lock`을 사용한다. journal이 다음 active 상태이면
`--autostart-worker`는 Worker를 실행하지 않는다.

```text
PREPARED
OLD_WORKER_STOP_REQUESTED
OLD_WORKER_STOPPED
NEW_VERSION_ACTIVATED
VERIFYING
ROLLBACK_STARTED
```

AutoStart는 짧게 bounded wait한 뒤 여전히 active이면 `AUTO_START_DEFERRED_UPDATE`를 기록하고 종료한다.
transaction owner가 `COMMITTED` 또는 `ROLLED_BACK` 후 정확한 Worker를 시작한다. journal 검사와 start
사이에는 start lease를 유지하며 Worker mutex가 최종 race를 방어한다.

## 13. AutoStart

완료 UI의 `Windows 로그인 시 GPU Worker 자동 실행` checkbox는 기본 선택이다. 설치 직후에는 선택
여부와 무관하게 Worker를 한 번 실행하고 실제 REGISTER와 IDLE을 확인한다.

HKCU Run은 stable Setup copy를 비관리자로 실행한다.

```text
"%LOCALAPPDATA%\GPUCompute\Setup\GPUWorkerSetup.exe" --autostart-worker
```

AutoStart 해제는 Setup이 소유한 정확한 registry value만 제거한다. Worker 설치, config, Benchmark cache,
state와 logs는 보존한다. 등록 실패는 `AUTO_START_FAILED` warning이며 REGISTER 성공을 무효화하지 않는다.

## 14. Worker payload와 설치 보안

### 14.1 Manifest

manifest metadata:

```text
schemaVersion
workerVersion
entrypoint
fileCount
mutableAllowlist
files[]: relativePath, size, sha256
```

Worker onedir 전체를 manifest와 SHA-256으로 보호한다. mutable allowlist는 정확히
`worker-config.json`, `benchmark-result.json`이다.

### 14.2 경로 검증

모든 manifest path에 대해 다음을 확인한다.

- 빈 경로와 normalized duplicate가 아님
- 절대경로, UNC, device path, drive-qualified path가 아님
- `..` component가 없음
- canonical destination이 staging root 내부임
- staging/install root와 모든 ancestor/entry가 reparse point가 아님
- payload entry가 symlink나 junction이 아님
- case-insensitive Windows common path 검사가 root 내부임

실패 코드는 `PAYLOAD_MANIFEST_INVALID`, `PAYLOAD_PATH_INVALID`, `INSTALL_PATH_UNSAFE`이며 Worker를 절대
실행하지 않는다.

### 14.3 설치 검증 체인

```text
GPUWorkerSetup.exe
  -> embedded Worker payload SHA-256
  -> private staging copy SHA-256
  -> installed Worker SHA-256
  -> installed gpu-worker-agent.exe only
  -> Benchmark / REGISTER / IDLE
```

PyInstaller onefile의 temporary extraction 위치에서는 Worker를 실행하지 않는다.

### 14.4 UpdateSource

```text
UpdateSource
  EmbeddedPayloadUpdateSource  # 현재 구현
  RemoteSignedUpdateSource     # interface only
```

UpdateSource는 검증 가능한 payload와 release metadata만 제공하고, staging/swap/REGISTER/rollback은 공통
installer transaction이 담당한다. 향후 RemoteSignedUpdateSource는 HTTPS, release metadata,
Authenticode, SHA-256, Worker version을 검증해야 하며 URL에서 받은 EXE를 직접 덮어쓸 수 없다.

## 15. Worker version policy와 downgrade 방지

Semantic Versioning 호환 비교를 제공하는 `WorkerVersion` value object와 `VersionPolicy` interface를 둔다.

```text
VersionPolicy.evaluate(current, candidate, policyMetadata) -> VersionDecision

policyMetadata:
  minimumSupportedWorkerVersion?
  recommendedWorkerVersion?
  latestWorkerVersion?

VersionDecision:
  allowed
  receiveWorkAllowed
  updateRequired
  updateRecommended
  reasonCode
```

현재 embedded-only PoC에서는 local policy가 candidate와 current version을 평가한다. Backend version policy
API는 추가하지 않지만 향후 adapter가 metadata를 공급할 수 있다. Backend policy metadata가 없는 현재
버전에서는 minimum/recommended/latest 값을 임의로 만들지 않고, candidate downgrade 차단과 manifest
version 일관성만 강제한다.

- current < minimum: `WORKER_VERSION_UNSUPPORTED`; 향후 작업 수신 금지 또는 update required
- current < recommended: `WORKER_UPDATE_RECOMMENDED` warning
- current < latest: 정보 표시 가능
- candidate > current: 정상 update 허용
- candidate == current: hash 정상 시 SKIP, 불일치 시 repair
- candidate < current: 일반 install/update에서 `WORKER_DOWNGRADE_BLOCKED`
- transaction rollback: journal에 기록된 previous version으로만 downgrade 예외 허용

rollback 예외는 사용자가 선택한 arbitrary older payload에 적용하지 않는다. transaction ID, previous
manifest hash와 backup 경로가 모두 journal에 일치할 때만 허용한다. version policy 결과는 향후 Worker의
job acceptance gate에 연결 가능하지만 이번 범위에서 Backend나 EXECUTE protocol을 변경하지 않는다.

## 16. Update transaction과 rollback

### 16.1 정상 update

```text
Backend IDLE 확인
  -> installed exact path Worker PID 확인
  -> Named Pipe GRACEFUL_SHUTDOWN
  -> process exit 확인
  -> directory lock 확인
  -> staging 검증
  -> directory swap
  -> installed hash 재검증
  -> 새 Worker 실행
  -> Backend REGISTER + ONLINE + IDLE + Benchmark 검증
  -> COMMITTED
  -> backup 삭제
```

BUSY Worker는 강제 update하지 않고 `WORKER_UPDATE_BUSY`로 연기한다. 종료 후 lock이 남으면 부분
overwrite 없이 `WORKER_UPDATE_LOCKED`로 중단한다.

### 16.2 Journal

`%LOCALAPPDATA%\GPUCompute\state\update-transaction.json`을 atomic replace로 저장한다.

```text
PREPARED
OLD_WORKER_STOP_REQUESTED
OLD_WORKER_STOPPED
NEW_VERSION_ACTIVATED
VERIFYING
COMMITTED

ROLLBACK_STARTED
NEW_WORKER_STOPPED
OLD_VERSION_RESTORED
OLD_WORKER_RESTARTED
ROLLED_BACK
```

backup은 새 Worker 검증 성공까지 유지한다. 새 Worker 실패 시 정확한 새 process를 종료하고 manifest가
유효한 previous backup으로 rollback한 뒤 이전 Worker를 재실행할 수 있게 한다. update crash 후에는
journal과 각 directory의 manifest/hash를 사용하며 수정 시간이나 directory 이름만으로 임의 선택하지
않는다. 손상된 journal은 `UPDATE_STATE_INVALID`로 중단한다.

WSL과 Docker 같은 system change는 rollback하지 않는다.

## 17. 통합 오류 모델

```text
SetupIssue
  stage
  code
  severity: INFO | WARNING | BLOCKING
  retryability: TRANSIENT | USER_ACTION | PERMANENT
  user_message
  diagnostic_id
```

UI에는 friendly message와 code만 표시하고 traceback이나 raw process/backend output은 노출하지 않는다.

주요 code:

- Credential: `INVALID_BACKEND_URL`, `BACKEND_UNREACHABLE`, `WORKER_TOKEN_REJECTED`,
  `WORKER_CREDENTIAL_MISMATCH`, `CREDENTIAL_DECRYPTION_FAILED`
- System/GPU: `UNSUPPORTED_WINDOWS`, `UNSUPPORTED_ARCHITECTURE`, `NVIDIA_GPU_NOT_FOUND`,
  `NVML_UNAVAILABLE`, `VIRTUALIZATION_DISABLED`
- WSL/UAC: `WSL_INSTALL_FAILED`, `WSL_UPDATE_REQUIRED`, `REBOOT_REQUIRED`, `UAC_CONSENT_DENIED`
- Docker: `DOCKER_LICENSE_NOT_ACCEPTED`, `DOCKER_INSTALLER_SIGNATURE_INVALID`,
  `DOCKER_INSTALL_FAILED`, `DOCKER_NOT_INSTALLED`, `DOCKER_STARTING`,
  `DOCKER_START_TIMEOUT`, `DOCKER_ENGINE_UNAVAILABLE`, `DOCKER_LINUX_REQUIRED`,
  `DOCKER_GPU_UNAVAILABLE`
- Payload/update: `PAYLOAD_MANIFEST_INVALID`, `PAYLOAD_PATH_INVALID`, `PAYLOAD_HASH_MISMATCH`,
  `INSTALL_PATH_UNSAFE`, `WORKER_UPDATE_BUSY`, `WORKER_UPDATE_LOCKED`,
  `WORKER_DOWNGRADE_BLOCKED`, `WORKER_VERSION_UNSUPPORTED`, `WORKER_PROCESS_EXITED`
- Concurrency: `SETUP_ALREADY_RUNNING`, `WORKER_ALREADY_RUNNING`, `AUTO_START_DEFERRED_UPDATE`
- REGISTER: `REGISTER_TIMEOUT`, `BACKEND_STATUS_UNAVAILABLE`,
  `BENCHMARK_REGISTRATION_FAILED`
- AutoStart: `AUTO_START_FAILED`

warning은 REGISTER 성공을 무효화하지 않는다. blocking error는 현재 단계에 머물며 이미 성공한 단계는
실제 re-probe 결과가 여전히 READY이면 재실행하지 않는다.

## 18. 로그 정책

기본 경로는 `%LOCALAPPDATA%\GPUCompute\logs\setup.log`이다. 크기 기반 rotation을 적용한다.

allowlisted fields:

```text
timestamp
runId
setupVersion
stage
event
status
errorCode
safeExitCode
diagnosticId
```

기록 금지:

- Worker Token
- Authorization header
- WebSocket handshake header 전체
- DPAPI plaintext와 ciphertext
- config 원문
- command line 전체
- Backend response 원문
- 불필요한 개인정보

외부 stdout/stderr는 크기를 제한하고 안전한 필드만 추출한다. defense-in-depth로 현재 Token과 Bearer
pattern을 redaction한다. Docker 약관 동의는 boolean과 timestamp만 기록하고 라이선스 자격을 기록하지
않는다. UI는 로그 폴더를 열 수 있다.

## 19. 테스트 전략

### 19.1 계층

| 단계 | 목적 | 시스템 변경 |
|---|---|---|
| Unit | 상태 전이, 정책, 보안 규칙 | 없음 |
| Component/Integration | 파일, subprocess, IPC, WSS/REST | 임시 경로와 test server |
| Packaged Smoke | PyInstaller Worker/Setup | 기본 read-only |
| Hardware E2E | Windows/NVIDIA/Docker/Backend | 승인된 실제 PC 또는 disposable VM |

실제 개발 PC에서 WSL/Docker 제거 시험을 하지 않는다. destructive 시나리오는 fake나 disposable VM을
사용한다.

### 19.2 Fake/mock 경계

- `SystemCommandRunner`: fixed typed operation만 받고 WSL/Docker/UAC 상태 재현
- `WindowsPlatform`: mutex, file lock, registry, DPAPI, process, Authenticode, reparse 검사
- `Clock`과 `BackoffPolicy`: 실제 대기 없는 timeout/retry 검증
- `BackendTransport`: fake WSS/REST와 local TLS test server
- `UpdateSource`: embedded fake payload와 향후 remote source contract
- `VersionPolicy`: minimum/recommended/latest 조합과 rollback exception

path/hash/lock test는 과도하게 mock하지 않고 실제 NTFS temporary directory를 사용한다.

### 19.3 필수 공격 및 실패 test

- `../`, absolute, UNC, device path, drive change, canonical root escape
- symlink, junction, reparse point
- hash mismatch, missing/extra file, duplicate normalized path
- Setup mutex와 Worker mutex 경쟁
- update/AutoStart race
- journal 각 단계 crash injection
- BUSY update 금지, 정확한 PID/path, PID reuse
- locked EXE/DLL과 부분 overwrite 부재
- DPAPI round-trip 및 decryption failure
- 신규 plaintext Token 저장 부재와 log secret redaction
- TLS failure 및 production disable option 부재
- Worker ID/token mismatch와 상태 정리
- Benchmark cache hit/miss, UUID/version 불일치
- normal update, arbitrary downgrade 차단, journal rollback downgrade 허용
- local control pipe ACL, 다른 SID 거부, unknown command 거부, oversized request 거부

### 19.4 기존 test 보존

- 기존 Worker `88 tests + 22 subtests` 전부 통과
- 기존 ffi-8.dll packaging test 유지
- 신규 Setup/Worker adapter test는 기존 test를 대체하지 않고 추가

## 20. Build와 배포 검증

단일 `build-release.ps1` entrypoint:

```text
test-worker
  -> test-setup
  -> build-worker
  -> verify-worker
  -> generate-worker-manifest
  -> verify-worker-manifest
  -> build-setup
  -> verify-setup
  -> packaged-smoke
  -> release-report
```

### 20.1 Worker 검증

- onedir 구조, entrypoint, runtime 파일 확인
- `sys.base_prefix\Library\bin\ffi-8.dll` 자동 탐색 및 `_internal\ffi-8.dll` 포함
- source/bundle ffi SHA-256 일치
- 전체 payload manifest/hash
- user absolute path나 plaintext credential 부재
- packaged smoke

### 20.2 Setup 검증

- 최종 공급자 artifact가 `GPUWorkerSetup.exe` 하나인지 확인
- embedded manifest/payload hash
- elevated helper action allowlist
- production Authenticode gate
- `--verify-package` read-only smoke

`--verify-package`는 embedded payload 검증과 private temporary staging hash 재검증만 수행하며 시스템
feature, registry, credential, installed Worker를 변경하지 않는다. 실제 LocalAppData 설치 검증은 disposable
user/VM 또는 승인된 hardware E2E에서 수행한다.

## 21. 구현 책임 단위

예상 신규 구조:

```text
setup/
  gpu_worker_setup/
    app.py
    orchestrator.py
    issues.py
    defaults.py
    setup_state.py
    endpoint_config.py
    version_policy.py
    logging.py
    credentials/
    platform/
    remediation/
    installer/
    worker_control/
    ui/
  tests/
  GPUWorkerSetup.spec

worker/gpu_worker/
  protected_credentials.py
  instance_guard.py
  shutdown_control.py
  setup_probe.py

scripts/
  build-release.ps1
  verify-worker.ps1
  generate-worker-manifest.ps1
  verify-setup.ps1
  packaged-smoke.ps1
```

정확한 파일 목록과 interface signature는 implementation plan에서 현재 저장소 관례에 맞춰 고정한다.

## 22. 구현 순서

1. SetupIssue, defaults, state, endpoint, version policy
2. CredentialProvider와 DPAPI
3. Windows adapter와 fake
4. Setup/Worker mutex와 operation lock
5. payload manifest와 UpdateSource
6. update transaction, lock 검사와 rollback
7. elevated helper allowlist
8. Docker 동의, official per-user install과 startup wait
9. Worker protected credential, instance guard와 secure local control
10. Worker authoritative probe와 Backend registration verifier
11. Tkinter UI, resume와 AutoStart
12. Worker/Setup PyInstaller와 release scripts
13. 전체 unit/integration/packaged/hardware 검증

각 단위는 failing test, 최소 구현, 관련 test 통과, 회귀 test 순서로 진행한다.

## 23. Backend 변경 필요 여부

이번 범위의 Backend code와 WebSocket protocol은 변경하지 않는다. 기존 `/ws/workers`와
`/api/workers/{workerId}`를 사용한다. 향후 선택적 확장점은 다음과 같다.

- enrollment/credential validation endpoint
- Worker status response의 GPU UUID
- minimum/recommended/latest Worker version policy endpoint
- status endpoint 인증 policy

이들은 현재 구현의 필수 조건이 아니다. 특히 GPU UUID가 Backend 응답에 없는 현재 제약은 7.3의 결합
검증으로 처리한다.

## 24. 실제 Windows 수동 검증

다음은 실제 Windows + NVIDIA GPU + Docker 환경 또는 disposable VM에서 확인한다.

- Python/venv 없는 supplier-like user에서 Setup onefile 실행
- UAC 승인/거절과 재부팅 후 resume
- Docker 약관 미동의 시 무변경
- 공식 per-user Docker 설치와 Authenticode
- Docker 로그인 직후 startup delay/backoff
- 실제 NVML과 Docker GPU probe
- Benchmark cache miss/hit
- REGISTER, Backend ONLINE, IDLE, current Benchmark
- Setup/Worker 동시 실행 방지
- AutoStart, 해제와 update race
- locked-file update와 rollback
- production Setup/Worker Authenticode 서명
- 새 Setup EXE를 이용한 정상 update와 downgrade 차단

## 25. 완료 및 보고 기준

| 상태 | 의미 |
|---|---|
| `IMPLEMENTED` | 해당 code와 설정이 저장소에 존재 |
| `TESTED` | unit/integration test가 실제 실행되어 통과 |
| `HARDWARE_VERIFIED` | 실제 Windows + NVIDIA GPU + Docker에서 검증 |
| `MANUAL_FALLBACK_REQUIRED` | 외부 환경, 서명, BIOS, 조직 policy 등으로 자동 검증 미완료 |

실제 GPU에서 Benchmark, REGISTER, Backend ONLINE과 IDLE까지 성공한 경우에만 그 흐름을
`HARDWARE_VERIFIED`로 보고한다. code가 있거나 mock test만 통과한 항목은 hardware verified로 표시하지
않는다. 서명 인증서가 없으면 Authenticode verification code가 tested여도 production signing은
`MANUAL_FALLBACK_REQUIRED`이다.

## 26. 변경 불가 보안 불변조건

- 기존 WebSocket REGISTER와 Backend 인증 protocol을 변경하지 않는다.
- 신규 Setup은 평문 Token을 생성하지 않는다.
- Token과 secret을 helper, CLI, environment, state, log에 넣지 않는다.
- 일반 Setup 전체를 관리자 권한으로 실행하지 않는다.
- elevated helper는 enum allowlist 밖의 command를 실행하지 않는다.
- Docker 약관 명시적 동의 없이 `--accept-license`나 installer를 실행하지 않는다.
- manifest/hash/canonical/reparse 검증 실패 payload를 실행하지 않는다.
- PyInstaller temporary extraction 위치에서 Worker를 실행하지 않는다.
- BUSY Worker를 강제 update하지 않는다.
- update 중 부분 overwrite를 하지 않는다.
- arbitrary downgrade를 허용하지 않고 rollback journal 예외만 허용한다.
- local control은 Current User ACL의 typed command만 처리한다.
- Docker container security와 WorkloadCatalog allowlist를 약화하지 않는다.
- TLS verification을 끄는 production option을 제공하지 않는다.
- SETUP_COMPLETED는 실제 Backend REGISTER 검증 후에만 기록한다.
