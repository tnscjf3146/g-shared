# Runtime History Contract — AI가 재학습에 필요한 것

예측기를 실측 데이터로 다시 학습하려면 끝난 Job의 **feature 11개와 실제 실행 시간**이 필요하다. 이 문서는 그것을 Backend가 어떻게 내주어야 하는지와, 지금 무엇이 부족한지를 적는다.

기준: `9c23f34` develop · `jobs`/`workers`/`datasets` 테이블 · `JobService`

> **Backend 코드는 수정하지 않았다.** AI가 MySQL에 직접 붙는 구조도 만들지 않았다. 현재는 `RuntimeHistorySource` 추상 뒤에 JSONL 파일 구현 하나만 있고, Backend가 API를 갖게 되면 구현이 하나 더 붙는다.

---

## 1. 필요한 값

| 필드 | 쓰임 | 현재 저장 위치 | 상태 |
|---|---|---|---|
| `jobId` | dedup·추적 | `jobs.job_id` | ✅ |
| `workerId` | 추적 | `jobs.assigned_worker_id` | ✅ |
| `gpuModel` | **feature** | `workers.gpu_model` | ⚠️ snapshot 없음 |
| `vramMb` | **feature** | `workers.vram_mb` | ⚠️ snapshot 없음 |
| `benchmarkTokensPerSec` | **feature** | `workers.benchmark_tokens_per_sec` | ⚠️ snapshot 없음 |
| `sampleCount` | **feature** | `datasets.sample_count` | ⚠️ snapshot 없음 |
| `effectiveTokens` | **feature** | `datasets.effective_tokens` | ⚠️ snapshot 없음 |
| `avgSeqLength` | **feature** | `datasets.avg_seq_length` | ⚠️ snapshot 없음 |
| `maxSeqLength` | **feature** | `datasets.max_seq_length` | ⚠️ snapshot 없음 |
| `epochs` | **feature** | `jobs.epochs` | ✅ |
| `batchSize` | **feature** | `jobs.batch_size` | ✅ |
| `maxLength` | **feature** | `jobs.max_length` | ✅ |
| `modelId` | **feature** | `jobs.base_model_id` | ✅ |
| `predictedRuntimeSec` | 사후 대조 | `jobs.predicted_runtime_sec DOUBLE` | ✅ |
| `actualRuntimeSec` | **target** | `jobs.actual_runtime_sec DOUBLE` | ✅ |
| `runtimeModelVersion` | 사후 대조 | `jobs.scheduler_model_version` | ✅ |
| `status` | 필터 | `jobs.status` | ✅ |
| `completedAt` | 범위 보고 | `jobs.finished_at` | ✅ |

**결론: 11개 feature 중 4개(training/model)는 Job에 박혀 있고, 7개(GPU 3 + dataset 4)는 다른 테이블의 현재값을 참조할 수밖에 없다.**

---

## 2. 부족한 것 — per-job snapshot

### GPU 3개 (심각)

`workers.benchmark_tokens_per_sec`는 **바뀐다.** System 6.5가 `PUT /api/worker/gpu` 계열로 벤치마크 갱신 경로를 만들었고, 공급자가 드라이버를 바꾸거나 재측정하면 값이 달라진다.

3개월 전에 끝난 Job에 오늘의 벤치마크 값을 넣어 학습하면, 모델은 그 Job이 실제와 다른 GPU 성능에서 돌았다고 배운다. **§3이 금지하는 바로 그것이다.**

`gpu_model`·`vram_mb`도 같은 문제를 갖는다 — 공급자가 GPU를 교체하고 같은 `worker_id`로 재등록하면 과거 Job의 GPU가 바뀐 것처럼 보인다.

### Dataset 4개 (덜 심각)

`datasets`의 통계는 파일 내용에서 파생되고 `data_hash`가 있어 사실상 불변에 가깝다. 다만 **불변이라는 보장이 스키마에 없다** — 같은 `dataset_id`로 다른 파일을 다시 올리는 경로를 막는 제약이 없다.

### 지금 할 수 있는 것

이 7개는 **지금 값으로 채우면 안 된다.** 채운 행은 검증을 전부 통과하고 모델은 조용히 틀린 것을 배운다.

---

## 3. Backend가 내주어야 할 최소 계약

### 선택지 A — Job에 snapshot 컬럼 추가 (권장)

Job이 배정될 때(`assignJob`) 또는 JOB_ACCEPTED 시점에 그 순간의 값을 Job 행에 복사한다.

```sql
ALTER TABLE jobs
    ADD COLUMN snapshot_gpu_model                VARCHAR(255) NULL,
    ADD COLUMN snapshot_vram_mb                  INT          NULL,
    ADD COLUMN snapshot_benchmark_tokens_per_sec DOUBLE       NULL,
    ADD COLUMN snapshot_sample_count             INT          NULL,
    ADD COLUMN snapshot_effective_tokens         BIGINT       NULL,
    ADD COLUMN snapshot_avg_seq_length           DOUBLE       NULL,
    ADD COLUMN snapshot_max_seq_length           INT          NULL;
```

이미 `predicted_runtime_sec`와 `scheduler_model_version`을 같은 이유로 Job에 저장하고 있다 — 그 주석이 "저장해야 나중에 예측 정확도 평가 가능"이다. 같은 논리가 feature에도 적용된다.

### 선택지 B — 조회 API

```
GET /api/internal/runtime-history?status=COMPLETED&since=<ISO8601>&limit=1000
```

응답은 아래 JSONL 한 줄과 같은 모양이면 된다. 다만 **snapshot 없이 현재값을 조인해 주는 API라면 A의 문제가 그대로 남는다** — A가 먼저다.

인증은 Worker 토큰이 아니라 내부 전용이어야 한다(이 데이터는 고객의 dataset 통계를 담는다).

---

## 4. AI가 받는 모양

한 줄에 Job 하나. `snake_case`는 예측기의 feature 이름과 정확히 같다.

```json
{
  "job_id": "1002",
  "worker_id": "worker-a",
  "status": "COMPLETED",
  "completed_at": "2026-09-02T11:30:00Z",

  "gpu_model": "NVIDIA GeForce RTX 4070 Laptop GPU",
  "vram_mb": 8188,
  "benchmark_tokens_per_sec": 29048.535,
  "sample_count": 1440,
  "effective_tokens": 153216,
  "avg_seq_length": 81.812,
  "max_seq_length": 118,
  "epochs": 3,
  "batch_size": 4,
  "max_length": 512,
  "model_id": "3",

  "predicted_runtime_sec": 950.0,
  "actual_runtime_sec": 982.4,
  "runtime_model_version": "runtime-xgb-v1",
  "provenance": "MEASURED"
}
```

---

## 5. AI가 거절하는 것

`app/training/runtime_history.py`가 하는 일의 대부분이다. 아래는 전부 **버려지고 이유와 함께 세어진다.**

| 거절 사유 | 언제 |
|---|---|
| `NOT_COMPLETED` | `status != COMPLETED` |
| `MISSING_FEATURE` | 11개 중 하나라도 없거나 `null` |
| `BAD_FEATURE` | 숫자가 아님 · 빈 문자열 · 모르는 `provenance` |
| `BAD_TARGET` | `actual_runtime_sec`가 없음·0 이하·NaN·불리언·문자열 |
| `CONFLICTING_DUPLICATE` | 같은 `job_id`인데 값이 다름 |
| `NOT_AN_OBJECT` | JSON 객체가 아님 |

### CANCELLED와 FAILED를 넣지 않는 이유

`actual_runtime_sec`는 "이 작업이 이 GPU에서 얼마나 걸리는가"의 답이어야 한다.

- **CANCELLED** — System 10이 이 값을 실측 monotonic으로 정확히 기록한다. 하지만 그것은 "고객이 72%에서 멈추기까지 걸린 시간"이지 완주 시간이 아니다.
- **FAILED** — CUDA OOM으로 40초 만에 죽은 Job의 40초는 그 작업의 실행 시간이 아니다.

둘 다 실측이고 둘 다 정확하지만 둘 다 그 질문의 답이 아니다. 넣으면 모델은 **모든 작업이 실제보다 빨리 끝난다**고 배운다.

같은 `job_id`가 값이 다르게 두 번 오면 어느 쪽이 맞는지 알 수 없으므로 **고르지 않고 둘 다 쓰지 않는다.**

---

## 6. Target leakage

학습 행에는 **11개 feature + target + provenance만** 들어간다. 아래는 기록에는 남지만 절대 feature가 되지 않는다:

```
actual_runtime_sec(=target) · predicted_runtime_sec · status
runtime_model_version · job_id · worker_id · completed_at
result_hash · fail_reason
```

`predicted_runtime_sec`를 feature로 넣으면 모델은 실행 시간을 배우는 대신 **이전 예측기를 흉내 내는 법**을 배운다. 검증 오차는 아름다워지고 실제 예측은 나아지지 않는다. `job_id`는 그 자체로 시간 순서를 담고 있어서 "최근 Job은 빠르다" 같은 것을 배울 수 있다.

`check_no_leakage()`가 학습 직전에 다시 확인하고, 걸리면 멈춘다.

---

## 7. 재학습이 만드는 것

```
history → COMPLETED만 → 11 feature + target
        → 기존 bootstrap 1,200행과 합침
        → candidate 학습 → models/candidates/runtime-xgb-v2-candidate/
        → 실제 predictor loader로 다시 읽어 확인
        → Bootstrap / Measured / Cross-GPU 지표를 따로 계산
```

- **현역 `models/runtime_model.json`은 읽지도 쓰지도 않는다.** 재학습 전후 파일 지문을 비교해 확인한다
- **자동 승격 없음.** `promoted: false`가 metadata에 박힌다
- 지표는 두 벌이고 섞지 않는다. 실측 행이 50건(= 기존 `MIN_ROWS_FOR_VALIDATION`) 미만이면 `INSUFFICIENT_DATA`
- Cross-GPU는 **실측 행이** GPU 2종 이상을 덮어야 `PASS`. 합성 행의 GPU 다양성은 증거가 아니다

---

## 8. 요약 — Backend에 필요한 것

1. **`jobs`에 feature snapshot 7개 컬럼** (GPU 3 + dataset 4) — 없으면 실측 재학습은 과거 Job에 오늘의 값을 넣는 셈이 된다
2. 내부 전용 이력 조회 경로 (선택지 B) — 없어도 파일로 파이프라인은 돈다

둘 다 Backend 담당의 결정이다. **이번 작업에서 Backend 코드는 한 줄도 고치지 않았다.**
