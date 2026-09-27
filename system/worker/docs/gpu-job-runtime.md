# GPU Job Runtime

JOB_ACCEPTED 이후 Worker가 실제로 학습을 실행하는 경로. **Backend·Blockchain과 공유하는 계약이 아니라 System 내부 구현**이지만, execution-context와 Live blocker는 다른 파트와 맞물린다.

기준: `db9dfc8` Backend worker-contract-v1 · `fa0eeb2` QLoRA Runtime · `d8812f1` Escrow Acceptance

> **이번 단계는 "실행"까지다.** JOB_PROGRESS(8) · JOB_RESULT/JOB_FAILED(9) · USER_REQUEST 취소(10)는 구현하지 않았다.

---

## 1. 파이프라인

```
JOB_ACCEPTED 전송 (6.5)
 → Backend RUNNING
 → GET /api/worker/jobs/{jobId}/execution-context
 → context ↔ 수락한 assignment 대조
 → Runtime Image 검증 (approved repo + immutable digest)
 → Model 준비 (pinned revision, 캐시)
 → Dataset 다운로드 → SHA-256 → 크기
 → docker pull @digest → inspect 확인
 → hardened docker run → NVIDIA GPU
 → exit code + stdout event
 → RuntimeOutcome (반환만, 전송 없음)
```

**각 단계가 싸고 빠른 순서다.** context가 맞지 않으면 dataset을 받지 않고, 이미지가 승인된 것이 아니면 모델을 받지 않고, 전부 검증되기 전에는 GPU를 건드리지 않는다.

---

## 2. Execution Context

`GET /api/worker/jobs/{jobId}/execution-context` · `Bearer <WORKER_TOKEN>`

| 상태 | 처리 |
|---|---|
| 200 | 진행 |
| **409** | **bounded retry** — 6회, 0.5s→8s. Worker가 Backend의 RUNNING 전이보다 빠른 것뿐이다 |
| 5xx · 연결 실패 | bounded retry |
| 401 · 403 · 404 | **즉시 중단.** 다시 물어도 답이 같다 |
| JSON 아님 · 필드 누락 | **fail closed** |

retry budget은 `execution_context.py` 상수 한 곳에 있다. stop signal이 오면 즉시 끝난다.

### assignment와 대조하는 것

`jobId` · `assignedWorkerId` · `dockerImageId` · `modelId` · `datasetId` · `trainingConfig` 4개 · `escrow` 5개(`escrowJobId`·`externalRef`·`client`·`provider`·`expectedPriceWei`) · `expectedState == 1`.

**escrow를 두 번 검사한다.** 6.5에서 체인과 대조한 값은 JOB_ASSIGN에서 왔고, 이건 같은 서비스의 다른 메시지다. 둘이 다르면 Backend의 기록끼리 어긋난 것이고, 그때는 어느 쪽도 체인에 예치된 것을 설명하지 못한다.

> `expectedPriceWei`는 Backend의 이름이고 의미는 **결제 토큰의 최소 단위**다. ETH wei가 아니다.
>
> `expectedState`는 수락 시점에 **기대했던** 상태다. Backend Job이 RUNNING이라고 이 값이 2가 되면 필드의 의미가 바뀐 것이므로 거부한다.

### chainId / contractAddress

**Worker 설정이 authoritative다.** context는 일치 확인용이고, `contractAddress`가 null인 것은 허용하되 **다른 주소면 거부**한다 — 6.5가 이미 설정된 주소로 서명했으므로, 다른 계약에서 돌리면 돈과 일이 서로 다른 계약에 있게 된다.

---

## 3. Runtime Image

### 기준값 (Canonical Runtime)

System 안의 원본은 `gshared_worker/runtime_image.py` 의 `CANONICAL_RUNTIME_*` 상수 하나다. 테스트·selfcheck·문서는 이 값을 따른다.

| 항목 | 값 |
|---|---|
| repository | `le3560/gshared-qlora-sft-runtime` |
| tag (사람용, pull에 쓰지 않음) | `0.4.0` |
| digest | `sha256:3f054d6d9fc28546818c424e2aa4167a23369463ecaa035e940673e77841e4ec` |
| imageRef | `le3560/gshared-qlora-sft-runtime@sha256:3f054d6d9fc28546818c424e2aa4167a23369463ecaa035e940673e77841e4ec` |

| 버전 | 추가된 것 |
|---|---|
| 0.2.0 | 기본 QLoRA 학습 |
| 0.3.0 | `--adapter-path` (입력 LoRA Adapter 이어 학습) |
| **0.4.0** | 학습 결과 검증: `training_metrics.json`에 `lossHistory`·`finalLoss`·`verification`(LoRA 텐서 수, NaN/Inf, layer×module 변화량) — Backend 결과 검증 패널이 `result.tar`에서 읽는다 |

0.4.0은 RTX 4070 Laptop에서 새로 학습(`initAdapter: null`, loss 점 15개, `verification.valid: true`)과 Job #6 Adapter 이어 학습(`initAdapter.weightsSha256` 일치, final loss 0.605, `valid: true`)을 확인한 뒤 push했다. 이 필드 이름은 Backend `JobResultVerificationService`가 읽는 키와 대조했다.

**전환기 허용** — 새 이미지는 Backend 카탈로그가 가리키기 **전에** Worker가 먼저 승인해야 한다. 반대 순서면 Worker가 "승인되지 않은 이미지"로 거부해 모든 Job이 실패한다.

| 상수 | 버전 | Adapter | 비고 |
|---|---|---|---|
| `CANONICAL_RUNTIME_DIGEST` | 0.4.0 | ✅ | 기준값 |
| `PREVIOUS_RUNTIME_DIGEST` | 0.3.0 `e32bd1c1…e170` | ✅ | V24 이전 카탈로그. 결과 검증 데이터만 없음 |
| `LEGACY_RUNTIME_DIGEST` | 0.2.0 `acda63bc…126d` | ❌ | V19 이전 카탈로그. Adapter Job은 `ADAPTER_UNAVAILABLE` |

어느 카탈로그도 더 이상 가리키지 않으면 해당 상수를 허용 목록에서 제거한다.

> **Backend 인계 — 완료.** `workloads.image_digest`는 V24(`V24__runtime_image_result_verification.sql`)에서 0.4.0 digest로 옮겼다. Worker(exe) 배포 뒤라는 순서도 지켰다.

### 규칙

| 규칙 | |
|---|---|
| approved repository | `gshared/qlora-sft-runtime`, `le3560/gshared-qlora-sft-runtime` — **Worker 설정**, 메시지가 아님 |
| approved digest | **canonical(0.4.0) + 전환기 0.3.0·0.2.0** (`APPROVED_DIGESTS`). 승인 repository의 다른 digest는 거부 |
| immutable digest | `repository@sha256:<64 hex>` 만 |
| tag | **거부** (오늘 맞는 이미지를 가리켜도) |
| all-zero digest | **거부** — Backend 카탈로그 seed의 placeholder |
| imageRef ↔ digest | 서로 일치해야 함 |
| pull | `docker pull repo@sha256:...` |
| pull 후 | `docker image inspect --format {{json .RepoDigests}}` 로 **재확인** |

> **local image ID ≠ registry digest.** `inspect`는 `.Id`가 아니라 `.RepoDigests`를 본다. `.Id`와 비교하면 정상적으로 pull한 이미지마다 실패하고, 그런 검사는 고쳐지는 대신 삭제된다.
>
> **캐시에 있어도 검사한다.** 캐시 히트는 과거에 *무언가*를 그 이름으로 받았다는 뜻이지 지금 무엇이 있는지에 대한 증거가 아니다.

Backend가 `attacker/image@sha256:...`을 보내도 실행하지 않는다.

---

## 4. Model

**pinned commit SHA만.** `null` · `main` · `master` · `latest` · `head` · 짧은 SHA 전부 거부. 값이 매겨지고 수락된 Job이 그 사이 브랜치에 push된 다른 weight로 학습되면 고객은 알 방법이 없다 — 두 실행 모두 성공하기 때문이다.

**다른 모델로 fallback하지 않는다.** Qwen3-0.6B를 못 받으면 Job이 실패한다.

### 어떻게 받는가

학습 컨테이너는 `--network none`이라 아무것도 못 받는다. 그래서 **별도의 짧은 준비 컨테이너**가 받는다 — 같은 승인된 이미지(이미 `huggingface_hub`와 pin된 의존성을 갖고 있다)를 network와 cache mount만 주고 띄운다. GPU 없음, job workspace 없음, 자격 증명 없음.

`modelRef`와 `revision`은 **프로그램의 argv**로 넘어간다. 코드에 문자열로 끼워 넣지 않는다 — 따옴표가 든 modelRef가 코드 실행 경로가 되지 않도록.

`trust_remote_code`는 쓰지 않는다.

### 캐시

`<cache>/models/{owner}__{name}__{revision}` — **revision이 키의 일부다.** 같은 repo의 두 revision은 두 디렉터리다. 하나로 묶으면 Job이 잘못된 weight를 받고 아무도 눈치채지 못한다(파일은 정상적으로 로드된다).

완료는 `.gshared-complete` 마커로 표시하고 **맨 마지막에** 쓴다. 마커가 없는 디렉터리는 재사용하지 않고 버린다 — 중간에 죽은 다운로드는 shard 하나가 빠진 채 멀쩡해 보인다.

---

## 5. Dataset

**해시를 다시 계산한다.** Backend가 준 해시는 아직 도착하지 않은 파일에 대한 주장이다. 서명 URL이 중간에 만료되거나, 프록시가 자르거나, 스토리지가 200으로 에러 페이지를 주면 — 전부 파일처럼 생긴 것이 나온다. 해시만이 그것들을 진짜와 구분한다.

| | |
|---|---|
| scheme | http/https만. `file://`은 Worker 자기 디스크를 읽는다 |
| 방식 | 1 MiB 청크 streaming. 전체를 메모리에 올리지 않는다 |
| 경로 | 고정. **URL에서 파일명을 만들지 않는다** |
| 중간 파일 | `.part` → 검증 통과 후 atomic rename |
| 크기 | 실제 파일 크기. Content-Length를 믿지 않는다 |
| 해시 불일치 | **재시도하지 않는다** |
| 전송 실패 | bounded retry (3회) |

> **해시 불일치를 재시도하지 않는 이유**: 다시 물어도 둘 중 어느 쪽이 틀렸는지 바뀌지 않는다. 숫자가 맞을 때까지 계속 받는 Worker는 결국 받은 것이 무엇이든 학습한다.

### URL은 자격 증명이다

query string의 서명이 고객 데이터 읽기 권한이다. **로그·에러 메시지·파일명 어디에도 넣지 않는다.** 전송 실패는 예외의 **타입 이름만** 보고한다 — urllib 에러 메시지는 URL 전체를 담을 수 있다.

Dataset 내용도 로그에 남기지 않는다. 내용 검증(JSONL·필수 필드)은 컨테이너 안 QLoRA Runtime의 일이다.

---

## 6. Docker Hardening

```
docker run --rm --name gshared-job-<jobId>
  --gpus all
  --network none
  --read-only
  --cap-drop ALL
  --security-opt no-new-privileges
  --pids-limit 512
  --memory 16g
  --user 10001:10001
  --tmpfs /tmp:rw,noexec,nosuid,size=256m,uid=10001,gid=10001,mode=0700
  --tmpfs /job/cache:rw,nosuid,size=1g,uid=10001,gid=10001,mode=0700
  -v <model>:/job/model:ro
  -v <workspace>/input:/job/input:ro
  -v <workspace>/output:/job/output
  <repository>@sha256:<digest>
  --model-path /job/model --dataset-path /job/input/dataset.jsonl
  --output-dir /job/output --epochs N --batch-size N
  --learning-rate F --max-length N
```

**`-e`가 하나도 없다.** workerToken · User token · operator private key · Blockchain key · registry credential · Backend credential — 컨테이너는 이 중 무엇도 받지 않는다. 필요가 없다: 모델을 학습하고 파일을 쓴다. 서명과 업로드는 host에서 나중에 한다.

> **tmpfs에 uid/gid를 반드시 준다.** 맨 `--tmpfs`는 이미지가 빌드 때 chown한 디렉터리 위에 root 소유 파일시스템을 덮어씌우고, non-root 런타임은 자기 캐시를 못 쓴다. QLoRA smoke에서 Torch Inductor가 `PermissionError`로 죽으며 발견된 문제다.

**command는 argument list다.** shell string을 만들지 않고 `shell=True`를 쓰지 않는다. `learningRate`는 Backend에서 온 값이다.

Docker socket · host root는 마운트하지 않는다.

---

## 7. Exit code

| | |
|---|---|
| 0 | 성공 — **단, output 파일도 있어야 한다** |
| 10 | `INVALID_INPUT` |
| 11 | `MODEL_LOAD_FAILED` |
| 12 | `CUDA_UNAVAILABLE` |
| 13 | `CUDA_OOM` |
| 14 | `TRAINING_FAILED` |
| 15 | `OUTPUT_VALIDATION_FAILED` |
| 그 외 (1·137·143·255…) | `UNKNOWN_RUNTIME_EXIT` — **성공으로 취급하지 않는다** |
| 응답 없음 | `TIMED_OUT` |

이 표는 `system/runtime/qlora-sft/qlora_sft/failures.py`가 발행하는 계약이다. System이 임의로 바꾸지 않는다.

---

## 8. stdout event

`GSHARED_EVENT=<한 줄 JSON>` — `training_started` · `step` · `training_completed` · `failed`.

파싱해서 `RuntimeEvent`로 만들고 `RuntimeOutcome.events`에 담는다. `progress_percent = step / totalSteps × 100`도 계산해 둔다.

**아직 아무 데도 보내지 않는다.** JOB_PROGRESS는 8번이다.

marker가 붙지 않은 줄은 무시한다 — stdout은 PyTorch·bitsandbytes·CUDA와 공유하고, 끝난 학습이 경고 한 줄 때문에 실패로 보고되면 안 된다. malformed event도 건너뛸 뿐 예외를 던지지 않는다.

---

## 9. 동시성

```
receive loop (단일 소유자)
 → JOB_ACCEPTED 전송
 → acceptance.start_runtime(job)   ← 배경 스레드
 → receive loop 계속, HEARTBEAT 계속
 → RuntimeOutcome이 queue로
 → loop가 처리
```

6.5의 배경 스레드·큐를 그대로 쓴다. **WebSocket receiver thread를 추가하지 않았다.**

한 번에 하나: accept · cleanup · runtime이 같은 스레드와 같은 lock을 공유한다. 같은 Job의 두 번째 runtime은 시작되지 않는다 — GPU 하나에 컨테이너 둘, 같은 output 디렉터리에 쓰는 둘이 된다.

배경에서 실행하지 않으면: 심장박동이 멈추고 → Backend sweeper가 90초 뒤 offline 처리 → **컨테이너가 학습 중인 Job이 재배정된다.**

---

## 10. Output

`<workspace>/jobs/<jobId>/output/` — `adapter_config.json` · `adapter_model.safetensors` · `training_metrics.json`.

**삭제하지 않는다.** 9번이 업로드할 대상이다. 성공·실패 모두 JobSlot을 유지하는 이유도 같다 — 지금 놓으면 다음 Job이 workspace를 덮어쓴다.

업로드 · resultHash · submitResult · JOB_RESULT는 **하지 않는다.**

---

## 11. 실패와 abort seam

`RuntimeOutcome`을 **반환**한다. 전송하지 않는다.

| 실패 | |
|---|---|
| `CONTEXT_UNAVAILABLE` · `CONTEXT_MISMATCH` | |
| `MODEL_PREPARATION_FAILED` | |
| `DATASET_UNAVAILABLE` | |
| `IMAGE_REFUSED` · `IMAGE_PULL_FAILED` | |
| `DOCKER_UNAVAILABLE` | |
| 컨테이너 exit code 6종 + UNKNOWN + TIMED_OUT | |

`RuntimeOutcome.abort_tx_hash` 자리를 비워 두었다. 9번이 `abort_escrow_job`(6.5에 이미 있다)을 호출하고 그 해시를 여기 담아 JOB_FAILED로 연결하면 된다. **이번 단계에서 자동 abort를 걸지 않았다** — 어떤 실패에 예치금을 돌려줄지는 9번이 JOB_FAILED와 함께 정할 결정이다.

---

## 12. 현재 Live blocker

| | 상태 |
|---|---|
| Registry digest | **BLOCKED** — registry 미정, credential 없음. push하지 않았다 |
| Qwen3 catalog | **BLOCKED** — `base_models` seed는 `qwen2.5-7b` 1건 |
| `model_revision` | **BLOCKED** — 컬럼은 있으나 NULL (V10 TODO) |
| Dataset `storage_uri` | **BLOCKED** — 실제 업로드된 데이터셋 없음 |
| `workloads.image_digest` | **BLOCKED** — all-zero placeholder |

**System이 제공할 수 있는 값** (Backend 담당이 seed해야 함):

```
Qwen/Qwen3-0.6B   c1899de289a04d12100db370d81485cdf75e47ca
Qwen/Qwen3-1.7B   70d244cc86ccca08cf5af4e1e306ecf908b1ad5e
```

Local GPU smoke는 통과했지만 **production digest 경로는 BLOCKED이고, 그 검사를 끄거나 약화하지 않았다.** smoke는 별도 harness를 쓴다.

---

## 13. 다음 단계 연결 지점

**8번 JOB_PROGRESS** — `RuntimeOutcome.events`에 이미 `step`/`totalSteps`/`progress_percent`가 있다. 다만 현재는 컨테이너가 **끝난 뒤** 한꺼번에 파싱한다. 실시간 진행률을 보내려면 stdout을 **스트리밍**으로 읽어야 하고, 그것이 8번의 실제 작업이다.

**9번 JOB_RESULT / JOB_FAILED** — `RuntimeOutcome`(state·failure·detail·exit_code·output_dir·abort_tx_hash)이 그대로 입력이다.

**10번 JOB_CANCEL** — 컨테이너 이름이 `gshared-job-<jobId>`로 결정적이라 `docker stop`으로 멈출 수 있다. 지금은 연결하지 않았다.
