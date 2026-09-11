# JOB_RESULT / JOB_FAILED — 결과 업로드와 종료 처리

컨테이너가 멈춘 뒤 Job을 끝내는 경로. 결과를 저장하고, 에스크로를 정산하고, 그 다음에야 Backend에 알린다.

기준: `a31b663` develop · Backend `WorkerJobController`·`WorkerJobService`·`JobService` · `blockchain/abi/GPUJobEscrow.json` (`sourceCommit 87055d6`)

> **이번 단계는 자연 종료 후 처리까지다.** USER_REQUEST 취소 · 실행 중 container stop · 일반 JOB_CANCEL(System 10)은 구현하지 않았다.

---

## 1. 두 흐름

**Success**

```
RuntimeOutcome FINISHED
 → output 3개 파일 검증 (symlink·경로 탈출·빈 파일 거부)
 → result.tar (deterministic)
 → streaming SHA-256
 → POST result-upload            → objectKey · uploadUrl
 → PUT signed URL (streaming)
 → POST result-upload/complete   → Backend가 직접 stat·hash
 → submitResult(jobId, 0x<sha256>)
 → jobs() 재조회: COMPLETED + 우리 operator + 우리 hash
 → JOB_RESULT
 → JobSlot release
```

**Failure**

```
RuntimeOutcome FAILED
 → failure 분류
 → jobs() 조회
 → ASSIGNED + 우리 operator일 때만 abortJob
 → jobs() 재조회: CANCELLED
 → JOB_FAILED
 → JobSlot release
```

### 순서를 바꾸지 않는 이유

| 순서 | 바꾸면 |
|---|---|
| 체인이 **업로드 뒤** | `submitResult`는 되돌릴 수 없는 hash를 쓴다. 뒤에 아무것도 없는 숫자가 체인에 남는다 |
| 메시지가 **체인 뒤** | JOB_RESULT는 "끝났고 정산됐다"는 뜻이다. 예치금이 아직 묶인 채 보내면 Backend 기록과 돈이 어긋난다 |
| slot release가 **전송 뒤** | slot은 이 Worker의 Job 소유권이다. 보고 중에 놓으면 다음 Job이 workspace를 덮어쓴다 |

---

## 2. Backend 계약 — 실제 코드 기준

### `POST /api/worker/jobs/{jobId}/result-upload`

요청 (`ResultUploadRequest`, 둘 다 필수)

| 필드 | 제약 |
|---|---|
| `fileName` | `^[A-Za-z0-9._-]{1,128}$` — 경로 구분자·상위 이동 불가 |
| `contentType` | `@NotBlank` |

응답 (`ResultUploadResponse`): `objectKey` · `uploadUrl` · `expiresAt`

**objectKey는 Backend가 만든다** — `"jobs/" + jobId + "/" + fileName`. Worker가 지어내서 주장하지 않는다. 완료 검증에서 같은 접두어를 다시 확인한다.

조건: `job.status == RUNNING` 이고 이 Worker에게 배정된 Job.

### `PUT {uploadUrl}`

`LocalObjectStorage.presignUpload`는 **method·objectKey·만료만 HMAC 서명하고 contentType은 버린다.** `StorageController`는 `request.getInputStream()`을 그대로 파일로 쓴다.

→ 보내는 헤더는 `Content-Length`와, 티켓을 요청할 때 쓴 그 `Content-Type`뿐이다. **Authorization 헤더는 넣지 않는다** — URL의 서명이 인증이고, 토큰을 하나 더 두는 셈이 된다.

### `POST /api/worker/jobs/{jobId}/result-upload/complete`

요청 (`ResultUploadCompleteRequest`, 전부 필수)

| 필드 | 제약 |
|---|---|
| `objectKey` | `@NotBlank`, `jobs/{jobId}/` 로 시작해야 함 |
| `sizeBytes` | `@Positive Long` |
| `sha256` | `^[0-9a-fA-F]{64}$` — **`0x` 없이** |

응답: `jobId` · `objectKey` · `verified`

**verified SHA를 돌려주지 않는다.** `completeResultUpload`가 오브젝트를 직접 `stat`하고 자기가 SHA-256을 계산해 크기·해시가 다르면 `RESULT_UPLOAD_MISMATCH`로 **던진다.** 그래서 **200이 곧 검증**이고, `verified`는 주석대로 항상 true다. Worker는 없는 필드를 요구하지 않고 성공 상태를 기준으로 삼는다.

### JOB_RESULT (`handleJobResult` → `completeJob`)

```json
{"type":"JOB_RESULT","jobId":"123","timestamp":"2026-09-11T05:00:00Z",
 "payload":{"objectKey":"jobs/123/result.tar",
            "resultHash":"0xbd1c0f…",
            "submitTxHash":"0x7e8fae…",
            "actualRuntimeSec":982.4}}
```

| 필드 | 읽는 방법 | 컬럼 |
|---|---|---|
| `objectKey` | `textOrNull` | `VARCHAR(512)` |
| `resultHash` | `textOrNull` | `VARCHAR(255)` |
| `submitTxHash` | `textOrNull` | **`CHAR(66)`** |
| `actualRuntimeSec` | **`doubleOrNull`** | **`DOUBLE`** (V7이 INT에서 넓힘) |

`completeJob`은 상태를 옮기기 전에 `resultMismatch`로 **세 가지를 전부** 확인하고, 하나라도 어긋나면 완료시키지 않고 `flagReconciliation`만 남긴다:

1. `result-upload/complete` 기록이 있을 것 (`resultSha256 != null`)
2. `objectKey`가 검증된 것과 같을 것
3. `resultHash == "0x" + resultSha256` (대소문자 무시)

→ 이 메시지는 Backend가 믿어 주는 주장이 아니라, **Backend가 이미 측정한 값에 Worker가 동의하는 것**이다. 그래서 세 값 모두 앞 단계에서 받은 것을 그대로 쓴다.

> ⚠️ `actualRuntimeSec`는 **실수**다. JOB_PROGRESS의 `elapsedSec`(INT)와 다르다 — 다른 측정값이고 다르게 저장된다.

### JOB_FAILED (`handleJobFailed` → `failJob`)

```json
{"type":"JOB_FAILED","jobId":"123","timestamp":"2026-09-11T05:00:00Z",
 "payload":{"code":"CUDA_OOM","message":"the GPU ran out of memory during training",
            "abortTxHash":"0xb13330…","actualRuntimeSec":41.2}}
```

`failIfRunning`이 `failReason = "CODE: message"`로 합쳐 저장하고 2000자에서 자른다. `abortTxHash`는 `CHAR(66)`, `actualRuntimeSec`는 `DOUBLE`.

**code allowlist는 Backend에 없다.** 그래서 코드는 Worker의 것을 안정적으로 쓴다 — 컨테이너가 정한 것은 `RuntimeFailure` 그대로, 그 뒤 단계는 아래 두 개.

| 코드 | 언제 |
|---|---|
| `STORAGE_FAILURE` | 학습은 성공했으나 결과를 저장하지 못함 |
| `RESULT_SUBMIT_FAILED` | 저장은 됐으나 `submitResult` 트랜잭션이 거부됨 (체인은 여전히 ASSIGNED) |

둘 다 RUNNING 상태에서만 적용된다.

---

## 3. Result Artifact

**`result.tar` 하나. 파일 3개.**

```
adapter_config.json
adapter_model.safetensors
training_metrics.json
```

들어가지 않는 것: workspace 전체 · Dataset · Base Model · cache · logs · Worker 설정 · 하위 디렉터리 · **링크로 가리킨 무엇이든**.

### deterministic

| 항목 | 고정값 |
|---|---|
| 형식 | `USTAR` (PAX는 확장 헤더에 access/change time이 들어간다) |
| entry 순서 | `RESULT_FILES` 고정 — `iterdir()`는 순서가 없다 |
| entry 이름 | 파일명만. 경로 없음 |
| mtime | `0` |
| mode | `0644` |
| uid/gid | `0/0` |
| uname/gname | `""` |

같은 3개 파일이면 어느 기계에서 언제 만들어도 **바이트 단위로 같은 tar**가 나온다. 고객이 결과를 검증하려면 이 hash를 다시 계산해 체인의 값과 비교해야 하는데, 만든 시각이 들어간 아카이브는 매번 다른 숫자를 주고 그러면 변조와 구분할 수 없다.

### 경로 안전

파일마다 확인한다: 링크 아님 · 존재함 · regular file · **resolve한 경로의 부모가 output root** · 비어있지 않음 · 크기 상한 이내 · 읽힘.

링크 검사가 핵심이다. `adapter_config.json`이라는 이름의 링크가 공급자의 `.env`를 가리키면 나머지 검사는 전부 통과하고, 그대로 패키징돼 Backend에 올라가 고객에게 전달된다.

> 이 기계에서는 Windows Developer Mode가 꺼져 있어 symlink 테스트 3건이 skip된다. 로직은 그대로 있고 권한이 있는 환경에서 실행된다.

---

## 4. resultHash

```
resultHash = "0x" + SHA-256(업로드한 result.tar의 raw bytes)
```

- 로컬: 64 hex 소문자, `0x` 없음 → `complete` 요청의 `sha256`
- 체인: `0x` + 64 hex → `bytes32`
- Backend: `"0x" + job.resultSha256`과 대조

**디렉터리 hash 아님. 개별 adapter hash 아님. manifest hash 아님.** 업로드한 bytes와 hash 대상 bytes가 정확히 같다.

`bytes32`는 32바이트다. 길이가 다른 값은 encoder가 왼쪽 패딩해 파일의 digest가 아닌 숫자를 만들고, 그게 되돌릴 수 없는 컨트랙트에 쓰인다. 그래서 `_result_hash()`가 보내기 **전에** 거부한다.

SHA도 크기도 1 MiB씩 streaming으로 계산한다 — adapter는 수십~수백 MB다.

---

## 5. 트랜잭션 불확실성

`submitResult`는 System 6.5의 `_send_and_confirm` / `_what_the_chain_says`를 **그대로 재사용**한다. send가 raise해도 노드에 도달했을 수 있으므로 가정하지 않고 체인에 묻는다.

| submitResult 전 `jobs()` | 처리 |
|---|---|
| `COMPLETED` + 같은 hash | **성공으로 reconcile. 트랜잭션 없음.** txHash 지어내지 않음 |
| `COMPLETED` + 다른 hash | `RESULT_HASH_CONFLICT` → `Unsettled`. 보고 안 함 |
| `ASSIGNED` + 우리 operator | 전송 |
| `ASSIGNED` + 다른 operator | 거부 |
| `CANCELLED` | 거부 |
| 그 외 | 거부 |

전송 후 postcondition은 receipt만으로 끝내지 않는다 — `jobs()`를 다시 읽어 **state == COMPLETED · operator == 우리 · resultHash == 우리 hash** 셋이 맞아야 한다.

abort도 같다. `abort_escrow_job`이 자기 전제조건을 확인하므로 **blind abort도 duplicate abort도 없다**: 이미 CANCELLED면 트랜잭션 없이 reconcile, COMPLETED면 거부, 다른 operator면 거부.

### 성공 경로에서 submitResult가 거부됐을 때

```
state == ASSIGNED  또는  state 미상(컨트랙트가 revert)
  → abortJob 시도 (abort가 자기 전제를 다시 확인)
  → RESULT_SUBMIT_FAILED

state == CANCELLED / COMPLETED / FUNDED / 다른 operator
  → Unsettled. 아무것도 보내지 않는다
```

성공한 실행 밑에서 체인이 CANCELLED라면 이 Worker가 하지 않은 일이 일어난 것이다. abort는 "이미 취소됨"으로 성공 처리되어 JOB_FAILED가 나가고, 그 원인은 아무도 확인한 적이 없다. 그래서 보내지 않는다.

---

## 6. actualRuntimeSec

**정의: 학습 컨테이너 프로세스가 시작된 시점부터, 종료되어 RuntimeOutcome이 확정된 시점까지의 monotonic duration.**

| 포함 | 제외 |
|---|---|
| container startup | execution-context 조회 |
| 컨테이너 안의 모델 로딩 | Model 준비·다운로드 |
| QLoRA 학습 | Dataset 다운로드 |
| 컨테이너 자체 output 검증 | Docker image pull |
| 프로세스 종료 | artifact packaging · upload · 체인 · WebSocket |

`time.monotonic()`. wall clock 금지 — 호스트가 시간을 맞추면 길이가 바뀌고 음수도 나온다.

**컨테이너의 `runtimeSec`와 다르다.** 그쪽 시계는 Trainer가 시작할 때부터라 모델 로딩이 빠진다 — 실측으로 `training_started`가 4.95s, 첫 step이 18.45s였다. Scheduler가 예측하는 것은 공급자의 GPU가 묶여 있는 시간이고, GPU는 그 전부 동안 묶여 있다. 컨테이너의 값은 `training_metrics.json`에 진단·교차검증용으로 남는다.

**컨테이너가 시작되지 않은 실패는 `None`이다.** 0이 아니다 — 측정한 적 없는 값이지 0초를 측정한 게 아니고, Backend 컬럼은 nullable이다.

---

## 7. Terminal 전달

```
Settlement (background thread)
  → TerminalChannel  (한 칸, 손실 없음)
  → connection loop  send_terminal()
  → JOB_RESULT / JOB_FAILED
  → JobSlot.release()
```

`ProgressChannel`과 **정반대**다:

| | Progress | Terminal |
|---|---|---|
| 성질 | 상태 | 사건 |
| 새 값 | 이전 것을 덮음 | 같은 Job이면 무시 |
| 읽기 | `take()`가 소비 | `take()`는 소비하지 않음 |
| 비우기 | 자동 | 전송 성공 후 `done()`만 |
| 유실 | 허용 (2초 뒤 다시 맞음) | **금지** (Job을 잃는다) |

- 업로드·트랜잭션은 분 단위라 connection loop가 아니라 `Work.SETTLE` background thread에서 돈다. 그 동안 heartbeat·ACK·JOB_CANCEL 수신은 계속된다
- 전송 실패 시 답을 버리지 않는다. 재연결 후 다시 보낸다
- `Unsettled`는 채널에 넣지 않는다 — 체인과 Worker가 어긋난 경우이고, 둘 중 아무 메시지나 보내면 확인된 적 없는 사실이 Backend 행에 기록된다
- 같은 Job에 대해 settlement가 두 번 돌지 않는다: 답이 아직 안 나갔으면 `start_settlement`가 거부한다

---

## 8. 보안

**로그 금지** — signed upload URL · query signature · Worker token · User token · operator private key · Registry/Docker credential. `result_upload`의 모든 `_debug` 호출은 URL·토큰 식별자를 참조하지 않는다(테스트가 AST로 고정).

**로그 가능** — jobId · objectKey · artifact size · sha256 앞 12자 · HTTP status · tx hash.

**Artifact에 없는 것** — Dataset · signed URL · token · private key · `.env` · model cache · Docker credential · logs · 임의 workspace 파일.

**JOB_FAILED에 raw stderr 없음.** `_said_why`는 컨테이너의 `failed` 이벤트 메시지(보여주려고 쓴 문장)만 쓰고 stderr는 쓰지 않는다. 메시지는 한 줄로 평탄화하고 200자에서 자른다.

**메모리 상한** — tar 생성은 파일 기반, SHA는 1 MiB chunk, PUT은 열린 파일을 Content-Length와 함께 넘긴다. 40 MB adapter가 40 MB의 프로세스가 되지 않는다.

**URL scheme** — `check_upload_url`이 http/https만 허용한다. `file://`은 이 프로세스가 자기 디스크에 쓰게 만든다.

---

## 9. Local Integration — 실측

실제 Hardhat 노드 + 저장소의 실제 `GPUJobEscrow` + 실제 `Web3Escrow`/`Settlement`, Backend만 `completeResultUpload`와 같게 동작하는 로컬 HTTP 서버로 대체. Runtime output은 **System 8 GPU smoke가 실제로 만든 40,422,168 바이트 adapter**.

**29/29 PASS**

| | |
|---|---|
| result.tar | 40,427,520 bytes |
| 서버가 계산한 sha256 == 로컬 sha256 | ✓ |
| 체인 `bytes32` == 업로드한 bytes의 hash | `0xbd1c0fcb8b59…` |
| 체인 상태 | COMPLETED, operator 일치 |
| provider 지급 | +9.0 GC |
| 같은 Job 재정산 | 트랜잭션 0건, nonce 그대로, txHash 지어내지 않음 |
| 실패 경로 | CANCELLED, client 환불 +9.0 GC |
| 실패 재정산 | abort 0건, abortTxHash 지어내지 않음 |
| 변조된 업로드 | 체인에 쓰지 않음 → 예치금 반환 → `STORAGE_FAILURE` |

---

## 10. System 10과의 경계

이번 단계는 **Runtime이 자연 종료한 뒤**만 다룬다. 다음이 필요하다:

- `JOB_CANCEL` `reason=USER_REQUEST` 처리
- 실행 중 container stop / process 종료
- 중간 산출물 처리
- 자연 성공과의 race — 컨테이너가 막 끝났는데 취소가 오는 경우
- `submitResult`와의 race — 정산 중 취소가 오는 경우
- 취소된 Job의 `actualRuntimeSec`

`ACCEPT_VALIDATION_FAILED` 경로(6.5)는 그대로 두었다. 공통으로 쓰는 것은 `abort_escrow_job` primitive 하나뿐이고, 로직을 섞지 않았다.
