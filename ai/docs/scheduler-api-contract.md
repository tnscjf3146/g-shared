# AI Scheduler API 계약

Backend ↔ AI Scheduler 연동 계약. **코드가 기준이고 이 문서는 그것을 설명한다** —
모든 예시는 실제 실행 결과이며 `ai/tests/test_api_contract.py`가 계약을 고정한다.

기준: `develop` @ `560be43` + `ai-feature/scheduler-integration` · 검증 `517 passed`

---

## 1. 역할 경계

| | 답하는 질문 |
| --- | --- |
| **Backend** | 이 Worker가 **지금** 일을 받을 수 있는가 |
| **AI Scheduler** | 이 Job을 **돌릴 수 있는** GPU는 무엇이고, 그중 **가장 빠른** 것은 |

**Backend가 하는 것** — 인증·revoked 제외·WebSocket 연결·heartbeat·ONLINE·다른 Job 미실행. 이 판단들은 Job이 무엇이든 답이 같다.

**AI Scheduler가 하는 것** — Job별 기술 적합성(VRAM), 실행 시간 예측, 순위, 추천.

**Backend가 하지 않는 것**

```java
if (worker.getVramMb() >= workload.getMinVramMb()) { ... }   // 넣지 마세요
```

Job마다 답이 달라지는 판단입니다. 두 곳에 두면 규칙이 정교해지는 날 — batchSize·maxLength·모델 크기를 함께 보게 되는 날 — 두 답이 갈리고, 그때 어느 쪽이 맞는지 아무도 모릅니다.

**AI Scheduler는 DB에 접속하지 않습니다.** `base_models`·`workloads`·`workers`·`jobs` 어느 것도 조회하지 않으므로, 판단에 필요한 값은 전부 요청에 실려야 합니다.

---

## 2. 공개 Endpoint

| Method | Path | 목적 | Runtime 모델 필요 |
| --- | --- | --- | --- |
| POST | `/scheduler/training-config` | AUTO 학습 설정 생성 | ❌ |
| POST | `/scheduler/input` | 확정 설정·데이터셋 수용 | ❌ |
| POST | `/scheduler/runtime/predict` | GPU별 실행 시간 예측 | ✅ |
| POST | `/scheduler/recommend` | 걸러내기 → 예측 → 순위 → 추천 | ✅ |
| GET | `/health` | 프로세스 생존 | ❌ |

**Backend가 실제로 쓸 것은 `/scheduler/recommend` 하나입니다.** 나머지는 AUTO 설정(프론트 경유)과 진단용입니다.

---

## 3. `POST /scheduler/recommend`

### Request

```json
{
  "modelId": 1,
  "dockerImageId": 3,
  "dataset": {
    "datasetSizeBytes": 52428800,
    "sampleCount": 10000,
    "totalTokens": 2500000,
    "effectiveTokens": 1800000,
    "avgSeqLength": 250,
    "maxSeqLength": 1024
  },
  "trainingConfig": {
    "epochs": 3,
    "batchSize": 8,
    "learningRate": 0.0002,
    "maxLength": 1024
  },
  "requirements": {
    "minVramMb": 12000
  },
  "gpuCandidates": [
    {
      "workerId": "worker-3060",
      "gpuModel": "NVIDIA GeForce RTX 3060",
      "vramMb": 12288,
      "benchmarkTokensPerSec": 17000.0
    },
    {
      "workerId": "worker-4090",
      "gpuModel": "NVIDIA GeForce RTX 4090",
      "vramMb": 24576,
      "benchmarkTokensPerSec": 72000.0
    }
  ]
}
```

### 필드

모두 **필수**입니다. 모르는 필드는 거절됩니다(`extra="forbid"`).

| 필드 | JSON | Java | 단위 | 제약 | 출처 |
| --- | --- | --- | --- | --- | --- |
| `modelId` | integer | `Long` | — | > 0 | `base_models` PK |
| `dockerImageId` | integer | `Long` | — | > 0 | `workloads` PK |
| `dataset.datasetSizeBytes` | integer | `Long` | byte | > 0 | `datasets` |
| `dataset.sampleCount` | integer | `Integer` | 건 | > 0 | `datasets` |
| `dataset.totalTokens` | integer | `Long` | 토큰 | > 0 | `datasets` |
| `dataset.effectiveTokens` | integer | `Long` | 토큰 | > 0 | `datasets` |
| `dataset.avgSeqLength` | number | `Double` | 토큰 | > 0 | `datasets` |
| `dataset.maxSeqLength` | integer | `Integer` | 토큰 | > 0 | `datasets` |
| `trainingConfig.epochs` | integer | `Integer` | — | > 0 | `jobs` |
| `trainingConfig.batchSize` | integer | `Integer` | — | > 0 | `jobs` |
| `trainingConfig.learningRate` | number | `Double` | — | > 0, 유한 | `jobs` |
| `trainingConfig.maxLength` | integer | `Integer` | 토큰 | > 0 | `jobs` |
| `requirements.minVramMb` | integer | `Integer` | **MiB** | > 0 | `workloads.min_vram_mb` |
| `gpuCandidates[].workerId` | string | `String` | — | 비어 있지 않음 | `workers` |
| `gpuCandidates[].gpuModel` | string | `String` | — | 비어 있지 않음 | `workers` |
| `gpuCandidates[].vramMb` | integer | `Integer` | **MiB** | > 0 | `workers` |
| `gpuCandidates[].benchmarkTokensPerSec` | number | `Double` | tok/s | > 0, 유한 | `workers` |
| `gpuCandidates[].benchmarkPeakVramMb` | integer \| null | `Integer` | **MiB** | > 0 또는 생략/null | `workers` |

- `gpuCandidates`는 **1개 이상**, 같은 `workerId`가 두 번 오면 거절.
- `benchmarkPeakVramMb`는 **선택**입니다. 벤치마크가 실제로 쓴 최대 VRAM으로, Worker가 등록할 때 이미 재는 값입니다. 보내지 않거나 `null`이어도 요청은 그대로 통과합니다 — 이 값을 모르는 옛 Worker가 등록한 GPU가 남아 있기 때문입니다. **지금은 계약으로만 받고 예측 feature에는 넣지 않습니다**(열을 늘리면 모델을 다시 학습해야 하고, 아직 값이 쌓이지 않았습니다). `vramMb`(카드 총량)와 다른 값이며, 서로를 대체하지 않습니다.
- `vramMb`와 `minVramMb`는 **같은 단위(MiB)** 여야 합니다. `vramMb`는 Worker가 보고한 값(`nvidia-smi memory.total`) **그대로** 보내고, `minVramMb`는 카탈로그의 공칭 용량(예: 8192)을 보냅니다.
- Scheduler는 비교할 때만 `vramMb`를 **공칭 용량(1 GiB 단위)** 으로 읽습니다. 바로 위 GiB 경계보다 **32 MiB 이하**로 모자란 보고값만 그 경계로 봅니다 — 드라이버가 판매 용량보다 몇 MiB 적게 보고하기 때문입니다(RTX 4070 Laptop 8188 → 8192, RTX 4080 16376 → 16384, RTX 4090 24564 → 24576). 그 밖의 값(예: 8100, 12000)은 그대로 비교합니다. 응답·예측에 쓰이는 `vramMb`는 바뀌지 않습니다. Backend는 값을 보정하지 말고 보고값을 그대로 보내면 됩니다.
- **숫자 문자열은 거절됩니다.** `"3"`, `"8188"` 모두 422 — JSON에 숫자 타입이 있으므로 문자열로 왔다면 보내는 쪽이 잘못 만든 것이고, 조용히 고쳐 주면 그 잘못이 계속 남습니다.
- `totalTokens`와 `effectiveTokens`는 **다른 값이며 어느 쪽이 큰지도 정해져 있지 않습니다** — 자르면 줄고 패딩하면 늡니다.

### 정상 Response (200)

```json
{
  "schedulerModelVersion": "runtime-xgb-v1",
  "recommendedWorkerId": "worker-4090",
  "rankings": [
    { "rank": 1, "workerId": "worker-4090", "predictedRuntimeSec": 985.8823852539062 },
    { "rank": 2, "workerId": "worker-3060", "predictedRuntimeSec": 4329.7177734375 }
  ]
}
```

- `rankings`는 `predictedRuntimeSec` **오름차순**, `rank`는 **1부터 연속**.
- **`rankings`에는 실행 가능한 GPU만 들어갑니다.** 탈락한 후보는 응답 어디에도 나오지 않습니다.
- `predictedRuntimeSec`는 예측값 **그대로**입니다. 반올림하지 않습니다 — 표시 형식은 화면이 정할 일이고, 여기서 정밀도를 버리면 되돌릴 방법이 없습니다.
- 응답에 **없는 것**: 가격·비용·`modelId` 되돌림·`requirements`·탈락 목록·Worker 상태. **비용 계산은 Backend 책임입니다.**

### 처리 순서

```
Request
  → Pydantic 검증 (타입·범위·중복 workerId)
  → Feasibility Filter    nominal(vramMb) >= minVramMb   (보고값이 GiB 경계보다 32 MiB 이하로 모자랄 때만 경계로 읽음)
  → Runtime Prediction    통과한 GPU만, 한 번에
  → Ranking               (predictedRuntimeSec, workerId) 오름차순
  → Recommendation        rank 1
```

**걸러내기가 예측보다 먼저입니다.** 탈락한 GPU에 대해서는 예측 자체를 수행하지 않습니다.

---

## 4. Error 계약

세 가지 실패는 **Backend가 해야 할 일이 서로 다릅니다.**

| Status | 언제 | Backend가 할 일 |
| --- | --- | --- |
| **422** | 요청이 잘못됨 | 요청을 고쳐서 다시 |
| **409** | 요청·모델 정상, 돌릴 수 있는 GPU 없음 | **재시도 무의미**, 후보가 달라지면 재호출 |
| **503** | Runtime 모델 미로드 | 요청 그대로 나중에 재시도 |

### 422 — 검증 실패

```json
{
  "detail": [
    {
      "type": "greater_than",
      "loc": ["body", "requirements", "minVramMb"],
      "msg": "Input should be greater than 0",
      "input": 0,
      "ctx": { "gt": 0 }
    }
  ]
}
```

`modelId: 0` · `modelId: "1"` · `minVramMb: -1` · `minVramMb: 12000.5` · 모르는 필드 · 같은 `workerId` 중복 · `NaN`/`Infinity` 등.

**`unknown_category`도 422입니다** — 학습 때 본 적 없는 `gpuModel` 또는 `modelId`가 온 경우:

```json
{
  "detail": [
    {
      "type": "unknown_category",
      "loc": ["body", "model_id"],
      "msg": "model_id='9999' was not seen during training (known: 1, 2, 3)"
    }
  ]
}
```

> ⚠️ **`base_models`에 행을 추가하면 Scheduler 재학습이 필요합니다.** id만 늘리면 그 모델의 모든 요청이 422가 됩니다. 추가 전에 AI 쪽에 알려 주세요. id 재사용도 금지입니다 — 3번을 지우고 다른 모델에 3을 주면 예전 모델의 실행 시간으로 답합니다. 삭제 대신 `enabled = FALSE`.

### 409 — `no_feasible_gpu`

```json
{
  "detail": [
    {
      "type": "no_feasible_gpu",
      "loc": ["body", "gpuCandidates"],
      "msg": "none of the 3 GPU candidates has at least 40960 MiB of VRAM, which this job requires"
    }
  ]
}
```

**의미를 정확히 읽어야 합니다.**

- ✅ "**이번 요청에 실린 후보 중**에 이 Job의 요구조건을 만족하는 GPU가 없다"
- ❌ "이 Job은 영원히 실행할 수 없다"

따라서:

- **같은 후보 집합으로 즉시 재시도하는 것은 의미가 없습니다.** 답이 같습니다.
- **후보 집합이 달라지면 결과도 달라집니다.** 새 GPU가 ONLINE이 되거나 기존 Worker가 Job을 끝내고 가용해지면 재호출할 수 있습니다.
- 사용자에게는 "지금 이 작업을 돌릴 수 있는 GPU가 없습니다" 정도가 맞습니다.

### 503 — Runtime 모델 없음

```json
{ "detail": "no runtime model at /app/models/runtime_model.json. Train one first (python -m app.training.train_runtime)." }
```

이때 **benchmark가 가장 높은 GPU로 대체 추천하지 않습니다.** 예측을 근거로 하지 않은 답을 예측처럼 내보내지 않기 위해서입니다. Backend도 같은 원칙으로 처리해 주세요.

### 기동 실패 — 호환되지 않는 artifact

`runtime-features-v1` artifact를 현재 빌드에 물리면 **애플리케이션이 뜨지 않습니다.**

```
IncompatibleModelError: runtime model feature schema mismatch:
expected='runtime-features-v2', actual='runtime-features-v1'.
Retrain it (python -m app.training.train_runtime).
```

HTTP 오류가 아니라 기동 실패입니다. 배포가 잘못 물린 것이라 요청을 고쳐도 달라지지 않으므로, 앱이 멀쩡히 뜬 채 요청마다 실패하는 것보다 기동에서 막는 편이 원인을 빨리 드러냅니다.

---

## 5. `/health`의 의미

```json
{ "status": "ok" }
```

**"프로세스가 살아 있다"는 뜻입니다. 그 이상이 아닙니다.**

> ⚠️ **Runtime 모델이 없어도 `/health`는 200입니다.** health 200을 "예측을 받을 준비가 됐다"로 읽으면 안 됩니다. 그 순간에도 `/scheduler/recommend`는 503일 수 있습니다.

이렇게 둔 이유는 `/scheduler/input`과 AUTO 설정 생성이 Runtime 모델과 무관하기 때문입니다 — 모델이 없다고 그것들까지 못 쓰게 만들 이유가 없습니다. 별도의 readiness endpoint는 아직 없습니다.

---

## 6. 나머지 Endpoint

### `POST /scheduler/training-config` — AUTO

```json
{ "dataset": { …5개, effectiveTokens 없음 }, "mode": "BALANCED" }
```

`mode`는 `SPEED` · `BALANCED` · `TRAINING`. 응답은 CUSTOM이 보내는 것과 같은 모양입니다:

```json
{ "epochs": 3, "batchSize": 8, "learningRate": 0.0002, "maxLength": 1024 }
```

### `POST /scheduler/input`

확정된 설정과 데이터셋을 Scheduler 내부 표현으로 옮깁니다. 응답은 받은 값을 그대로 되돌려 주고(확인용) `features`를 덧붙입니다:

```json
{
  "dataset": { …보낸 그대로 },
  "training": { …보낸 그대로 },
  "features": {
    "datasetSizeBytes": 52428800.0,
    "sampleCount": 10000.0,
    "totalTokens": 2500000.0,
    "avgSeqLength": 250.0,
    "maxSeqLength": 1024.0
  }
}
```

> **주의**: `features`는 데이터셋 값 5개를 float으로 옮긴 것이며, **Runtime Prediction의 feature 11개와 다릅니다.** 값이 float인 것은 예측기가 받는 것과 같은 숫자임을 보이기 위해서입니다.

API의 모든 JSON 키는 **camelCase**입니다 — `features` 안도 마찬가지입니다. 아래 §8의 snake_case 이름들은 ML 내부 이름이며 JSON에 나오지 않습니다.

현재 이 엔드포인트의 정책 검사에는 **규칙이 하나도 없어** 스키마를 통과한 입력은 더 막히지 않습니다. 승인 이미지의 전처리 계약이 정해지면 규칙만 추가하면 되도록 배선만 남겨 둔 상태입니다.

---

## 7. 두 버전 문자열

혼동하기 쉬워 따로 적습니다. **둘은 다른 값이고 합치면 안 됩니다.**

| 값 | 뜻 | 언제 오르나 | Backend가 보나 |
| --- | --- | --- | --- |
| `runtime-xgb-v1` | 어느 모델이 답했는가 | 재학습할 때 | ✅ `schedulerModelVersion`으로 응답에 실림 |
| `runtime-features-v2` | 어떤 열로 학습됐는가 | feature 구성·의미가 바뀔 때 | ❌ 내부값, 응답에 없음 |

`schedulerModelVersion`은 **저장해 두세요.** 나중에 `actualRuntimeSec`와 대조할 때 어느 모델이 뭐라고 했는지 알 수 있어야 모델 개선을 평가할 수 있습니다.

---

## 8. Runtime Prediction feature (참고)

예측에 실제로 들어가는 열은 **11개**입니다.

| 구분 | 요청 필드 (JSON) | 내부 feature 이름 |
| --- | --- | --- |
| GPU | `benchmarkTokensPerSec` · `vramMb` · `gpuModel` | `benchmark_tokens_per_sec` · `vram_mb` · `gpu_model` |
| Dataset | `sampleCount` · `effectiveTokens` · `avgSeqLength` · `maxSeqLength` | `sample_count` · `effective_tokens` · `avg_seq_length` · `max_seq_length` |
| Training | `epochs` · `batchSize` · `maxLength` | `epochs` · `batch_size` · `max_length` |
| Model | `modelId` | `model_id` |

오른쪽 열은 **모델 내부 이름**입니다. JSON 계약에 나오지 않고, `unknown_category` 오류 메시지의 `loc`에서만 보입니다(`["body", "model_id"]`).

**계약에는 있지만 feature가 아닌 것**: `dockerImageId`(승인 이미지가 하나뿐이라 모든 행에서 같은 값) · `datasetSizeBytes`(디스크 크기이지 GPU 계산량이 아님) · `totalTokens`(`effectiveTokens`와 거의 같은 정보) · `learningRate`(step 수도 step당 계산량도 바꾸지 않음) · `minVramMb`(실행 가능 여부를 가르는 제약이지 실행 시간의 원인이 아님) · `workerId`(식별자).

> ⚠️ **현재 모델은 synthetic bootstrap 데이터로 학습됐습니다.** 실제 Job 실행 기록이 아직 없기 때문입니다. **순위 계산은 정확하지만 그 순위가 현실을 맞히는지는 검증되지 않았습니다.** `actualRuntimeSec`가 쌓이면 데이터만 교체해 재학습합니다.
>
> 이 상태는 artifact metadata에서 직접 읽을 수 있습니다 — `productionReady: false`와 그 이유가 담긴 `limitations` 배열입니다. 재학습할 때마다 다시 계산되므로, 문서가 아니라 그쪽을 믿으세요. 학습 데이터 출처·baseline 비교·실측 대조는 [Runtime Prediction Model](runtime-prediction-model.md)에 있습니다.

---

## 9. Backend 체크리스트

- [ ] `modelId`·`dockerImageId`를 **`Long`**으로 (현재 `SchedulerRecommendRequest`는 `String`이라 **지금 상태로는 422**)
- [ ] `requirements.minVramMb`를 `workloads.min_vram_mb`에서 읽어 전달
- [ ] `gpuCandidates`에는 **운영 가능한 Worker만** — 인증·revoked 제외·연결·heartbeat·ONLINE·다른 Job 미실행
- [ ] `vramMb`·`benchmarkTokensPerSec`·`gpuModel`이 **NULL인 Worker는 후보에서 제외** (NULL을 보내면 422)
- [ ] Backend에서 `vramMb >= minVramMb` 비교를 **구현하지 않음**
- [ ] `schedulerModelVersion`을 `jobs`에 저장
- [ ] **409 / 503 / 422를 각각 다르게 처리** — 409는 재시도 대상이 아님
- [ ] `base_models` 행 추가 시 AI 쪽에 사전 통보 (재학습 필요)
