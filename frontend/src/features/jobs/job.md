# 작업(Job) — 학습 주문 만들기 · 내 작업 목록 · 상세 구현 계획 (프론트)

브랜치: `fe-feature/jobs`. 백엔드 job 도메인은 develop 에 머지됨(`be-feature/job-domain`, 2026-09-09 확인). AI 서버는 `origin/ai-feature/training-config`(`c6937d8`). 이 문서는 사용자가 GPU 학습 작업을 주문하고 진행을 보는 웹 화면을 정한다. **2026-09-09 프론트 구현 완료(목업 기준) — 아래는 구현 기준 설명.** 기존 PoC 임시 스키마(`QUEUED/…`)는 백엔드 모델로 교체했다.

## 0. 범위와 용어

- **Job = 학습 주문서.** 사용자가 "어떤 작업(workload)을, 어떤 베이스 모델로, 어떤 데이터셋으로, 어떤 GPU 등급에서, 어떤 학습 설정으로" 돌릴지 정하고 견적(예상 시간·비용)을 승인하면 생긴다. 이후 예치(FUNDED) → 워커 배정 → 실행 → 결과.
- **한다**: `/jobs` 내 작업 목록(상태 필터·페이지), `/jobs/[id]` 상세(진행률·epoch·비용·결과·취소), `/jobs/new` 작업 만들기(카탈로그 선택 → 학습 설정 AUTO/CUSTOM → 견적 → 생성), 네비 `작업` 탭, 랜딩 `지금 시작하기` CTA 연결, MSW 목업, 백엔드/AI 요청 스펙.
- **안 한다(이번 브랜치)**: 데이터셋 **업로드**(백엔드·AI 에 경로 없음 — 목록 선택만), 예치(FUNDED 전이 = Escrow, 미배포), 결과 파일 뷰어(다운로드 링크만), 실시간 WebSocket(폴링), 작업 로그 스트리밍, 재시도.
- **전제**: 라이브러리 추가 없음. 폼은 네이티브 `<form>` + zod/mini(`use-zod-form` 재사용 검토), 상태는 컴포넌트 로컬. 응답은 zod 경계 검증.

## 1. 백엔드 API

### 1-1. 있는 것 (develop, 2026-09-10 `be-feature/job-wizard-apis` 머지 후) — 프론트 스키마와 맞춤

| 메서드/경로 | 요청 | 응답 | 실패(`{ code, message }`) |
| --- | --- | --- | --- |
| `GET /api/workloads` | — | `[{ id, kind: TRAIN_LORA\|INFERENCE\|EVALUATION, name, description("" 가능) }]` | |
| `GET /api/base-models` | — | `[{ id, name, params("7B"), workloadIds[] }]` params 는 서버가 만든 표기 | |
| `GET /api/gpus` | — | `[{ id, model, vramMb, hourlyPrice: 0 }]` **살아 있는 워커를 모델명으로 묶은 등급** — 시간에 따라 바뀌고 빈 배열 가능. `hourlyPrice` 는 과금이 토큰 기준이라 0 → 화면에 안 씀 | |
| `POST /api/jobs/gpu-options` | `JobSpec`(평평) | `[{ …gpu, fit: RECOMMENDED\|OK\|TIGHT\|INSUFFICIENT, requiredVramMb, reason, availableWorkers }]` INSUFFICIENT 도 포함(선택 차단은 화면). 저장 안 함 | `NO_AVAILABLE_WORKER` 409, `NO_FEASIBLE_GPU`/`SCHEDULER_REJECTED` 422, `SCHEDULER_UNAVAILABLE` 503 |
| `POST /api/jobs/estimate` | `JobSpec + gpuId` | `{ trainingConfig{4값}, predictedRuntimeSec, estimatedCost }` AUTO 면 Scheduler 가 정한 설정. 저장 안 함 | 위와 같음 |
| `POST /api/datasets` | `{ sizeBytes, sampleCount, totalTokens, effectiveTokens, avgSeqLength, maxSeqLength, dataHash?(66), storageUri?(512) }` **메타만**(원본 파일 X) | `201 { datasetId, …같은 필드, createdAt }` | `INVALID_INPUT` 400 |
| `GET /api/datasets/{id}` | 본인 것만 | `200 Dataset` | `DATASET_NOT_FOUND` 404 |
| `POST /api/jobs` | `{ workloadId, baseModelId, datasetId, trainingMode, trainingPriority?(AUTO), trainingConfig?(CUSTOM) }` **gpuId·predicted·estimated 는 안 받음** — 서버가 Scheduler 로 다시 계산(가격 조작 방지) | `201 { jobId, recommendedWorkerId, predictedRuntimeSec, estimatedCost, schedulerModelVersion }` | `WORKLOAD_NOT_FOUND`/`BASE_MODEL_NOT_FOUND`/`DATASET_NOT_FOUND` 404, `INVALID_TRAINING_CONFIG` 400, Scheduler 계열 |
| `GET /api/jobs?status=&page=&size=` | 최신순 고정 | `200 { content: JobSummary[], page, totalPages, totalElements }` | |
| `GET /api/jobs/{jobId}` | 본인 것만 | `200 JobDetail` | `JOB_NOT_FOUND` 404 |
| `POST /api/jobs/{jobId}/cancel` | `CREATED/FUNDED/ASSIGNED` 만 | `200` | `JOB_NOT_FOUND` 404, `INVALID_JOB_STATUS` 409 |
| `POST /api/jobs/{jobId}/fund` | (Escrow 연동용, 화면 미사용) | `200` | |

```
JobSummary: { jobId, status, workloadId, progressPercent, estimatedCost, createdAt }
JobDetail : { jobId, status, workloadId, baseModelId, datasetId, trainingMode, epochs, batchSize, learningRate, maxLength,
              predictedRuntimeSec, actualRuntimeSec, estimatedCost, actualCost, assignedWorkerId, progressPercent, progressEpoch,
              progressTotalEpochs, resultHash, resultUrl, failReason, createdAt, startedAt, finishedAt }
              ※ gpuId 없음(V7: 워커 1대 = GPU 1개 → assignedWorkerId 가 곧 GPU). GPU 이름은 백엔드에 gpuModel 추가 요청(§8)
JobStatus : CREATED(예치 대기) → FUNDED(배정 대기) → ASSIGNED(시작 대기) → RUNNING → COMPLETED | FAILED,  CANCELLED
autoMode(견적) = trainingPriority(주문) : SPEED | BALANCED | TRAINING — 값 같고 이름만 다름(통일 요청 §8)
```

- `POST /uploads`(원본 파일)는 **없다** — 저장 위치(공급자 PC / S3·MinIO / 직접 전송)가 블록체인 회의 안건(6-3). 정해지면 붙인다. 그때까지 5단계는 데이터셋 **메타 + 파일 SHA-256 해시**만 등록하고 파일(데이터셋·어댑터·내 모델)은 브라우저에 남긴다.
- `effectiveTokens`(과금 기준, 패딩·잘림 반영)는 지금 `totalTokens` 를 그대로 보낸다. 견적 API 도 서버에서 같은 우회를 쓴다. 정확한 값은 토크나이저·배치 구성을 아는 쪽(AI)이 계산하는 게 맞다(§8).
- 실서버에서 4단계부터는 워커가 최소 1대 `ONLINE` 이어야 한다. 없으면 `/api/gpus` 빈 배열, `gpu-options` 409 `NO_AVAILABLE_WORKER` — 정상 동작. 화면은 "지금 사용 가능한 GPU 가 없습니다" 로 안내.

### 1-2. (해결됨) 카탈로그·워커·견적 API — 2026-09-10 develop 에 들어왔다. 위 1-1 로 이동

### 1-3. AI 서버 (`ai-feature/training-config`, FastAPI `/scheduler`)

| 경로 | 기능 | 상태 |
| --- | --- | --- |
| `POST /scheduler/training-config` `{ mode: SPEED\|BALANCED\|TRAINING, dataset: DatasetMetadata }` → `TrainingConfig{ epochs, batch_size, learning_rate, max_length }` | AUTO 설정 생성 | 구현됨 |
| `POST /scheduler/input` `{ training, dataset }` → `{ dataset, training, features }` | 설정+데이터셋 정책 검사(현재 규칙 0) + 특징 추출 | 구현됨. **예상 시간·비용은 아직 안 나온다** |

→ 견적의 핵심인 `predictedRuntimeSec`/`estimatedCost` 산출은 **AI 도 백엔드도 아직 없다.** §1-2 의 estimate API 를 요청하고, 프론트는 MSW 로 진행.

## 2. 화면 진입 · 흐름

```
네비 [작업](authed) / 랜딩 [지금 시작하기]
  ├─ guest → proxy: /login?next=/jobs(/new)
  └─ /jobs            GET /jobs?status&page  → 목록(빈 목록이면 "첫 작업 만들기" 카드)
     /jobs/new        카탈로그 4개 GET → 폼 → [견적 보기] POST /jobs/estimate → 견적 카드 → [작업 만들기] POST /jobs → /jobs/{id}
     /jobs/[id]       GET /jobs/{id} (RUNNING·ASSIGNED·FUNDED·CREATED 면 10초 폴링) → 진행률·비용·결과, [취소]
```

- 보호 경로 `/jobs`(하위 포함) → `proxy.ts` `PROTECTED` + matcher. 랜딩 `지금 시작하기` href `/` → `/jobs/new`.
- **2026-09-09 대시보드 통합**: 목록은 `/dashboard/jobs`(작업)·`/dashboard/workers`(공급) 로(2026-09-10 에 `?tab=` 쿼리 → 경로로 변경, 옛 `?tab=` 주소는 리다이렉트 호환). `/jobs`·`/workers` 는 대시보드 탭으로 리다이렉트(옛 링크 호환), `/jobs/new`·`/jobs/[id]` 는 그대로. 로그인 후 기본 목적지도 `/dashboard`(auth.md §8 결정 이행).
- **2026-09-10 셸 통일**: 랜딩과 로그인/회원가입을 뺀 모든 화면(대시보드·지갑·워크로드 등록·작업 상세)이 라우트 그룹 `(console)` 의 사이드바 셸 안에 있다. `(app)` 그룹(상단 SiteNav 셸)은 삭제. 사이드바 현재 항목: `/jobs/[id]` 는 대시보드로 표시.
- **2026-09-10 대시보드 개편(참고 디자인 "G-shared Dashboard" 구조 채택, 토큰은 AGENTS.md 유지)**: 라우트 그룹 `(console)/dashboard` — 상단 유리 네비 대신 **접히는 아이콘 사이드바**(56↔220px, localStorage; 대시보드·워크로드 등록·지갑·홈·내 계정·로그아웃), 헤더(킥커 "한 계정, 두 역할" · 실시간 시계 · 작업/워커 세그먼트 탭 + 개수), **지표 타일 4개**, **카드 그리드**(작업: #id·워크로드·상태·예상 비용·생성·진행 바·상세/취소 / 워커: id·OS·상태 점·GPU·VRAM·드라이버·벤치마크·힌트·해제). 백엔드에 없는 데이터(알림·수익·크레딧·온도·가동률·수신 토글)는 그리지 않음. 목록용 `jobsPage/jobRow`, `workersPage/workerCard` 는 삭제(대시보드 `_components` 로 대체).
- `/jobs/new` 는 지갑 연결 여부를 **막지 않는다**(예치는 다음 단계, Escrow 미배포). 대신 상단에 "예치는 지갑에서 진행됩니다 — 지갑 미연결 시 연결 안내" 한 줄(지갑 GET 1회).

## 3. 파일 구조 (AGENTS.md 골격)

```
src/
  app/(app)/jobs/
    page.tsx                         h1 "내 작업" + <JobsPage/>
    loading.tsx                      목록 스켈레톤
    new/page.tsx                     h1 "워크로드 등록" + <WorkloadWizard/>
    [jobId]/page.tsx                 <JobDetailPage jobId/> (Next 16: params 는 Promise → await)
    [jobId]/loading.tsx
  proxy.ts                           PROTECTED += '/jobs', matcher += '/jobs/:path*'
  features/auth/components/navAuth.tsx   authed: [작업][워커][지갑][로그아웃]
  app/(marketing)/_components/hero/heroSection.tsx  지금 시작하기 → /jobs/new
  features/jobs/
    job.md                           이 문서
    schema.ts                        JOBS_PATH · jobStatusSchema(7) · jobSummarySchema · jobPageSchema · jobDetailSchema
                                     · trainingConfigSchema(epochs/batchSize 정수>0, learningRate>0, maxLength>0) · estimateSchema
                                     · catalog: workloadSchema/baseModelSchema/datasetSchema/gpuSchema  — zod/mini 로 교체(현재 zod 본체)
    types.ts                         z.infer 들 + JobStatus
    api.ts                           getJobs / getJob / createJob / cancelJob / estimateJob(spec+gpuId) / getGpuOptions(spec) / uploadFile(kind, file, metadata?) / getCatalog — authFetch + parse
    errors.ts                        JOB_NOT_FOUND / INVALID_JOB_STATUS / INVALID_INPUT(서버 메시지) → 문구
    format.ts                        formatDuration(sec) "1시간 20분" · formatCost(n) "1,200 GSC" · 상태 라벨/톤 표(STATUS_META)
    components/jobsPage.tsx          'use client'. 상태 필터 필(전체/진행 중/완료/실패·취소) + 목록 + 페이지네이션(이전/다음) + 빈 상태
    components/jobRow.tsx            한 줄: #id · workload 이름 · StatusBadge · 진행률 바(RUNNING) · 비용 · 생성 시각 → /jobs/[id] 링크
    components/workloadWizard.tsx    'use client'. §4 위저드: WizardState · 스테퍼 · 단계별 완료 조건 · 이전/다음. 4·5 단계는 자리만
    components/stepWorkload.tsx      1단계: 유형 카드 3 → 제공 모델 카드
    components/stepConfig.tsx        2단계: 모델 출처(제공/내 모델 드롭) · 학습 수준 · 고급 직접 입력(use-zod-form) · 어댑터 드롭
    components/stepDataset.tsx       3단계: 데이터셋 파일 드롭(→ 브라우저 분석·진행률·5값 표시). 기존 데이터셋 선택은 2026-09-10 제거
    components/stepGpu.tsx           4단계: POST /jobs/gpu-options 결과 카드(적합도 배지·필요 VRAM·대기 워커) + 선택 시 아래 견적(estimateCard, 버튼 없음). 스펙 key 로 재조회
    components/stepUpload.tsx        5단계: 선택 요약 + 지갑 잔액(GET wallet → RPC balanceOf) vs 예상 비용 → [업로드하고 등록] / [예치금 예치하기] / [지갑 연결하기]
    dataset-analysis.ts              파일 → DatasetMetadata(스트리밍, 토큰 근사) — §4-3
    components/estimateCard.tsx      견적 결과: 설정 4값 · 예상 시간 · 예상 비용 · [작업 만들기] (5단계용, 현재 미사용)
    components/jobDetailPage.tsx     'use client'. 10초 폴링(종료 상태면 중단) · 헤더(상태·취소) · 진행 카드 · 설정/비용 카드 · 결과 카드
    components/jobProgress.tsx       진행률 바 + "epoch 3/10 · 42%" (StatusBadge 재사용)
    components/jobSkeleton.tsx       JobListSkeleton · JobDetailSkeleton
    mocks/handlers.ts                jobHandlers: 카탈로그 4 GET · gpu-options(VRAM 근사식) · estimate(메타데이터 기반) · uploads(multipart) · jobs CRUD. 생성한 작업(id≥1000)은 5s FUNDED → 5s ASSIGNED → 3s RUNNING → 3s 마다 +10% → COMPLETED 자동 전이. 상태는 **sessionStorage** 에 보존(새로고침에도 유지·전이 재개, 탭 닫으면 fixture 로)
    mocks/data.ts                    카탈로그 fixture(workload 2·모델 3·데이터셋 2·GPU 3) · 작업 7건(상태별 1개씩) · MOCK_ADVANCE_MS
  components/ui/progressBar.tsx      무상태. `<progress>` 대신 div 2개(브라우저별 스타일 편차 회피), aria-valuenow
  components/ui/select.tsx           네이티브 <select> 래퍼(input 과 같은 8px·높이 40/46) — 커스텀 드롭다운 안 만듦
  components/ui/fileDrop.tsx         파일 드롭 영역(끌어놓기/클릭, 선택 후 이름·크기·제거). File 만 들고 업로드 안 함
  components/ui/stepper.tsx          단계 표시(완료 체크·현재·이후, 완료 단계로 되돌아가기)
```

기존 `features/jobs/{schema,types,mocks}` 삭제·교체. `jobs/mocks/handlers.ts` 의 `GET /jobs`, `GET /jobs/:id` 는 새 스키마로 다시 쓴다. 배럴 없음, 컴포넌트 camelCase.

## 4. 워크로드 등록 (`/jobs/new`) — 5단계 위저드 (2026-09-09 개편, 5단계 전부 구현)

"작업 만들기" 한 페이지 폼을 **워크로드 등록** 5단계로 바꿨다(사용자 결정). 스테퍼(`ui/stepper`)로 진행을 보이고, 완료한 단계로는 되돌아갈 수 있다. 상태는 `WizardState` 하나(`workloadWizard.tsx`)에 모여 있고 각 단계는 그 일부를 편집한다.

| 단계 | 내용 | 완료 조건 | 상태 |
| --- | --- | --- | --- |
| 1 워크로드 | 유형 카드 3개: **학습(LoRA) / 추론 / 평가**(`Workload.kind`) → 유형을 고르면 그 유형을 지원하는 **제공 모델** 카드(`BaseModel.workloadIds` 필터). 유형을 바꾸면 모델 선택 초기화 | 유형 + 모델 | ✅ |
| 2 모델·설정 | **모델 출처**: 제공 모델 / **내 학습된 모델 가져오기**(파일 드롭) · **학습 수준**(학습 유형만): 빠르게/균형/충분히 + `<details>` "고급: 직접 입력" 4값(즉시 검증, 틀리면 다음 차단) · **기존 어댑터**(선택, 파일 드롭): 학습이면 "이어서 학습", 추론·평가면 "모델에 적용" | 내 모델 선택 시 파일 필수, 고급이 열려 있으면 값 유효 | ✅ |
| 3 데이터셋 | 유형별 문구(학습 데이터셋 / 입력 데이터 / 평가 데이터셋). **파일 드롭**만(기존 데이터셋 선택은 2026-09-10 제거). 파일을 올리면 **브라우저가 즉시 분석**해 메타데이터 5값(크기·샘플·토큰(근사)·평균·최대 길이)을 표시(§4-3) | 파일(+분석 완료) 또는 기존 데이터셋 | ✅ |
| 4 GPU 등급 · 견적 | `POST /jobs/gpu-options` 에 **메타데이터·모델·설정만** 보내 백엔드가 매긴 적합도(추천/가능/빡빡함/VRAM 부족)·필요 VRAM·대기 워커 수를 카드로. VRAM 부족은 비활성. **등급을 고르면 바로 아래에 견적**(`POST /jobs/estimate`, `estimateCard` 버튼 없음). 등급을 바꾸면 견적 다시 | GPU 선택 + 견적 수신 | ✅ |
| 5 업로드 | 선택 요약 + 4단계 견적 + **지갑 잔액 확인**: 백엔드 `GET /users/me/wallet` → SSAFY RPC 로 GSC 잔액. 잔액 ≥ 예상 비용이면 **[업로드하고 등록]**(이때 처음으로 파일이 서버로 — `POST /uploads` 데이터셋+metadata → 어댑터 → 내 모델, 있는 것만 → `POST /jobs` → `/jobs/{id}`). 부족하면 **[예치금 예치하기]**(`/wallet?deposit=<비용>` 새 탭) + 부족액 표시 + [잔액 다시 확인]. 지갑 미연결이면 [지갑 연결하기](새 탭). 위저드 선택은 유지 | 잔액 충분 | ✅ |

### 4-3. 데이터셋 분석 — 파일은 프론트에만, 서버엔 메타데이터만 (사용자 결정 2026-09-09)

AI 스케줄러는 파일을 보지 않고 `DatasetMetadata` 5값(`dataset_size_bytes, sample_count, total_tokens, avg_seq_length, max_seq_length`)만 받는다(`ai/app/schemas/dataset.py`). 그래서:
- 3단계에서 `dataset-analysis.ts` 가 파일을 **스트리밍으로 읽어**(JSONL/CSV/TXT 줄 단위, JSON 배열은 통째) 샘플 수·길이 통계를 뽑는다. JSON 줄은 문자열 값들을 이어 붙여 텍스트로 본다. CSV 첫 줄은 헤더.
- **토큰 수는 근사**: 토크나이저 없이 문자 종류별 비율(한글 1.5자·CJK 1자·그 외 4자 ≈ 1토큰, `ponytail:` 주석). 화면에 "토큰(근사)" 로 표시하고 견적 카드에 "실행 시 서버가 다시 계산" 안내. 모델별 토크나이저가 필요해지면 `estimateTokens` 만 교체.
- GPU 추천(4)·견적(5)은 `JobSpec = { workloadId, baseModelId, datasetMetadata, trainingMode, autoMode?, trainingConfig?, hasAdapter, ownModel }` 만 보낸다. 파일 업로드는 5단계 [업로드하고 등록] 에서만.
- 자체 점검: `estimateTokens/textOfJson/sampleText/summarize/analyzeDataset` 를 node 로 검사(스크래치 `da/t.mjs`, JSONL 1000줄 스트리밍·CSV 헤더·JSON 배열·빈 파일 EMPTY_DATASET).

- **파일은 File 객체로만 들고 있다**(어댑터·내 모델·데이터셋) — 마지막 단계까지. 업로드 API 는 백엔드에 없어 `POST /api/uploads`(multipart: file, kind, metadata) 를 제안(`api-checklist.md`).
- 백엔드 `Job` 엔티티에는 `kind`·어댑터·내 모델 개념이 없다 → `CreateJobRequest` 에 `adapterId?`, `modelId?` 확장 요청(프론트는 이미 보냄, 서버가 무시해도 동작). `kind` 는 워크로드 카탈로그가 담는다.
- 이전 한 페이지 폼(`jobCreateForm.tsx`)은 삭제. `estimateCard.tsx` 는 5단계에서 재사용(`createLabel` prop).

## 5. 목록 · 상세

**목록(`/jobs`)**: 필터 필 `전체 | 진행 중(CREATED·FUNDED·ASSIGNED·RUNNING) | 완료 | 실패·취소` — 백엔드 `status` 는 단일 값이라 "진행 중"은 프론트에서 4번 호출하지 않고 **전체를 받아 클라이언트 필터**(size=50) 로 시작; 작업이 수십 건을 넘으면 `status` 파라미터로 전환. 페이지네이션은 이전/다음 두 버튼(`page/totalPages`). 15초 폴링(탭 보일 때만). 빈 목록 → "첫 작업을 만들어 보세요" 카드 + `/jobs/new` 버튼.

**상세(`/jobs/[id]`)**
| 카드 | 내용 |
| --- | --- |
| 헤더 | `작업 #12` · StatusBadge · [취소](CREATED/FUNDED/ASSIGNED 만, `confirm`) |
| 진행 | RUNNING: 진행률 바 + `epoch 3/10 · 42%` + 시작 시각 · 예상 남은 시간(predicted − 경과) / CREATED: "예치 대기 — 지갑에서 예치하면 배정이 시작됩니다"(Escrow 전까지 안내만) / FUNDED·ASSIGNED: "워커 배정 대기·시작 대기" / FAILED: `failReason` 이 응답에 없음 → 백엔드에 노출 요청(§8) |
| 설정 | workload·모델·GPU 이름(카탈로그 조회로 id→이름), 모드, 학습 설정 4값 |
| 비용·시간 | 예상 vs 실제(있으면) 두 열 |
| 결과 | COMPLETED: `resultUrl` 다운로드 링크 + `resultHash` 복사 |

상태 라벨/톤: CREATED 예치 대기(warning) · FUNDED 배정 대기(neutral) · ASSIGNED 시작 대기(neutral) · RUNNING 학습 중(primary) · COMPLETED 완료(success) · FAILED 실패(danger) · CANCELLED 취소됨(neutral).

## 6. 목업(MSW) — `jobHandlers`

- 카탈로그 3 GET(workloads·base-models·gpus): fixture 그대로. `GET /datasets` 는 기존 데이터셋 선택 제거(2026-09-10)와 함께 뺐다.
- `POST /jobs/estimate`: AUTO 면 mode 별 고정 설정(SPEED: 1 epoch/…), CUSTOM 이면 받은 설정. `predictedRuntimeSec = sampleCount×epochs / (gpu 계수)` 정도의 단순식, `estimatedCost = ceil(runtime/3600) × hourlyPrice`. **정확할 필요 없음** — 화면용.
- `POST /jobs`: id 증가, `CREATED` 로 추가. 목업 전용 **자동 전이 타이머**: 5초 뒤 FUNDED → 5초 ASSIGNED → RUNNING(진행률 +10%/3초, epoch 갱신) → COMPLETED(`resultUrl` 가짜). 폴링 UI·상태 배지 전부를 목업만으로 확인.
- `GET /jobs`: status 필터·페이지 계산 흉내(`page/size`). `GET /jobs/:id`: 404 재현용 id `999`.
- `POST /jobs/:id/cancel`: 진행 전 상태만 200, 아니면 409 `INVALID_JOB_STATUS`.
- 상태는 `sessionStorage('gshared.mock.jobs')` 에 보존 → 새로고침해도 만든 작업이 남고 자동 전이가 중간 단계에서 재개된다. 워커 목업처럼 메모리에만 두면 새로고침마다 사라져 헷갈렸던 경험 반영. 탭을 닫으면 fixture 로 돌아간다.

## 7. 에러 메시지 (`errors.ts`)

| 코드 | 문구 | 어디에 |
| --- | --- | --- |
| `JOB_NOT_FOUND` 404 | 작업을 찾을 수 없습니다. | 상세 본문 + 돌아가기 |
| `INVALID_JOB_STATUS` 409 | 현재 상태에서는 취소할 수 없습니다. 목록을 새로 고칩니다. | 상세·대시보드 취소 |
| `INVALID_INPUT` 400 | (서버 메시지) | 폼 상단 |
| `DATASET_NOT_FOUND` 404 | 데이터셋 정보를 찾을 수 없습니다. 3단계에서 파일을 다시 올려 주세요. | 5단계 |
| `WORKLOAD_NOT_FOUND` / `BASE_MODEL_NOT_FOUND` 404 | 1단계에서 다시 선택 안내 | 5단계 |
| `INVALID_TRAINING_CONFIG` 400 | 학습 설정 4값 확인 | 2·5단계 |
| **`NO_AVAILABLE_WORKER` 409** | 지금 일할 수 있는 GPU 가 없습니다. **설정은 그대로 두고** 잠시 후 다시 시도 | 4·5단계 — 기다리면 해결 |
| **`NO_FEASIBLE_GPU` 422** | 이 설정으로 실행할 수 있는 GPU 가 없습니다. **모델·batch·max length 를 줄여** 주세요 | 4·5단계 — 기다려도 소용없음 |
| `SCHEDULER_REJECTED` 422 | 스케줄러가 요청을 거절했습니다. 설정을 확인 | 4·5단계 |
| `SCHEDULER_UNAVAILABLE` 503 | 스케줄러에 연결할 수 없습니다. 잠시 후 재시도 | 4·5단계 |

409(기다리면 됨)와 422(요청을 바꿔야 함)는 사용자에게 할 말이 정반대라 문구를 갈랐다.

## 8. 결정 필요

### 8-0. 백엔드 "프론트 연동 안내(2026-09-10)" 에 대한 프론트 답변·요청
- ① `jobDetail.gpuId` → **나(백엔드가 `JobDetailResponse` 에 `gpuModel` 문자열 추가) 로 부탁**. 프론트는 우선 `assignedWorkerId` 를 "배정 워커" 로 보이게 바꿨고, `gpuModel` 이 오면 그 자리에 GPU 이름을 붙인다.
- ② `createJobRequest` 에서 `gpuId·predictedRuntimeSec·estimatedCost` 제거 완료. AUTO 는 `trainingPriority` 로 보낸다. **이름은 `autoMode` 로 통일해 주면 좋겠다**(견적과 주문이 같은 값이라 한 이름이 덜 헷갈림). 바꾸면 프론트는 한 줄 수정.
- ③ `effectiveTokens`: 프론트 분석기는 토크나이저 없이 문자 비율로 근사한 `totalTokens` 만 낸다. 패딩·잘림(maxLength 는 2단계, 배치 구성은 서버)까지 반영한 값은 **AI/백엔드가 계산하는 게 맞다**. 지금은 `totalTokens` 를 그대로 보내니 **선택 항목으로 열어 두거나 서버에서 채우는 쪽**을 권한다.
- ④ `hourlyPrice` 표시 제거 완료(대기 워커 수만 남김).
- 추가: `GET /api/datasets` 목록은 필요 없다(기존 데이터셋 선택 기능 제거). `POST /uploads` 는 저장 위치 확정 후.


- [ ] **카탈로그 4종 + estimate API** — 백엔드 요청(§1-2). 이게 없으면 `/jobs/new` 는 목업으로만 동작. 지갑·워커와 같은 패턴으로, 직접 넣을지 요청할지는 사용자 결정. 카탈로그 **데이터**(어떤 workload/모델을 지원하는지)는 시스템·AI 담당이 정해야 함(승인 이미지 목록).
- [ ] **`FAILED` 사유 노출** — `Job.failReason` 이 있는데 `JobDetailResponse` 에 없다. 한 필드 추가 요청.
- [ ] **상세에 데이터셋 이름·메타** — `JobDetailResponse` 에 `datasetName`, `datasetMetadata{sizeBytes, sampleCount, totalTokens, avgSeqLength, maxSeqLength}` 추가 요청. 업로드(`POST /api/uploads`) 때 받은 값을 Job 에 저장해 돌려주면 됨. 프론트는 optional 로 받아 있으면 [데이터셋] 표를 그린다(목업은 이미 붙임).
- [ ] **[예치금 예치하기] 의 목적지** — 지금은 `/wallet?deposit=<GSC>` 새 탭. Escrow(또는 예치금 계정) 가 정해지면 지갑 페이지에 `deposit` 쿼리를 읽어 예치 흐름을 붙인다. 잔액 기준은 지갑 GSC 보유량(예치금 계정이 따로 생기면 그 잔액으로 교체).
- [ ] **예치(FUNDED) 흐름** — Escrow 배포 후: 상세의 "예치 대기" 카드에 [예치하기] → 지갑 `approve + fundJob` 서명 → 백엔드가 이벤트로 FUNDED. 이번 범위 밖, 화면 자리만 남김.
- [ ] **데이터셋 업로드** — 백엔드·AI 어디에도 없음. 이번엔 목록 선택만, "업로드 준비 중" 안내. 별도 기획(파일 크기·저장소·메타 추출 주체).
- [ ] **AI 서버 직접 호출 여부** — 하지 않는다. 오리진·인증이 달라지고(FastAPI 별도 포트) 견적 로직이 두 곳에 갈라진다. 백엔드 estimate 로 감싼다.
- [ ] **폴링 간격** — 목록 15초, 상세 10초(RUNNING 진행률 체감). WS 구독은 나중에.
- [x] **경로** — `/jobs`, `/jobs/new`, `/jobs/[id]`. 백엔드·워커 프로토콜 모두 "job".
- [x] **기존 `features/jobs` 목업** — 삭제·교체(PoC 임시).
- [x] **`use-zod-form` 위치** — `features/auth/hooks` 의 훅을 jobs 폼이 그대로 import. 두 도메인이 쓰게 됐으니 `src/hooks/use-zod-form.ts` 로 올리는 게 AGENTS.md 원칙에 맞음 — 다음 정리 때(auth 파일 이동이라 이번 MR 에 안 섞음).

## 9. 진행 상태 (2026-09-09)

- **프론트 구현 완료(목업 기준)**: §3 의 파일 전부. `tsc --noEmit`, `eslint src` 통과.
- **2026-09-09 개편**: `/jobs/new` 를 워크로드 등록 5단계 위저드로. 5단계 전부 목업 검증 통과 — JSONL 300줄 드롭 → 분석(샘플 300·토큰 5,300·평균 18) → gpu-options 요청에 메타데이터만 실림 → 3등급 적합도 → 견적(1분·400 GSC) → [업로드하고 등록 (데이터셋 · 어댑터)] → `POST /uploads` ×2 → `POST /jobs`(datasetId 5000) → `/jobs/1000` 예치 대기. 1~3단계 검증(유형→모델 필터·리셋, 내 모델 파일 필수, 고급 epochs=0 즉시 오류·다음 차단, 어댑터/데이터셋 드롭, 기존 데이터셋과 상호 배타, 4단계 자리·다음 비활성, 스테퍼로 1단계 복귀 시 선택 유지). 4·5단계는 다음 작업.
- **목업 검증(헤드리스 Chrome, 사용자 dev 서버 3000)**: guest → `/login?next=%2Fjobs` / 로그인 → 목록 7건(상태별 배지, RUNNING 진행률 바) / 필터 진행 중 4·실패·취소 2 / 네비 `작업 | 워커 | 지갑 | 로그아웃` / 만들기: 미선택 제출 오류 → workload 선택 시 모델 목록 필터 → AUTO 견적(3 epochs·56분·600 GSC) → GPU 변경 시 stale + 만들기 비활성 → 재견적 → "이 설정 직접 고치기" 로 CUSTOM 전환·값 복사 → epochs=0 오류 → 재견적 → 생성 → `/jobs/1000` 예치 대기 + 취소 버튼 → 폴링으로 학습 중(30%) → 완료(결과 링크, 취소 버튼 사라짐) / 두 번째 작업 즉시 취소 → 취소됨 / `/jobs/999` → 찾을 수 없음. 콘솔 오류 0.
- **실서버 연동 상태**: `GET/POST /api/jobs`, `GET /jobs/{id}`, `POST cancel` 은 develop 에 있어 목업을 끄면 목록·상세·취소는 동작. **카탈로그 4종·`POST /jobs/estimate` 가 없어 `/jobs/new` 는 실서버에서 "선택 항목을 불러오지 못했습니다"** → §1-2 요청 대기(직접 넣을지 요청할지 결정).
- 실서버 확인 절차(카탈로그·견적 반영 후): 로그인 → `/jobs/new` 카탈로그 → 견적 → 생성 → `/jobs` 목록 → 상세 → 취소.

## 10. 작업 순서 (커밋 단위, 예정)

1. `[fe-feature] 작업 스키마·API·목업(카탈로그·견적·자동 전이·sessionStorage) + 보호 경로·네비 탭·랜딩 CTA` — schema/types/api/errors/format, mocks, `proxy.ts`, `navAuth.tsx`, `heroSection.tsx`
2. `[fe-feature] 작업 목록·상세 화면` — `(app)/jobs`, `[jobId]`, `ui/progressBar`, `jobsPage/jobRow/jobDetailPage/jobProgress/jobSkeleton`
3. `[fe-feature] 작업 만들기 폼` — `(app)/jobs/new`, `ui/select`, `jobCreateForm/estimateCard`, job.md 갱신
