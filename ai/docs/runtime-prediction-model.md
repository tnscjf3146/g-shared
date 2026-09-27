# Runtime Prediction Model

`predictedRuntimeSec`를 내놓는 XGBoost 모델. 학습·데이터 출처·현재 한계를 적는다.

> **이 모델은 아직 production이 아니다.** `models/runtime_model.metadata.json`의
> `productionReady`가 `false`이고, 그 이유가 `limitations`에 문장으로 들어 있다.
> 코드가 아니라 metadata를 믿어라 — 재학습할 때마다 다시 계산된다.

---

## 1. 계약

| | |
|---|---|
| Target | `actual_runtime_sec` (초, 완료된 Job 전체) |
| 학습 변환 | `log` — 값의 범위가 수천 배라 상대오차로 학습한다 |
| Feature | 11개, 아래 |
| 출력 보증 | finite · `> 0`. NaN·∞·음수는 예측이 아니라 오류로 처리한다 |
| artifact | `models/runtime_model.json` + `.metadata.json` |

### Feature 11개

| 범주 | | 처리 |
|---|---|---|
| GPU | `gpuModel` | **categorical** |
| | `vramMb`, `benchmarkTokensPerSec` | numeric |
| Dataset | `sampleCount`, `effectiveTokens`, `avgSeqLength`, `maxSeqLength` | numeric |
| Training | `epochs`, `batchSize`, `maxLength` | numeric |
| Model | `modelId` | **categorical** |

`modelId`가 categorical인 이유: 정수로 두면 트리가 "3보다 작은가"로 쪼갠다. 2번 모델이 1번보다 크다는 뜻이 아니므로 그 순서에는 의미가 없다.

**Feature가 아닌 것**: `dockerImageId`(값이 하나뿐 — 정보 0) · `datasetSizeBytes`(디스크이지 계산량이 아님) · `totalTokens`(`effectiveTokens`와 거의 같은 정보) · `learningRate`(step 수도 step당 계산량도 바꾸지 않음) · `minVramMb`(**Feasibility 전용**) · `workerId`(식별자 — 학습하면 관계 대신 이름을 외운다).

열 순서는 `app/scheduler/runtime_features.py` 한 곳에서만 정해지고 artifact metadata에 저장된다. 학습과 추론의 열 순서가 다르면 모델은 **예외 없이** 그럴듯한 오답을 낸다.

---

## 2. 데이터 출처 — 가장 중요한 절

행마다 `provenance`가 붙는다. **표시가 없는 행은 거절된다** — 기본값을 주면 실측이 합성으로 기록되거나 그 반대가 되고, 둘 다 되돌릴 수 없다.

| | 뜻 | 학습 target |
|---|---|---|
| `MEASURED` | 실제 GPU에서 **끝까지** 돌린 Job | ✅ |
| `CALIBRATION` | `--max-steps` 등으로 **중간에서 끊은** 실행 | ❌ **절대** |
| `BOOTSTRAP` | 파이프라인 확인용 합성값 | ✅ (단 `productionReady = false`) |
| `PRODUCTION` | Backend `job_results.actual_runtime_sec` | ✅ (아직 0건) |

### CALIBRATION을 왜 막는가

11개 feature 안에 **"몇 step에서 끊었는가"가 없다.** 40-step calibration 행과 같은 설정을 끝까지 돌린 행은 **feature가 완전히 똑같고 target만 다르다.** 넣으면 모델은 구분할 수 없는 두 답을 배우고, 그 모순이 조용히 전체 예측을 망가뜨린다.

`app/training/provenance.py`가 코드로 막고, `tests/test_provenance.py`가 그것을 붙잡아 둔다.

---

## 3. 지금 있는 데이터

### 실측 6건 (`data/runtime_measured.jsonl`)

System 1번 QLoRA Runtime 작업에서 같은 기계로 측정. 전부 **RTX 4070 Laptop**(VRAM 8,188 MiB, CC 8.9).

`benchmarkTokensPerSec` = **29,048.535** — Worker 등록용 `gshared-benchmark-v1`을 실제로 돌려 얻은 값이다(3 run 중앙값). 짐작으로 채우지 않았다.

| run | provenance | 이유 |
|---|---|---|
| quick-demo-240 (33.4s) | `MEASURED` | 끝까지 실행 |
| quick-demo-1440 (268.0s) | `MEASURED` | 끝까지 실행 |
| smoke-0.6b / smoke-1.7b | `CALIBRATION` | `--max-steps 3` |
| standard-calibration-40 / -100 | `CALIBRATION` | `--max-steps` |

> **실측 2건은 아직 학습에 들어가지 않는다.** Qwen3가 Backend `base_models`에 없어 가리킬 `modelId` 정수가 없기 때문이다. **없는 PK를 지어내지 않는다.** Backend가 seed하면 `measured_runs.trainable_rows()`가 저절로 두 건을 돌려준다.

> **두 건 다 VRAM이 모자란 상태에서 측정됐다.** peak allocated가 물리 VRAM(8,187 MiB)을 넘어 WSL2 드라이버가 시스템 메모리로 넘겨받았고, step당 시간이 1.9s → 2.9s로 나빠졌다. 지배적 비용은 가중치가 아니라 **vocab 151,936짜리 logit**이다. 그래서 이 두 건의 실효 처리량(약 590 tokens/sec)은 여유 있는 설정의 값보다 훨씬 낮다.

### 합성 1,200건 (`data/runtime_bootstrap_synthetic.jsonl`)

현재 모델은 이것으로 학습된다. 결정적 생성기(seed 20250908)이고 파일은 언제든 재생성된다.

실측으로 고친 것 둘:

| | 이전 | 지금 | 근거 |
|---|---|---|---|
| RTX 4070 Laptop benchmark 범위 | 35,200~37,400 | **28,300~30,000** | 실측 29,048.5 — 이전 값은 25% 어긋났고 출처 불명 |
| 기동 overhead | `45 + 12/B` 초 | **`3.0 + 3.2/B` 초** | 실측 4.96s(0.6B) / 8.49s(1.7B) — 이전 값은 10배 컸다 |
| `sampleCount` 분포 | 균등 500~60,000 | **log 균등 100~60,000** | 균등이면 중앙값이 3만 건이라 시연용 수백 건이 학습 분포 밖 |

---

## 4. 학습

```bash
cd ai && python -m app.training.train_runtime
```

출처 집계 · baseline 비교 · 실측 대조를 모두 출력한다.

### Baseline 비교 (§11)

MAPE 15%가 좋은지 나쁜지는 그 자체로 알 수 없다. 매 학습마다 셋을 함께 잰다.

| | 방법 | 현재 검증 MAPE |
|---|---|---|
| `median` | 학습셋 중앙값을 언제나 답한다 | 549.69% |
| `throughput` | `effectiveTokens x epochs / benchmarkTokensPerSec` | 94.10% |
| **`xgboost`** | 11 feature | **17.15%** |

> `throughput`을 **production 공식으로 고정하지 않는다.** benchmark는 고정 합성 workload(batch 4 · 길이 512 · 135M 모델)를 잰 값이라 실제 Job의 모델 크기·배치가 다르면 그대로 맞지 않는다. 비교 대상일 뿐이다.

결과는 metadata의 `baselines`에 저장된다. XGBoost가 **지더라도 저장되고**, 그 사실이 `beatsBaselines: false`로 남는다.

### 실측 대조 (정확도 주장이 아님)

두 점으로 정확도를 말할 수는 없다. 하지만 **몇 배** 어긋나는지는 두 점으로도 알 수 있다.

| run | 실제 | 예측 범위 | 배수 |
|---|---|---|---|
| quick-demo-240 | 33.4s | 21.7 ~ 54.3s | 0.6x ~ 1.6x |
| quick-demo-1440 | 268.0s | 56.8 ~ 194.1s | 0.2x ~ 0.7x |

범위가 생기는 이유는 `modelId`가 없어 **알려진 id 전부로 예측**하기 때문이다. 그 폭 자체가 결과다 — 카탈로그 항목 하나가 예측을 2~3배 움직인다.

1,440건 쪽을 실제보다 빠르게 예측하는 것은 합성 생성기가 **vocab logit의 VRAM 압박을 모형에 넣지 않았기** 때문이다. 두 점에 맞춰 생성기를 더 깎지 않았다 — 맞춰야 할 대상은 두 점이 아니라 물리다.

---

## 5. 현재 한계 (metadata `limitations` 그대로)

1. 학습 행 1,200개가 전부 **BOOTSTRAP 합성**이다. 검증 오차는 "XGBoost가 생성기의 공식을 얼마나 잘 따라 했는가"이지 실행 시간을 얼마나 맞히는가가 아니다.
2. 학습셋에 **MEASURED full-job이 하나도 없다.**
3. **cross-GPU 정확도를 검증한 적이 없다.** 학습셋의 GPU 다양성은 합성이며 아무 증거도 아니다.

RTX 3060 / 4090 / A100 등에 대한 정확도는 **주장하지 않는다.**

`gpuModel`·`modelId`가 카탈로그에 없으면 **422**다(`unknown_category`). 임의로 `__UNKNOWN__`에 밀어 넣지 않는다 — 기존 정책 그대로.

---

## 6. 실제 데이터로 넘어가는 법

파이프라인은 이미 그 모양이다. 바뀌는 것은 입력뿐이다.

1. Backend가 `job_results.actual_runtime_sec`를 모은다 → `provenance: "PRODUCTION"`
2. Backend `base_models`에 Qwen3를 seed → 실측 2건이 자동으로 학습에 합류
3. `train_runtime.py`에서 bootstrap 파일을 뺀다
4. `productionReady`가 저절로 `true`가 되고 `limitations`가 줄어든다

`schedulerModelVersion`을 저장해 두면 나중에 `actualRuntimeSec`와 대조해 어느 모델이 뭐라고 했는지 알 수 있다.
