# JOB_CANCEL — 사용자 취소와 terminal ownership

고객이 실행 중인 Job을 멈췄을 때, 컨테이너를 세우고 예치금을 돌려준 뒤 Backend에 알린다. 그리고 **성공·실패·취소가 동시에 Job을 끝내려 할 때 하나만 이기게 한다.**

기준: `7593c72` develop · Backend `JobService.cancelJob`·`handleJobCancelled`·`JobRepository` · `blockchain/abi/GPUJobEscrow.json`

> **이번 단계는 USER_REQUEST까지다.** process restart 후 복구는 구현하지 않았다 — 같은 Worker 프로세스 수명 안에서의 취소를 완성한다.

---

## 1. 두 가지 취소는 다르다

| | `ACCEPT_VALIDATION_FAILED` (6.5) | `USER_REQUEST` (이번) |
|---|---|---|
| 누가 | Backend가 JOB_ACCEPTED를 거부 | 고객이 취소 |
| 언제 | 실행 전 | 준비 중 또는 학습 중 |
| 컨테이너 | 없음 | 있을 수 있음 → 중단 필요 |
| 경쟁 | 없음 | 성공·실패와 경쟁 |
| 코드 경로 | `start_cleanup` (그대로 둠) | `start_cancellation` |

**공유하는 것은 `abort_escrow_job` primitive 하나뿐이고, 로직은 섞지 않았다.**

`reason`이 없거나 모르는 값이면 **둘 다 아니다.** 두 경로 모두 고객에게 환불하는 트랜잭션으로 끝나므로, 못 알아듣는 단어를 USER_REQUEST로 기본값 처리하면 추측으로 일을 내주는 것이 된다.

---

## 2. Backend 계약 — 실제 코드 기준

### JOB_CANCEL (Backend → Worker)

`JobService.cancelJob`이 보낸다:

```
CREATED / FUNDED   → 바로 CANCELLED (워커에게 알리지 않음)
ASSIGNED / RUNNING → CANCELLING + sendCancel(workerId, jobId, "USER_REQUEST")
그 외              → INVALID_JOB_STATUS
```

```json
{"type":"JOB_CANCEL","jobId":"123","timestamp":"...","payload":{"reason":"USER_REQUEST"}}
```

`markCancellingIfActive`는 `status IN (ASSIGNED, RUNNING)`일 때만 움직인다. 답이 없으면 CANCELLING에 남아 온체인 대조 대상이 된다 — **Backend는 Worker의 응답을 보장된 것으로 보지 않는다.**

### JOB_CANCELLED (Worker → Backend)

`handleJobCancelled` → `confirmCancelled` → `confirmCancelledIfCancelling`

```json
{"type":"JOB_CANCELLED","jobId":"7001","timestamp":"2026-09-11T05:00:00Z",
 "payload":{"reason":"USER_REQUEST",
            "abortTxHash":"0x54f46e…",
            "actualRuntimeSec":42.5}}
```

| 필드 | 읽는 방법 | 컬럼 |
|---|---|---|
| `jobId` | envelope, `Long.parseLong` | — |
| `abortTxHash` | `textOrNull` | `CHAR(66)` |
| `actualRuntimeSec` | **`doubleOrNull`** | **`DOUBLE`** |
| `reason` | `textOrNull` — **로그 줄에만 쓰인다** | 저장 안 함 |

전이 조건: `status == CANCELLING` **이고** 이 Worker의 Job. 아니면 warn만 남기고 무시한다.

`actualRuntimeSec`는 실수다 — JOB_RESULT·JOB_FAILED와 같고, JOB_PROGRESS의 `elapsedSec`(INT)와 다르다.

---

## 3. Terminal ownership

이번 작업의 핵심이다. 한 Job을 끝내려는 것이 셋 있을 수 있다.

```
NONE ──claim──> RESULT     submitResult 직전
     ──claim──> FAILURE    abortJob 직전 (실행 실패)
     ──claim──> CANCEL     abortJob 직전 (고객 요청)
```

**claim은 체인 트랜잭션 바로 직전에 잡고, 돌려주지 않는다.** 그 앞의 모든 것 — 아카이브 생성, 업로드, 심지어 컨테이너 중단 — 은 되돌릴 수 있고 트랜잭션은 아니다. compare-and-set 하나이고, 12개 스레드가 동시에 달려들어도 정확히 하나만 이긴다.

### intent와 claim은 다르다

| | intent (`request_cancel`) | claim (`claim_for`) |
|---|---|---|
| 언제 | JOB_CANCEL 수신 즉시 | 트랜잭션 직전 |
| 성격 | **권고** | **권한** |
| 효과 | pipeline·settlement가 다음 checkpoint에서 멈춤 | 체인에 쓸 자격 |

RESULT가 이미 claim한 뒤 도착한 취소도 **진짜 취소다.** 다만 졌으므로, 체인에 쓰는 대신 읽는다.

`isCancelling`/`isSettling`/`isDone` 같은 플래그를 따로 두지 않았다. 서로 어긋날 수 있고, 어긋남은 트랜잭션 두 건으로 나타난다. 한 객체, 한 락이다.

---

## 4. 각 경쟁의 결과

| 상황 | 결과 |
|---|---|
| 학습 전 취소 | 컨테이너 시작 안 함 · abortJob · JOB_CANCELLED |
| 학습 중 취소 | docker stop · abortJob · JOB_CANCELLED |
| 성공했지만 settlement 시작 전 취소 | packaging·upload·submitResult 전부 없음 · abortJob · JOB_CANCELLED |
| packaging 중 취소 | 다음 checkpoint에서 중단 · 업로드 안 함 |
| upload 중 취소 | 업로드는 끝나되 **submitResult 없음** · orphan object 보고 |
| submitResult가 claim한 뒤 취소 | **abort 금지.** 체인을 읽어 판단 |
| 이미 COMPLETED | abort 금지 · JOB_CANCELLED 금지 · RESULT 보존 |
| 이미 CANCELLED (FAILURE가 주인) | JOB_FAILED 유지 · reason 덮어쓰기 금지 |
| 이미 CANCELLED (CANCEL이 주인) | 중복 abort 금지 · JOB_CANCELLED reconcile |
| 컨테이너가 안 멈춤 | **escrow 손대지 않음** |
| RPC 불확실 | 트랜잭션 없음 · 메시지 없음 · slot 유지 |

### 업로드 이후 취소가 남기는 것

claim은 업로드 **뒤에** 잡히므로, 스토리지에 객체가 남고 아무도 그 값을 지불하지 않는 상태가 생길 수 있다. Backend 계약에 삭제 endpoint가 없으므로 **System이 삭제 API를 지어내지 않고** 그대로 두고 보고한다.

---

## 5. 컨테이너 중단

**이름만으로 멈추지 않는다.** 이름은 job id에서 나오므로 같은 Job의 이전 실행이나 다른 Worker 프로세스가 만든 컨테이너도 같은 이름을 쓴다.

그래서 컨테이너에 label 두 개를 붙인다:

```
gshared.job-id=<safe job id>
gshared.generation=<이 실행의 번호>
```

`docker inspect`는 호스트의 누구나 읽을 수 있으므로 지갑·토큰·고객 데이터는 넣지 않는다. Worker 자신의 장부 두 줄뿐이다.

```
inspect → 라벨이 우리 것인가? → 아니면 NOT_OURS, 아무것도 보내지 않음
        → 맞고 실행 중이면 docker stop --time 20
        → inspect 다시 → 정말 사라졌는가?
```

| 결과 | 의미 | abort 가능? |
|---|---|---|
| `STOPPED` | 우리 것이었고 이제 없다 | 예 |
| `ALREADY_GONE` | 없다 (`--rm`으로 지워진 것 포함) | 예 |
| `NOT_OURS` | 이름은 같지만 우리 것이 아니다 | **아니오** |
| `STILL_RUNNING` | 아직 돈다 | **아니오** |
| `UNKNOWN` | Docker에 물을 수 없었다 | **아니오** |

`docker kill`을 먼저 쓰지 않는다 — SIGTERM을 받은 학습 프로세스는 파일을 닫고 CUDA 컨텍스트를 반납한다. grace는 상수 한 곳(`STOP_GRACE_SECONDS = 20`)이다.

**stop 명령의 exit code로 판단하지 않는다.** 명령이 0을 돌려준 것은 Docker가 시도했다는 뜻이고, escrow가 필요한 것은 "사라졌다"는 확인이다.

> ⚠️ **`STILL_RUNNING`에서 abort를 보내면 안 되는 이유**: 고객은 환불받고 공급자의 GPU는 계속 그 Job을 돌린다. 공급자가 일은 하고 돈은 못 받는 유일한 경로다.

---

## 6. 취소된 실행의 분류

`docker stop`으로 죽은 컨테이너는 137이나 143으로 끝난다. exit 테이블에 없는 코드라 `UNKNOWN_RUNTIME_EXIT`로 읽히고, 고객은 자기가 멈춘 Job이 **크래시했다고** 기록된 것을 보게 된다.

그래서 `RuntimeState.CANCELLED`를 추가했다. cancel token이 켜져 있고 exit가 non-zero면 CANCELLED이고, `failure`는 `None`이다.

**exit 0은 여전히 FINISHED다.** 취소가 진행 중이어도 그렇다 — 학습은 정말로 끝났고, 누가 Job의 끝을 소유하는지는 나중에 체인을 상대로 결정된다. 여기서 완료된 실행을 취소로 부르면 존재하는 결과를 버리게 된다.

---

## 7. actualRuntimeSec

System 9 정의 그대로. **컨테이너 프로세스 시작 → 종료 확인까지의 monotonic duration.**

취소된 경우 **stop을 요청한 시점이 아니라 프로세스가 실제로 끝난 시점까지** 포함한다. 종료 중에도 GPU는 그 Job에 묶여 있었다.

컨테이너가 시작되기 전에 취소됐으면 **`None`.** 0이 아니다 — 측정한 적 없는 값이다.

---

## 8. 전달

```
JOB_CANCEL 수신 (connection loop)
 → reason 확인 · 현재 Job인지 확인
 → request_cancel()  — 즉시 반환
 → [background] docker stop → runtime 대기 → claim → abortJob → CANCELLED 확인
 → TerminalChannel (한 칸, 손실 없음)
 → connection loop send_terminal()
 → JOB_CANCELLED
 → JobSlot.release()
```

- JOB_CANCELLED는 **System 9의 TerminalChannel을 그대로 쓴다.** RESULT·FAILURE·CANCEL 중 하나만 들어가고, 이미 든 답이 있으면 두 번째는 거부된다
- 전송 실패 시 답도 slot도 유지하고 재연결 후 재전송한다
- **background worker는 socket을 만지지 않는다** — `job_cancellation`·`runtime_stop`에 `RegisteredSocket`·`websocket`·메시지 빌더가 없다(AST로 고정)
- docker stop 대기·receipt 대기 중에도 heartbeat·ACK·수신 루프가 계속 돈다

**JobSlot은 JOB_CANCELLED가 실제로 나간 뒤에만 release된다.** 취소 수신 시점도, 컨테이너 중단 시점도, 트랜잭션 시점도, 체인 CANCELLED 확인 시점도 아니다.

---

## 9. 진행률

취소가 기록되는 순간 `ProgressChannel`을 비우고, `send_progress`가 게이트에서 막는다. 재연결 후 replay도 없다 — 고객에게 취소한 Job이 계속 진척되는 것처럼 보이면 안 된다.

---

## 10. Backend CANCELLING vs 체인 COMPLETED

**이 단계가 해결할 수 없는 유일한 경우다.**

체인이 COMPLETED면 Job은 끝났고 provider는 이미 지불받았다. 그런데 Backend는 CANCELLING이고, `completeIfRunning`은 `status == RUNNING`일 때만 움직인다 — **그 JOB_RESULT는 드롭된다.**

Worker가 할 수 있는 일:

- JOB_CANCELLED를 보내지 **않는다** (체인이 완료라고 말한다)
- JOB_FAILED를 보내지 **않는다** (실패하지 않았다)
- JOB_RESULT를 보내도 Backend가 무시한다

그래서 `BACKEND_CANCEL_COMPLETION_RACE`로 이름 붙여 보존하고 보고한다. 운영자가 맞춰야 한다. **거짓 JOB_CANCELLED는 보내지 않는다.**

> 이것은 **Backend 계약의 제약**이지 Worker의 결함이 아니다. 해소하려면 `completeIfRunning`이 CANCELLING도 받아들이거나 별도의 reconciliation 경로가 필요한데, 둘 다 Backend 변경이라 이번 범위 밖이다.

---

## 11. 중복

같은 Job에 JOB_CANCEL이 여러 번 와도 — 재연결 후 Backend가 다시 보낼 수 있다 —

| | 횟수 |
|---|---|
| `docker stop` | ≤ 1 |
| `abortJob` | ≤ 1 |
| JOB_CANCELLED | ≤ 1 |
| slot release | 1 |

`request_cancel`이 처음 한 번만 True를 돌려주는 것이 그 보장이다.

---

## 12. 실측

### Local Chain E2E — 24/24 PASS

실제 Hardhat + 실제 `GPUJobEscrow` + 실제 `Web3Escrow`/`Cancellation`.

| | |
|---|---|
| 학습 중 취소 → CANCELLED | client +9.0 GC 환불, provider 미지급 |
| 같은 Job 재취소 | 트랜잭션 0건, nonce 그대로, abortTxHash 미생성 |
| 컨테이너가 안 멈춤 | 체인 ASSIGNED 유지, 트랜잭션 0건 |
| 이미 COMPLETED | abort 0건, `BACKEND_CANCEL_COMPLETION_RACE` |
| RESULT가 먼저 claim | 체인 COMPLETED, 취소 미보고 |
| CANCEL이 먼저 claim | 체인 CANCELLED, 취소 보고 |

### Real GPU Cancel Smoke — 22/22 PASS

RTX 4070 Laptop · Qwen3-0.6B · 16 samples · 12 epochs (48 steps)

| | |
|---|---|
| T0 컨테이너 명령 | 0.00s |
| `training_started` | 4.95s |
| T1 첫 JOB_PROGRESS | 17.50s |
| **T2 USER_REQUEST** | **18.73s** |
| T3 docker stop 요청 | 18.73s |
| **T4 프로세스 종료** | **39.50s** |

**T1 < T2 < T4**, 72.92%에서 중단. stop 결과 `STOPPED`, exit 137 → `RuntimeState.CANCELLED`, `actualRuntimeSec` 39.5s. `result.tar` 없음, 부분 output 없음, 업로드 없음, submitResult 없음.

> **첫 시도는 실패했고 그게 이 smoke의 값어치다.** `docker inspect --format`에 탭 대신 `\` + `t` 두 글자가 들어가 Go 템플릿이 그대로 출력했고, 파서가 구분자를 못 찾아 label을 비워 읽었다. 결과는 `NOT_OURS` — **방금 우리가 띄운 컨테이너를 남의 것으로 판정**해 아무것도 멈추지 않았고 학습은 100%까지 갔다. 단위 테스트는 자기가 만든 진짜 탭으로 파서만 검사하고 포맷 문자열은 한 번도 파서에 통과시키지 않아서 놓쳤다. 이제 `FIELD_SEPARATOR` 하나를 양쪽이 공유하고, 그 사실 자체가 테스트로 고정돼 있다.

---

## 13. 보안

| 항목 | |
|---|---|
| 멈추는 컨테이너 | **검증된 우리 label을 가진 것만** |
| `shell=True` / `os.system` | **없음** |
| 임의 컨테이너 이름 | **없음** — 이름은 `container_name(job_id)`에서만 |
| Worker/User token · operator key · signed URL · Dataset | 취소 경로 어디에도 없음 |
| JOB_CANCELLED payload | 숫자 1개 + 해시 1개 + reason 1개 |
| raw stderr | 없음 |

로그에 남는 것: jobId · reason · generation · 컨테이너 상태 · 체인 상태 · tx hash · 시각.

---

## 14. 범위 밖 — 다음으로

**Process restart 복구는 구현하지 않았다.** Worker가 재시작되면 `JobTerminal`도 `RuntimeHandle`도 없다. `JobSlot`이 원래 메모리 전용이라(6번 작업부터) 재시작 후 Job을 기억하지 못하는 것은 기존 설계 그대로이고, 이번에 갑자기 바꾸지 않았다. 전체 E2E 단계에서 Backend의 CANCELLING 잔류분과 온체인 `settleTimeout`으로 다뤄야 한다.
