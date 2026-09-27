# QLoRA SFT Runtime

G-shared가 승인한 학습 컨테이너. **모델 weight와 Dataset을 마운트로 받아 LoRA Adapter 하나를 내놓는 것**이 전부이고, 그 외에는 아무것도 하지 않는다 — 네트워크도, 자격 증명도, Backend 호출도 없다.

System 7 GPU Job Runtime이 이 컨테이너를 실행한다. **7번은 아직 구현되지 않았다.**

---

## 지원 모델

| | modelRef | modelRevision | license | architecture |
|---|---|---|---|---|
| Quick Demo | `Qwen/Qwen3-0.6B` | `c1899de289a04d12100db370d81485cdf75e47ca` | apache-2.0 | `Qwen3ForCausalLM` |
| Standard | `Qwen/Qwen3-1.7B` | `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e` | apache-2.0 | `Qwen3ForCausalLM` |

revision은 Hugging Face model API에서 실제로 resolve한 commit SHA다. **`main`/`latest`를 쓰지 않는다** — tag는 움직이고, 한 번 값이 매겨진 Job이 다음 주에 다른 weight로 학습되면 안 된다.

목록은 `qlora_sft/config.py`의 `CATALOG` 하나뿐이고, runtime·문서·준비 helper가 모두 그것을 본다.

> Runtime은 **Backend DB PK에 종속되지 않는다.** 컨테이너가 받는 것은 local path이고, Backend 연동에 필요한 것은 `modelRef` + `modelRevision`이다.

---

## Container 계약

### CLI

```bash
python -m qlora_sft \
  --model-path   /job/model \
  --dataset-path /job/input/dataset.jsonl \
  --output-dir   /job/output \
  --epochs 1 --batch-size 16 --learning-rate 0.0002 --max-length 512
```

선택: `--seed` (기본 20260910), `--gradient-accumulation-steps`, `--gradient-checkpointing`, `--adapter-path`.

**`--adapter-path`** — 이어서 학습할 LoRA Adapter 디렉터리(`adapter_config.json` + `adapter_model.safetensors`). 주면 새 LoRA를 만들지 않고 그 Adapter를 `PeftModel.from_pretrained(..., is_trainable=True)`로 올려 **그 rank·target 그대로** 학습을 이어 간다. 없으면 기존처럼 새로 초기화한다. Worker는 `/job/input/adapter`에 풀어 넘긴다.

**Runtime은 SPEED/BALANCED 같은 이름을 모른다.** numeric config만 받는다. 모드 → 숫자 변환은 AI Scheduler의 일이다.

**config를 고치지 않는다.** 범위를 벗어나면 거부하고, 반올림하거나 기본값으로 바꾸지 않는다. CUSTOM 모드에서는 그 숫자가 고객이 직접 넣고 값을 치른 것이다.

### Mount

| 경로 | 모드 | 비고 |
|---|---|---|
| `/job/model` | read-only | Base model weight. 이미지에 포함되지 않는다 |
| `/job/input` | read-only | `dataset.jsonl`, 선택 `adapter/` (이어 학습할 Adapter) |
| `/job/output` | writable | Adapter + metrics |
| `/job/cache` | writable 또는 tmpfs | HF·Triton·Inductor cache |
| `/tmp` | tmpfs | Trainer 임시 디렉터리 |

> **tmpfs를 쓸 때는 반드시 uid/gid를 준다.** 컨테이너는 uid 10001로 돌고, 맨 `--tmpfs /job/cache`는 이미지에서 chown해 둔 디렉터리 위에 **root 소유 0755 파일시스템을 덮어씌운다.** 그러면 Torch Inductor cache 생성이 `PermissionError`로 죽는다. Bind mount를 쓸 때는 host 디렉터리를 10001로 chown한다.

### Dataset

JSONL, 한 줄에 객체 하나:

```json
{"instruction": "결과는 어디서 보나요?", "input": "", "output": "결과 페이지에서 확인하실 수 있습니다."}
```

| 규칙 | |
|---|---|
| `instruction` | 필수 · 비어 있지 않은 문자열 |
| `output` | 필수 · 비어 있지 않은 문자열 |
| `input` | 선택 · 문자열 · 빈 값 허용 |
| 그 외 | 거부 (JSON 아님 / 객체 아님 / 타입 불일치 / 필드 누락) |

거부 메시지는 **줄 번호만** 말하고 내용은 인용하지 않는다. Dataset은 고객 것이고 stdout은 GPU 소유자가 읽는다.

Prompt template은 하나뿐이다 (`qlora_sft/dataset.py`). Loss는 **응답 부분에만** 걸린다 — prompt 토큰은 `-100`으로 마스킹된다.

### Output

```
/job/output/
├── adapter_config.json
├── adapter_model.safetensors
└── training_metrics.json
```

**Base model weight는 복사되지 않는다.** 결과물은 Adapter다. `model.safetensors` 등이 output에 있으면 `OUTPUT_VALIDATION_FAILED`로 실패한다.

PEFT가 남기는 `README.md` model card는 지운다 — 고객이 받을 물건이 아니고, `base_model: /job/model`이라는 컨테이너 내부 경로가 박힌다.

매 Job은 **자기 결과물을 스스로 검증한다**: 저장 후 모델을 내리고 `PeftModel.from_pretrained`로 다시 올려 본다. 실패하면 non-zero로 끝난다.

`training_metrics.json`의 **`initAdapter`** 는 시작점이다. 새로 초기화했으면 `null`, `--adapter-path`로 이어 학습했으면 실제로 읽은 파일의 `weightsSha256`·`configSha256`과 `r`·`loraAlpha`·`targetModules`. 결과만 보고 어떤 Adapter에서 출발했는지 확인할 수 있다.

### 이어 학습할 Adapter 검사

GPU를 쓰기 전에 host Python으로 확인한다(`qlora_sft/adapter.py`). 하나라도 어긋나면 `INVALID_INPUT`.

| 규칙 | |
|---|---|
| 필수 파일 | `adapter_config.json`, 비어 있지 않은 `adapter_model.safetensors` |
| config | JSON 객체, `peft_type == "LORA"`, `task_type`이 있으면 `CAUSAL_LM`, 양의 정수 `r`, `target_modules` 있음 |
| 거부 | base model weight(`model.safetensors` 등), pickle(`adapter_model.bin`·`.pt`) |
| Base model 호환 | shape·target이 이 모델에 안 맞으면 PEFT 로드에서 실패 → `INVALID_INPUT` |

`base_model_name_or_path`는 비교하지 않는다 — 이 컨테이너가 쓴 Adapter는 항상 `/job/model`로 기록돼 어떤 weight였는지 말해 주지 않는다.

### stdout

```
GSHARED_EVENT={"event":"training_started","version":"qlora-sft-events-v1","totalSteps":90,...}
GSHARED_EVENT={"event":"step","version":"qlora-sft-events-v1","step":1,"totalSteps":90,"epoch":0.01,"loss":3.29}
GSHARED_EVENT={"event":"training_completed","version":"qlora-sft-events-v1","runtimeSec":268.0,...}
```

marker가 붙는 이유: stdout은 공유된다. transformers는 진행 막대를, bitsandbytes는 배너를 쓴다. 모든 줄을 JSON으로 읽는 reader는 익사한다. GPU benchmark가 쓰는 것과 같은 방식이다.

**진행률은 `currentStep / totalSteps`다.** epoch 비율이 아니다 — 한 epoch이 300 step이면 epoch로는 답할 수 없다.

> JOB_PROGRESS는 **8번 작업**이다. 여기에는 WebSocket도 HTTP도 없다. 이것은 8번이 파싱할 stdout 계약일 뿐이다.

### Exit code

| | 코드 | |
|---|---|---|
| `OK` | 0 | Adapter를 썼다 |
| `INVALID_INPUT` | 10 | Dataset/config가 읽히지 않음, 또는 이어 학습할 Adapter가 규칙에 안 맞거나 이 모델에 안 맞음 |
| `MODEL_LOAD_FAILED` | 11 | 지원하지 않는 모델이거나 weight 로드 실패 |
| `CUDA_UNAVAILABLE` | 12 | 쓸 수 있는 NVIDIA GPU 없음 |
| `CUDA_OOM` | 13 | VRAM 부족 |
| `TRAINING_FAILED` | 14 | 학습 중 예외 |
| `OUTPUT_VALIDATION_FAILED` | 15 | 결과물이 로드 가능한 Adapter가 아님 |

**CUDA_OOM에서 재시도하지 않는다.** batch size를 몰래 줄이지 않고, CPU로 넘어가지 않는다. config를 고르는 것은 AI Scheduler의 일이다.

---

## QLoRA 구성

| | |
|---|---|
| Quantization | 4-bit **NF4** + double quantization |
| Compute dtype | `bfloat16` (지원 시) · 아니면 `float16`. **CPU fallback 없음** |
| LoRA | r=16, alpha=32, dropout=0.05, bias=none |
| Target modules | `"all-linear"` |
| Optimizer | `paged_adamw_8bit` |
| Padding | batch 내 최장 길이까지 (동적) |

`"all-linear"`는 Qwen3 + PEFT 0.20에서 **실제로** 아래로 풀린다. 다른 모델에서 복붙한 목록이 아니라, 기록된 `adapter_config.json`에서 읽어 확인한 값이다:

```
q_proj  k_proj  v_proj  o_proj  gate_proj  up_proj  down_proj
```

`lm_head`는 제외된다. Adapter가 **아무 레이어에도 안 붙으면** 실패로 처리한다 — 붙지 않은 adapter도 학습은 "성공"하고 아무 출력도 바꾸지 못하는 파일을 남기기 때문이다.

---

## Docker

```bash
docker build -t gshared/qlora-sft-runtime:0.1.0 system/runtime/qlora-sft
```

| | |
|---|---|
| Base image | `pytorch/pytorch@sha256:79c5599719e0b1afdb56ac2d14588b530283752d7ae6ec3c36e18ec9deb8b229` |
| | Python 3.12.3 · torch 2.12.1+cu126 · CUDA 12.6 |
| 주요 의존성 | transformers 5.17.0 · peft 0.20.0 · bitsandbytes 0.50.2 · accelerate 1.15.0 |

base image는 **GPU PoC가 이미 실제 하드웨어에서 검증했고 Worker benchmark가 지금 pin하고 있는 바로 그 이미지**다. digest로 고정한다.

전체 pin은 `requirements.txt`에 있다. 직접 의존성 4개만이 아니라 pip이 끌어온 것까지 전부 고정한다 — 직접 의존성만 pin한 Dockerfile은 매주 다른 이미지를 만든다.

> base image의 `spin`(scientific-python 개발용 task runner)은 **제거한다.** `click<8.4`를 요구해서 transformers가 필요로 하는 typer/click 8.5와 충돌한다. 빌드의 `pip check`가 이걸 잡아낸다.

**이미지에 없는 것**: 모델 weight · Dataset · 자격 증명 · `tools/` · 테스트.

### Hardening

```bash
docker run --rm \
  --gpus all --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 512 --memory 16g \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m,uid=10001,gid=10001,mode=0700 \
  --tmpfs /job/cache:rw,nosuid,size=1g,uid=10001,gid=10001,mode=0700 \
  -v "$MODEL":/job/model:ro \
  -v "$JOB/input":/job/input:ro \
  -v "$JOB/output":/job/output \
  gshared/qlora-sft-runtime:0.1.0 \
  --epochs 1 --batch-size 16 --learning-rate 0.0002 --max-length 512
```

non-root(uid 10001)로 돌고, 위 조건 전부에서 실제로 학습이 완료되는 것을 확인했다.

---

## 개발용 도구 (`tools/` — 이미지에 포함되지 않는다)

```bash
# 모델 준비. 인터넷을 쓰는 유일한 곳이고, 컨테이너 밖에서 돈다.
python tools/prepare_model.py qwen3-0.6b --dest /opt/gshared-dev/models/qwen3-0.6b

# Dataset 생성. seed 고정, 같은 (count, seed)면 같은 파일.
python tools/make_dataset.py demo --out demo.jsonl

# Base vs Base+Adapter 출력 비교.
python tools/validate_adapter.py --model-path /job/model --adapter-path /job/output
```

`prepare_model.py`는 **7번의 Runtime Asset Preparation이 아니다.** 7번은 revision 기준 캐시, 디스크 예산, 부분 다운로드 정책, 무결성 확인, 동시 요청 처리가 필요하다. 여기엔 그중 아무것도 없다.

---

## 측정 결과 — RTX 4070 Laptop 기준

> 아래 숫자는 **이 GPU에서만** 유효하다. 실행시간은 모델의 속성이 아니다.

**측정 환경**: NVIDIA GeForce RTX 4070 Laptop GPU · VRAM 8187 MiB · Compute Capability 8.9 · BF16 지원 · driver 596.36 · WSL2 + Docker 29.7.2 + NVIDIA Container Toolkit

### Smoke (둘 다 동일 Runner · 동일 이미지)

| | samples | steps | dtype | runtime | peak VRAM | Adapter |
|---|---|---|---|---|---|---|
| Qwen3-0.6B | 16 | 3 | bfloat16 | 7.2s | 3,060 MB | 40.4 MB · reload OK |
| Qwen3-1.7B | 16 | 3 | bfloat16 | 11.1s | 5,238 MB | 69.8 MB · reload OK |

### Quick Demo Full Run — 실측

Qwen3-0.6B + SPEED (epochs 1 · batch 16 · lr 2e-4 · maxLength 512)

| samples | steps | total | training | trainLoss | steps/s | samples/s | peak VRAM |
|---|---|---|---|---|---|---|---|
| 240 | 15 | 33.4s | 28.5s | 1.811 | 0.527 | 8.43 | 9,783 MB |
| **1,440** | **90** | **268.0s (4.5분)** | **262.3s** | **0.327** | **0.343** | **5.49** | **10,282 MB** |

목표 2~5분 → **1,440 samples 채택.** 240은 33초로 너무 짧아 보여 줄 것이 없다.

Adapter 효과 확인 (`validate_adapter.py`, greedy):

| | |
|---|---|
| Base | `<think>Okay, the user is asking about...` (영어 사고 과정) |
| Base+Adapter | `작업 완료 후 결과 페이지에서 학습된 Adapter 파일을 확인할 수 있습니다.` |

3개 prompt 전부 출력이 바뀌었다. **품질 보증이 아니라 Adapter가 실제로 적용된다는 확인이다.**

### Standard Calibration — 실측

Qwen3-1.7B + BALANCED (epochs 3 · batch 8 · lr 2e-4 · maxLength 1024)

| steps | training runtime | sec/step | steps/s | samples/s | peak VRAM |
|---|---|---|---|---|---|
| 40 | 137.3s | 3.433 | 0.291 | 2.33 | 8,685 MB |
| 100 | 359.7s | **3.597** | 0.278 | 2.22 | 8,685 MB |

40 → 100 step에서 step당 시간이 5%만 늘고 peak VRAM은 동일하다. **이 workload는 메모리가 안정적이다.**

### Standard Full Run — **미실행 · Calibration 기반 추정**

3.597 sec/step 기준:

| samples | steps/epoch | 총 steps | **추정 시간** |
|---|---|---|---|
| 2,000 | 250 | 750 | ~45분 |
| **2,400** | **300** | **900** | **~54분** ← 채택 |
| 3,000 | 375 | 1,125 | ~67분 |

**실제로 1시간짜리 Full Run을 수행하지 않았다.** 위는 전부 추정치다. 2,400을 고른 이유는 900 step이 45~75분 구간의 가장자리가 아니라 가운데에 오기 때문이다.

### VRAM 관찰 — minVramMb 후보 제안용

| workload | peak allocated |
|---|---|
| 0.6B smoke (batch 4 · len 512) | 3,060 MB |
| 1.7B smoke (batch 4 · len 512) | 5,238 MB |
| 1.7B BALANCED (batch 8 · len 1024) | 8,685 MB |
| 0.6B SPEED (batch 16 · len 512) | 9,783 ~ 10,282 MB |

> **Demo workload의 peak가 카드의 물리 VRAM(8,187 MiB)을 넘는다.** OOM이 나지 않은 것은 WSL2 NVIDIA 드라이버가 시스템 메모리로 넘겨 받아 주기 때문이다. 240 → 1,440 samples에서 reserved가 10.5 GB → 22.8 GB로 늘고 step당 시간이 1.9s → 2.9s로 나빠진 것이 그 대가다.
>
> 지배적인 비용은 weight가 아니라 **logit**이다. Qwen3의 vocab은 151,936이고, batch 16에서 loss 계산이 만드는 텐서가 4-bit로 줄여 둔 weight보다 훨씬 크다.
>
> **AI Scheduler의 `minVramMb`를 여기서 확정하지 않는다.** 위는 이 카드 한 대의 측정치이고, 후보를 제안하기 위한 자료다. 현재 seed 값은 8,192인데, 측정대로면 Demo workload조차 그 안에 들어가지 않는다.

---

## 테스트

```bash
cd system/runtime/qlora-sft && python -m unittest discover -s tests
```

torch 없이 host Python에서 돈다 — 무거운 import는 전부 함수 안에 있다. GPU 테스트는 위 "측정 결과"에 따로 기록한다.

---

## 이 Runtime이 하지 않는 것

JOB_ASSIGN 연결 · Worker subprocess 관리 · JOB_PROGRESS · JOB_RESULT · JOB_FAILED · JOB_CANCEL · resultHash · ZIP · Object Storage upload · `submitResult` · `abortJob` · Escrow · Blockchain.

테스트(`tests/test_boundaries.py`)가 위 항목들이 소스에 없다는 것을 검사한다.
