# JOB_PROGRESS — 학습 진행률 실시간 전송

컨테이너가 학습하는 **동안** stdout을 읽어 Backend에 진행률을 보낸다. System 7까지는 컨테이너가 끝난 뒤에야 stdout 전체를 파싱했다.

기준: `855bebd` develop · Backend `WorkerWebSocketHandler.handleJobProgress` · `JobRepository.updateProgressIfRunning` · `V10__worker_contract_v1.sql`

> **이번 단계는 진행률까지다.** JOB_RESULT / JOB_FAILED / result upload / submitResult(9), USER_REQUEST 취소 · container stop(10)은 구현하지 않았다.

---

## 1. 무엇이 바뀌었나

**System 7 (이전)**

```
docker run → 학습 완료 → 프로세스 종료 → stdout 전체 반환 → parse_events()
```

학습이 4시간이면 4시간 동안 Backend는 아무것도 모른다.

**System 8 (현재)**

```
docker run
 → stdout line streaming          AsyncSubprocessRunner.run_streaming
 → GSHARED_EVENT 즉시 parse        job_runtime.parse_event_line
 → RuntimeProgressEvent            runtime_progress.ProgressTracker
 → latest snapshot (1칸)           runtime_progress.ProgressChannel
 → 기존 WebSocket single owner     worker_connection._every_turn
 → JOB_PROGRESS                    worker_job.job_progress_message
```

---

## 2. Backend 계약 — 실제 코드 기준

`handleJobProgress` → `jobService.updateProgress` → `updateProgressIfRunning`

| 필드 | 읽는 방법 | 컬럼 | 필수 |
|---|---|---|---|
| `jobId` | **envelope**, `Long.parseLong` | — | 예 |
| `progressPercent` | `doubleOrNull` | `DOUBLE NULL` | 아니오 |
| `currentStep` | `intOrNull` | `INT NULL` | 아니오 |
| `totalSteps` | `intOrNull` | `INT NULL` | 아니오 |
| `currentEpoch` | `intOrNull` | `INT NULL` | 아니오 |
| `totalEpochs` | `intOrNull` | `INT NULL` | 아니오 |
| `elapsedSec` | **`intOrNull`** | **`INT NULL`** | 아니오 |

> ⚠️ **`elapsedSec`는 정수다.** 유통되던 규격 예시는 `123.4`지만 핸들러는 `intOrNull`(=`asInt()`)로 읽고 컬럼은 `INT`다. 실수를 보내면 조용히 잘린다. **코드가 계약이다.**

UPDATE 조건: `AND j.assignedWorkerId = :workerId AND j.status = RUNNING`. 그 밖의 상태에서는 Backend가 무시하고 warn만 남긴다 — **Worker는 Backend의 상태를 추측하지 않는다.**

```json
{
  "type": "JOB_PROGRESS",
  "jobId": "123",
  "timestamp": "2026-09-11T05:00:00Z",
  "payload": {
    "progressPercent": 42.5,
    "currentStep": 85,
    "totalSteps": 200,
    "currentEpoch": 2,
    "totalEpochs": 3,
    "elapsedSec": 123
  }
}
```

`timestamp`는 기존 `worker_socket.now_in_iso8601()`을 그대로 쓴다 (UTC ISO-8601 Z).

---

## 3. Runtime event 계약

`system/runtime/qlora-sft/qlora_sft/events.py` · 한 줄 = `GSHARED_EVENT=<JSON>`

| event | 필드 |
|---|---|
| `training_started` | `totalSteps` `sampleCount` `epochs` `batchSize` `maxLength` `learningRate` `modelType` `gpuName` `computeDtype` |
| `step` | `step` `totalSteps` `epoch`(소수) `loss` **`currentEpoch`** **`totalEpochs`** **`elapsedSec`** |
| `training_completed` | `runtimeSec` `trainLoss` `totalSteps` `peakVramMb` |
| `failed` | `errorCode` `message` |

**굵은 3개가 이번에 추가됐다.** 나머지는 그대로다.

### 왜 추가했나 — epoch을 추측하지 않기 위해

`state.epoch`는 소수(0.21, 1.5)이고 Backend 컬럼은 정수다. 누군가는 변환해야 한다.

Worker에서 하면 `ceil(currentStep / 한_epoch당_step수_추정)`이 된다. 이 추정은 gradient accumulation이나 마지막 배치 크기가 달라지는 순간 틀리고, **틀려도 epoch처럼 보인다.** 그래서 `args`·`state`를 손에 쥔 Trainer callback에서 변환하고, Worker는 들은 것을 보고하거나 아무것도 보고하지 않는다.

- 1-based — 첫 바퀴가 epoch 1
- 마지막 step에서 `state.epoch == num_train_epochs`가 되므로 총 개수로 묶는다 ("3 중 4"를 만들지 않는다)
- Trainer가 말하지 못하면 **필드 자체를 생략** — `null`이 아니라 부재

`elapsedSec`도 컨테이너가 잰다. Worker의 시계는 *첫 줄을 읽은* 시점부터인데, 그 앞에 이미지 pull과 모델 로딩이 있다 (실측 4.95s → 18.45s).

> **학습 알고리즘은 건드리지 않았다.** optimizer(`paged_adamw_8bit`) · LoRA(r16/α32/dropout0.05) · quantization(nf4 double) · epochs/batch semantics · learning rate · scheduler · 모델 로딩 전부 그대로. `tests/test_progress_metadata.py::TestTrainingItselfIsUntouched`가 이를 고정한다.

---

## 4. Streaming runner

`command_runner.AsyncSubprocessRunner.run_streaming(...)` — **기존 `run()`은 그대로 둔다.**

| | `run()` | `run_streaming()` |
|---|---|---|
| 줄 전달 | 없음 | 프로세스 실행 중 |
| stdout/stderr | 종료 후 일괄 | 동시 drain |
| `max_output_bytes` 초과 | `OUTPUT_LIMIT_EXCEEDED` 예외 | **tail buffer** — 오래된 바이트를 버리고 계속 |
| timeout · kill · exit code | 동일 | 동일 |

**초과 처리가 다른 이유.** 진단 명령이 1GB를 뱉었다면 그 답은 못 믿으니 예외가 맞다. 학습은 반대다 — CUDA 경고가 너무 많이 반복됐다고 끝난 GPU 작업 몇 시간을 버릴 수는 없고, 중요한 event는 한참 전에 이미 실시간으로 읽혔다. 메모리 상한은 동일하게 유지된다 (stdout/stderr 각 절반).

**두 파이프를 동시에 읽는다.** stdout만 읽으면 자식이 stderr 버퍼를 채우는 순간 쓰기에서 멈추고, 읽는 쪽은 영원히 오지 않을 stdout을 기다린다. 학습 컨테이너는 양쪽에 다 쓴다.

**줄 나누기**는 `\n`과 `\r` 둘 다. `transformers`·`tqdm`은 줄 끝 대신 줄 앞으로 돌아가며 진행바를 다시 그리므로, `\n`만 아는 파서에게는 실행 전체가 끝나지 않은 한 줄이다. `\r\n`은 하나로 센다. 종결자 없이 64KiB에 도달한 줄은 그대로 넘긴다.

`shell=True` 없음. `create_subprocess_shell` 없음. argument list 그대로다.

---

## 5. 검증 규칙

`runtime_progress.ProgressTracker`. **의심스러운 event는 고치지 않고 버린다** — 104%를 100%로 clamp하면 두 카운터가 어긋났다는 사실이 사라진다.

| 규칙 | 처리 |
|---|---|
| `totalSteps <= 0` | drop |
| `step < 0`, 숫자 아님, **`true`** | drop (Python에서 `True == 1`이라 명시적으로 막는다) |
| `step > totalSteps` | drop — clamp 안 함 |
| `step <= 마지막 보고 step` | drop (중복·역행) |
| percent가 비유한 또는 0~100 밖 | drop |
| `currentEpoch > totalEpochs` | epoch만 `None`, **step은 계속 보낸다** |
| `elapsedSec`가 음수/비유한 | Worker 시계로 **대체** (percent를 잃지 않는다) |
| elapsed 역행 | 직전 값 유지 |
| `training_started` | **아무것도 보내지 않는다** — step이 없는데 0을 지어내지 않는다 |
| `training_completed` | 마지막 step = totalSteps → 100% 보장 |
| `failed` | **progress 아님.** outcome에만 반영 (JOB_FAILED는 9번) |
| 그 뒤 도착한 event | drop |

---

## 6. Progress buffer

`ProgressChannel` — **큐가 아니라 한 칸.**

진행률은 로그가 아니라 상태다. step 103이 나온 뒤에 101을 보낼 이유가 없고, 연결이 끊긴 동안 큐를 쌓으면 재연결 때 진실 대신 역사를 보내게 된다.

- `publish()` — runtime thread. 최신 값으로 덮는다
- `take()` — connection loop. 새 값이 있을 때만 1건, 그 뒤엔 `None`
- `next_generation()` — 새 학습 시작. 이전 run의 값을 버리고 번호를 올린다
- `resend_on_next_take()` — 재연결. 최신 1건을 **한 번만** 다시 제공

**generation**은 프로세스 밖으로 나가지 않는 내부 식별자다. 같은 Job을 재시도하면 jobId가 같으므로, 이미 끝난 컨테이너의 늦은 줄을 구분하려면 jobId만으로는 부족하다. 옛 generation의 publish는 무시된다.

**terminal outcome은 이 채널을 쓰지 않는다.** `RuntimeOutcome`은 기존 `queue.Queue`로 간다. progress는 버려도 2초 뒤 다시 맞지만, outcome을 잃으면 Job을 잃는다.

---

## 7. 누가 보내는가

```
container stdout   [runtime thread]  →  ProgressChannel  ←  [connection loop]  →  socket.send
```

**runtime thread는 socket을 만지지 않는다.** `runtime_progress` · `runtime_pipeline` · `job_runtime` 어디에도 `RegisteredSocket` · `websocket` · `job_progress_message`가 없다 (`test_job_progress.py::TestOnlyTheConnectionSends`가 고정).

`_every_turn`이 `keep_alive` 루프에서 매 turn 호출된다:

1. `answer_decisions(...)` — JOB_ACCEPTED가 먼저다. 그게 Backend를 RUNNING으로 옮기므로, 그 전에 보낸 progress는 무시된다
2. `send_progress(...)`

전송 직전 확인:

- `progress.jobId == slot.held.job_id` — 아니면 drop (다른 Job의 행을 갱신하게 된다)
- `slot.state is ASSIGNED` — ACCEPTING이면 아직 학습 전이고 Backend도 RUNNING이 아니다

**heartbeat가 우선이다.** 상자가 한 칸이므로 한 turn에 최대 1건이다. 컨테이너가 turn 사이에 300 step을 뱉어도 나가는 건 300번째 하나뿐이라, 구조적으로 heartbeat를 굶길 수 없다. 별도 throttle은 두지 않았다 — 실측 cadence(§9)가 loop 주기와 비슷해 coalescing만으로 충분하다.

**전송 실패는 학습 실패가 아니다.** `ConnectionWentAway`를 잡아 snapshot을 되돌려 놓고 `False`를 반환한다. 연결 문제는 기존 reconnect가 처리한다.

---

## 8. 재연결

```
disconnect  →  컨테이너 계속 학습  →  snapshot만 메모리에서 갱신
            →  reconnect → REGISTER → REGISTER_ACK
            →  resend_on_next_take()  →  최신 1건
```

끊기기 전 32%, 끊긴 동안 33·34·35·36·37% → **재연결 후 37% 한 번.** 과거 step은 replay하지 않는다 (덮어써서 남아 있지도 않다).

`JobSlot`과 `JobAcceptance`는 연결보다 오래 산다 (System 6.5부터). 재연결로 **JOB_ASSIGN 재실행 · dataset 재다운로드 · 컨테이너 재시작은 일어나지 않는다.**

---

## 9. Local GPU Realtime Smoke — 실측

RTX 4070 Laptop · Qwen3-0.6B · 16 samples · epochs 3 · batch 4 · lr 2e-4 · maxLength 512 · **12 steps**

이미지: `gshared/qlora-sft-runtime:0.2.0-progress` (로컬 빌드)

| | |
|---|---|
| T0 컨테이너 시작 | 0.00s |
| T1 `training_started` 관측 | 4.95s |
| T2 첫 `step` event | 18.45s |
| T3 첫 `RuntimeProgressEvent` | 18.45s |
| **T4 첫 JOB_PROGRESS** | **18.86s** |
| T5 컨테이너 종료 | 29.67s |

**T4 < T5, 여유 10.81초.** 학습 도중 12건이 약 0.5초 간격으로 나갔다.

```
t+18.86s  {"progressPercent": 8.33,  "currentStep": 1,  "totalSteps": 12, "elapsedSec": 2, "currentEpoch": 1, "totalEpochs": 3}
t+19.38s  {"progressPercent": 16.67, "currentStep": 2,  ...}
...
t+25.42s  {"progressPercent": 100.0, "currentStep": 12, "totalSteps": 12, "elapsedSec": 9, "currentEpoch": 3, "totalEpochs": 3}
```

step 간격 ≈ 0.5s, loop 주기와 같은 수준이라 throttle 없이 coalescing만으로 충분했다. **더 빠른 workload에서 초당 수십 건이 나오면 그때는 상자가 알아서 합친다** — 추가 throttle은 근거가 생겼을 때 넣는다.

> **Production Digest Path가 아니다.** registry digest가 없어 `check_image`는 이 이미지를 거부한다 — 옳은 동작이고 끄지 않았다. 이 harness는 그 검증 이후 단계만 실제 객체로 실행한다.

---

## 10. 보안

JOB_PROGRESS payload는 **숫자 6개뿐이다.**

들어가지 않는 것: Dataset 내용 · Dataset URL · signed URL query · 모델 로컬 경로 · Worker token · User access token · operator private key · Registry/Docker credential · container env · **stdout/stderr 원문**.

- stderr는 drain만 하고 **event로 파싱하지 않으며 어디로도 보내지 않는다** (stack trace에 경로가 들어간다)
- `GSHARED_EVENT=` 줄만 구조화 파싱한다
- stdout/stderr 보관은 각 128KiB tail로 제한
- Runtime event에 `loss`가 있지만 **Backend 계약에 없으므로 payload에 넣지 않는다**

---

## 11. System 9 연결점

이번 단계가 남겨 놓은 입력:

| 성공 | 실패 |
|---|---|
| `RuntimeOutcome.state = FINISHED` | `RuntimeOutcome.failure` (exit code 매핑) |
| `output_dir` | `detail` (컨테이너의 `failed` 메시지) |
| `adapter_config.json` · `adapter_model.safetensors` · `training_metrics.json` | `abort_tx_hash` 자리 |
| `events` (마지막 `training_completed.runtimeSec`) | `events` |

`actualRuntimeSec`는 아직 채우지 않았다 — `training_metrics.json`의 `runtimeSec`와 `training_completed` event 양쪽에 있고, 어느 쪽을 계약값으로 쓸지는 9번이 정한다.

Exit code 계약(0·10~15·`UNKNOWN_RUNTIME_EXIT`)과 출력 파일 계약은 System 7 그대로 유지된다.
