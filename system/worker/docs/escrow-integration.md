# Worker ↔ Escrow 연동 규격

Worker가 온체인에서 Job을 수락하는 방법. **Backend·Blockchain과 공유하는 계약**이다.

기준: `blockchain/abi/GPUJobEscrow.json` (`sourceCommit 38b0cfa`, solc 0.8.28) · `blockchain/docs/03-에스크로-연동-규격.md`

> **현재 실제 연동은 양쪽 모두 BLOCKED다.** 아래 [8절](#8-현재-차단-상태)을 먼저 읽을 것.

---

## 1. provider와 operator는 다른 주소다

```
provider   users.wallet_address       사람 지갑(MetaMask). 대금이 여기로 간다
operator   workers.operator_address   Worker 기계. 서명만 한다. 대금과 무관
```

공급자 쪽 함수는 사람 없이 자동으로 불려야 해서 프로그램이 키를 들고 있어야 하고, 그 키를 사람의 MetaMask와 같게 둘 수 없어 계약이 둘을 나눴다.

> **이전 구현은 `job.provider == 로컬 지갑`을 검사했다.** 최종 설계에서 이 조건은 **참이 될 수 없다** — 완성된 시스템이었다면 모든 Job을 거절했을 것이다. 지금은 `isOperator(provider, operator)`를 온체인에서 읽는다.

승인은 소유자가 MetaMask로 한 번 한다: `setOperator(workerAddress, true)`.

---

## 2. Operator Key

| | |
|---|---|
| 생성 | Worker가 첫 실행 때 |
| 보관 | 이 PC, **Windows DPAPI** (`worker-wallet.json`의 `privateKeyProtected`) |
| 등록 | **공개 주소만** Backend로 |
| 서명 | Worker 호스트 프로세스. 코드상 **정확히 한 줄** |

**개인키가 가지 않는 곳**: Backend · WebSocket · 로그 · 예외 메시지 · Docker 환경변수 · 학습 컨테이너 · 마운트.

Backend가 대신 서명하지 않는 이유 둘: 서버가 침해돼도 모든 Worker의 서명 권한이 한꺼번에 넘어가지 않아야 하고, `JobAccepted.operator`가 **실제로 서명한 기계**를 가리켜야 온체인 기록이 Backend 신뢰에 기대지 않는다.

### 키를 잃으면

DPAPI는 키를 Windows 계정과 기계에 묶는다. 재설치·기계 교체로 잃으면 **기존 ASSIGNED Job은 끝낼 수 없다** — `submitResult`·`abortJob` 둘 다 `msg.sender == job.operator`를 본다. 그 Job들은 시한 만료 환불로 간다.

### 기존 Worker 이관

| 상황 | 결과 |
|---|---|
| Backend에 주소 없음 · 진행 중 Job 없음 | `PUT /{workerId}/operator`로 등록 |
| Backend 주소 == 로컬 주소 | 아무것도 하지 않음 |
| Backend 주소 ≠ 로컬 주소 | **거부.** 덮어쓰지 않는다 |
| 진행 중 Job 있음 | **거부.** rotation 금지 |

세 번째가 중요하다. 조용히 덮어쓰면 옛 키로 수락한 Job을 **아무도 끝낼 수 없게** 되고, 예치금은 execution timeout까지 잠긴다.

---

## 3. JobState는 다섯이다

```
0 NONE   1 FUNDED   2 ASSIGNED   3 COMPLETED   4 CANCELLED
```

이전 구현은 `FUNDED = 2`였다(계약이 단순화되기 전 revision). 그 해석에서는 **2가 ASSIGNED인데 FUNDED로 읽혀**, 남이 이미 가져간 Job이 아직 비어 있는 것처럼 보인다.

이제 `escrow_abi.check_contract_matches()`가 artifact의 `stateEnum`과 Worker의 enum을 대조하고, 다르면 **테스트가 실패한다.**

## 4. `jobs(jobId)`는 일곱 필드다

```
client, state, deadline, provider, operator, price, resultHash
```

`operator`가 **다섯째**로 들어오면서 이전 6필드 디코더의 `row[4]`(= price)가 주소를 읽게 됐다. 코드는 위치 대신 이름으로만 접근한다.

> `externalRef`는 **구조체에 없다.** `JobFunded` 이벤트에만 실린다.

---

## 5. JOB_ASSIGN — Escrow 5필드

기존 runtime 필드에 더해 **다섯이 필수**다.

```json
{
  "type": "JOB_ASSIGN",
  "jobId": "123",
  "timestamp": "2026-09-11T01:00:00Z",
  "payload": {
    "dockerImageId": 3, "modelId": 1, "datasetId": 42,
    "trainingMode": "AUTO",
    "trainingConfig": { "epochs": 1, "batchSize": 16,
                        "learningRate": 0.0002, "maxLength": 512 },

    "escrowJobId":    "17",
    "externalRef":    "0x0123...(0x + 64 hex)",
    "price":          "9000000000000000000",
    "clientWallet":   "0x1111111111111111111111111111111111111111",
    "providerWallet": "0x2222222222222222222222222222222222222222"
  }
}
```

| 필드 | 형식 | 비고 |
|---|---|---|
| `escrowJobId` | **10진 문자열** | uint256. **DB jobId가 아니다** |
| `externalRef` | `0x` + 64 hex | bytes32. `JobFunded` 이벤트에서 옴 |
| `price` | **10진 문자열** | wei. JSON number 금지 |
| `clientWallet` | address | |
| `providerWallet` | address | 소유자 정산 주소 |

**하나라도 없거나 형식이 틀리면 fail closed.** Escrow 수락을 시작조차 하지 않는다.

> **fallback은 없다.** DB jobId → escrowJobId 대체, price 자체 계산, providerWallet → 로컬 주소, clientWallet 더미, externalRef 유도, execution-context로 누락 보완 — 전부 구현하지 않았고 테스트가 그것을 검사한다.
>
> `escrowJobId`가 특히 위험하다. DB jobId와 온체인 jobId는 **둘 다 1부터 올라가서 개발 중에는 우연히 같다.** 바꿔 쓴 구현은 두 수열이 어긋나는 날까지 정상으로 보이다가, 그날 남의 Job을 수락한다.
>
> `price`를 JSON number로 보내면 **거절한다.** 고쳐서 받지 않는다 — JavaScript number를 지난 uint256은 이미 자릿수를 잃었고, 그 값은 완벽하게 그럴듯해 보인다.

---

## 6. 수락 전 검증

서명 **전에** 전부 통과해야 한다. 하나라도 실패하면 **트랜잭션을 보내지 않는다.**

| # | | 어디서 |
|---|---|---|
| 1 | 인증된 WebSocket session | 연결 |
| 2 | JobSlot 예약 | 로컬 |
| 3 | 다른 active Job 없음 | 로컬 |
| 4–8 | 5필드 형식 | JOB_ASSIGN |
| 9 | chain id == 31221 | 설정 |
| 10 | 그 주소에 contract 있음 | 온체인 |
| 11 | `jobs(escrowJobId)` 존재 | 온체인 |
| 12 | **`JobFunded.externalRef` 일치** | **이벤트 로그** |
| 13 | `job.client == clientWallet` | 온체인 |
| 14 | `job.provider == providerWallet` | 온체인 |
| 15 | **`job.price == price`** | 온체인 |
| 16 | `job.state == FUNDED(1)` | 온체인 |
| 17 | deadline 안 지남 — **블록 시각 기준** | 온체인 |
| 18 | operator key 사용 가능 (DPAPI 복호화 + 주소 파생) | 로컬 |
| 19 | 파생 주소 == 등록된 operatorAddress | 로컬 |
| 20 | **`isOperator(provider, operator) == true`** | 온체인 |

**가격 대조(15)가 유일한 방어선이다.** 계약이 가격을 강제하지 않는다 — 의뢰자가 트랜잭션에 직접 적어 보내므로 1 GSC로 적어 놓고 A100을 지정할 수 있다. 정수로 정확히 비교한다.

**deadline(17)은 로컬 시계를 쓰지 않는다.** 1분 빠른 Windows PC는 계약이 받아 줬을 Job을 거절하고, 공급자는 이유도 모른 채 일감이 지나가는 걸 본다.

**`externalRef`(12)를 나머지보다 먼저 본다.** 그게 없으면 client·provider·price를 *다른 Job*과 비교하게 되고, 셋 다 우연히 맞을 수도 있다.

---

## 7. 수락 · 거절 · 반납

### acceptJob

```
검증 20개 통과
  → acceptJob(escrowJobId)  ← operator key로 서명
  → receipt (또는 timeout)
  → jobs() 재조회
  → state == ASSIGNED AND operator == 로컬
  → 그제서야 JOB_ACCEPTED
```

**영수증만으로 성공을 선언하지 않는다.** 영수증은 트랜잭션이 성공했다고 말하고, 재조회는 *이 기계가* ASSIGNED Job의 operator라고 말한다. Backend에 알리기 전에 참이어야 하는 건 후자다.

**불확실한 결과는 실패로 단정하지 않는다.** send가 예외를 던져도 노드에는 닿았을 수 있어 체인에 되묻는다 — 실패로 단정하면 같은 Job에 두 번째 트랜잭션을 보내게 된다.

### JOB_ACCEPTED

```json
{ "type": "JOB_ACCEPTED", "jobId": "123", "timestamp": "...",
  "payload": { "escrowJobId": "17", "operatorAddress": "0x...", "txHash": "0x..." } }
```

`txHash`는 **트랜잭션이 없었으면 생략**한다(이미 온체인에서 우리 것이었던 경우). null로 보내지 않는다.

### JOB_REJECTED

```json
{ "type": "JOB_REJECTED", "jobId": "123", "timestamp": "...",
  "payload": { "code": "PRICE_MISMATCH", "message": "..." } }
```

| code | 재시도 |
|---|---|
| `BLOCKCHAIN_RPC_FAILED` | ✅ **유일하게** transient |
| `INVALID_ASSIGNMENT` · `ASSIGNMENT_MISMATCH` | ❌ |
| `ESCROW_JOB_NOT_FOUND` · `EXTERNAL_REF_MISMATCH` | ❌ |
| `CLIENT_MISMATCH` · `PROVIDER_MISMATCH` · `PRICE_MISMATCH` | ❌ |
| `INVALID_ESCROW_STATE` · `ACCEPT_DEADLINE_EXPIRED` | ❌ |
| `OPERATOR_KEY_UNAVAILABLE` · `OPERATOR_ADDRESS_MISMATCH` · `OPERATOR_NOT_APPROVED` | ❌ |
| `ESCROW_NOT_CONFIGURED` | ❌ 이 Worker에 escrow 설정이 없음 |
| `TRANSACTION_FAILED` | ❌ |

**mismatch를 다른 값으로 덮어서 재시도하지 않는다.**

### JobSlot 해제

| 결과 | 슬롯 |
|---|---|
| ACCEPTED | 확정 보유 |
| REFUSED (확정적 no) | **해제** + JOB_REJECTED |
| UNCERTAIN (tx가 아직 살아 있을 수 있음) | **유지**, JOB_REJECTED도 안 보냄 |

### abortJob — capability만

전제: `state == ASSIGNED` **AND** `operator == 로컬`. 성공 후 `state == CANCELLED` 재확인.

**자동 호출은 없다.** Runtime failure·JOB_FAILED·JOB_CANCEL·사용자 취소 어디에도 연결되지 않았다. 개발 중 테스트 Job이 예치금을 30시간 묶지 않도록 하는 용도이고, 7번·9번이 재사용한다.

---

## 8. 현재 차단 상태

### Blockchain

| | |
|---|---|
| `deployed` (artifact) | **false** |
| SSAFY 체인(31221) | **미배포.** 생성자가 `SsafyDeploymentBlocked()`로 되돌림 |
| 배포된 것 | GCoin뿐 |

→ **SSAFY Chain Integration = BLOCKED.** Worker 검증은 전부 fake chain 또는 local chain이다.

### Backend

`be-feature/worker-contract-v1`은 **원격에 존재하지 않는다.** develop 기준 실제 구현:

| 필요 | develop 현재 |
|---|---|
| register에 `operatorAddress` | ❌ `deviceName`·`driverVersion` 평면 구조 |
| authenticate **body** token | ❌ **header** 토큰 (`AuthenticateWorkerRequest`에 workerId만) |
| `PUT /{workerId}/operator` | ❌ 없음 |
| `PUT /{workerId}/benchmark` | ❌ `PUT /{workerId}/gpu` |
| JOB_ASSIGN 5필드 | ❌ 없음 |

→ **Backend Live Integration = BLOCKED.**

> Worker는 **최종 합의 계약**에 맞춰져 있다. 그래서 지금 develop에 떠 있는 Backend와는 **등록·인증이 통하지 않는다.** 의도된 것이고, Backend가 해당 브랜치를 올리면 맞는다.

### Backend Integration Requirements

1. `POST /register` — `operatorAddress` 필수 + `system`/`gpu`/`benchmark` 그룹
2. `POST /authenticate` — `workerToken`을 **body**로
3. `PUT /{workerId}/operator` 신설
4. `PUT /{workerId}/benchmark` (`/gpu` 대체, `driverVersion` 제외, `benchmarkVersion` 포함)
5. **JOB_ASSIGN에 5필드** — 없으면 Worker는 수락 자체를 시작하지 않는다
6. `jobs.escrow_address` / `escrow_job_id` / `funded_at`, `workers.operator_address`
7. `JobFunded` 이벤트 수신 → `externalRef` 저장 → JOB_ASSIGN에 실어 보내기
8. **키퍼** — 가스 0 체인이라 `settleTimeout`을 부를 주체가 없다
9. 금액: DB는 TOKEN(BIGINT), 온체인은 wei, 변환은 `BigInteger`
10. **JOB_ACCEPTED 검증** — 현재 escrowJobId mismatch를 warning만 남기고 RUNNING으로 넘긴다고 보고됨. mismatch면 RUNNING 금지, tx receipt·on-chain `state == ASSIGNED`·`operator` 확인 필요
11. 후보 필터에 **on-chain `isOperator`** 확인 (현재는 주소 존재 여부만)

---

## 9. 이 단계에서 하지 않은 것

Dataset download · Model download · Runtime image pull · Docker 실행 · QLoRA Runner 연결 · JOB_PROGRESS · Artifact · resultHash · `submitResult` · JOB_RESULT · JOB_FAILED workflow · JOB_CANCEL workflow · PyInstaller.

`tests/test_escrow.py`와 `tests/test_escrow_terms.py`가 위 항목들이 소스에 없다는 것을 검사한다.
