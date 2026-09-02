# G-shared API 명세서 (프론트엔드 요구 기준)

프론트엔드 화면 구현에 필요한 백엔드 API 전체 목록. 백엔드 협의용 초안이며, 확정 시 `features/<도메인>/schema.ts`의 기준이 된다.

- 작성일: 2026-09-02
- 기준 디자인: `docs/G-shared Design System (1) (1).fig` — 17개 화면(P1~P17) 텍스트 레이어 전체 추출
- 기준 코드: `poc/gpu-compute-poc/backend` (Spring Boot, `/api/jobs`, `/api/workers`)

## 0. 근거 표기

| 표기 | 의미 |
|---|---|
| ✅ | Figma 화면에 해당 데이터/액션이 직접 존재 |
| 🟢 | PoC 백엔드에 이미 구현됨 |
| 🔶 | 화면에는 있으나 필드명·구조는 프론트 제안 (백엔드 협의) |

화면 텍스트는 확인했으나 컴포넌트 프로퍼티(예: 필드 타입)는 추정이므로 필드명은 전부 🔶 수준으로 보면 된다.

---

## 1. 화면 ↔ API 매핑

| 화면 | 필요한 API |
|---|---|
| P1 랜딩 | `GET /stats/public`, `GET /nodes?limit=3`, `GET /chain/recent` |
| P2 로그인 | `POST /auth/login`, `GET /wallet/nonce`, `POST /wallet/connect` |
| P3 회원가입 | `POST /auth/signup` |
| P4 대시보드 | `GET /me`, `GET /jobs?status=RUNNING&limit=1`, `GET /provider/summary`, `GET /nodes?limit=3`, `GET /stats/platform` |
| P5 GPU 목록 | `GET /nodes` (필터·정렬) |
| P6 등록 Step1 | `GET /workloads` |
| P6 등록 Step2 | `GET /workloads/{id}`, `GET /projects`, `GET /projects/{id}/versions` |
| P6 등록 Step3 | (브라우저 내 분석, API 없음) |
| P6 등록 Step4 | `POST /jobs/estimate`, `GET /wallet` |
| P6 등록 Step5 | `POST /jobs`, `POST /jobs/{id}/dataset/upload-url`, `POST /jobs/{id}/dataset/complete`, `POST /jobs/{id}/dataset/abort` |
| P7 내 작업 | `GET /jobs`, `GET /jobs/counts` |
| P8 작업 현황 | `GET /jobs/{id}`, `GET /jobs/{id}/progress`, `GET /jobs/{id}/metrics`, `GET /jobs/{id}/events`, `POST /jobs/{id}/cancel` |
| P9 결과 (완료) | `GET /jobs/{id}`, `GET /jobs/{id}/result`, `GET /jobs/{id}/settlement`, `GET /jobs/{id}/artifacts/{aid}/download`, `POST /ratings` |
| P9 결과 (실패) | `GET /jobs/{id}`, `GET /jobs/{id}/settlement`, `POST /jobs/{id}/retry`, `POST /jobs/{id}/report` |
| P9 결과 (진행 중) | `GET /jobs/{id}/progress`, `GET /jobs/{id}/settlement/projected` |
| P10 모델 프로젝트 | `GET /projects`, `POST /projects` |
| P11 버전 관리 | `GET /projects/{id}`, `GET /projects/{id}/versions`, `POST /projects/{id}/versions/{v}/activate`, `GET /projects/{id}/versions/compare` |
| P12 내 노드 | `GET /provider/summary`, `GET /provider/nodes`, `GET /provider/income/daily`, `GET /notifications` |
| P13 GPU 등록 | `GET /provider/agent/latest`, `POST /provider/pairing`, `GET /provider/pairing/{code}`, `GET /provider/pricing-range`, `PATCH /provider/nodes/{id}` |
| P14 지갑 | `GET /wallet`, `GET /wallet/usage`, `GET /wallet/transactions`, `POST /wallet/topup` |
| P15 이력 | `GET /jobs?status=terminal`, `GET /wallet/transactions`, `GET /ratings/mine` |
| P17 작업 현황판 | `GET /jobs?groupBy=column` (또는 `GET /jobs` 전체) |
| 공통 헤더 | `GET /me` (닉네임, 토큰 잔액 `TokenBalanceChip`) |

---

## 2. 공통 규약

### 2.1 기본

| 항목 | 값 |
|---|---|
| Base URL | `NEXT_PUBLIC_API_BASE_URL`. 로컬 `http://localhost:8080/api/v1` (랜딩 시안 C에 `POST /api/v1/jobs/estimate` 표기 → `/v1` 접두 제안 🔶) |
| Content-Type | `application/json; charset=utf-8` |
| 시간 | ISO 8601 UTC (`2026-08-31T10:14:00Z`). 표시 포맷은 프론트 담당 |
| 금액 (`Amount`) | GST **소수 문자열** (`"0.44"`, `"1240.0"`). 화면 전체가 소수 GST 표기. 온체인 최소 단위 변환은 백엔드 담당 🔶 |
| 시간 길이 | 초 단위 정수 (`runtimeSec`). "47분 50초" 포맷은 프론트 |
| ID | 문자열. Job은 화면에 `#301` 정수형 표기 → 정수 ID 또는 `displayNo` 별도 제공 🔶 |

### 2.2 인증 ✅

P2/P3: **이메일+비밀번호 계정**이 기본, **지갑 연결은 선택**(정산·예치 시 필수). "🦊 지갑으로 연결" 버튼도 있어 지갑 단독 로그인도 가능.

- 세션: `Authorization: Bearer <accessToken>`. 리프레시는 httpOnly 쿠키 🔶
- 지갑 연결 상태는 `GET /me`의 `wallet` 필드로 판단. 미연결 시 예치·충전 API는 `403 WALLET_REQUIRED`
- 공개 엔드포인트: `/auth/*`, `/wallet/nonce`, `/stats/public`, `/chain/recent`, `/nodes` (읽기), `/workloads`

### 2.3 목록 응답

```json
{ "items": [], "nextCursor": "opaque | null", "total": 12 }
```

- `?cursor=&limit=` (기본 20, 최대 100). `total`은 탭 카운트("전체 12")용, 비용 크면 생략 가능
- P7 하단에 `Pagination` 컴포넌트 → 오프셋 필요 시 `?page=&size=` 병행 🔶

### 2.4 에러 응답

```json
{ "code": "WORKER_TIMEOUT", "message": "Worker가 10분 이상 응답하지 않아 작업이 중단되었습니다.", "details": {} }
```

프론트는 `code`를 한국어 매핑. `message`는 P9 실패 화면처럼 그대로 노출하는 곳도 있으니 **사용자용 한국어**로 내려주면 매핑 테이블이 줄어든다 🔶.

### 2.5 뮤테이션

- 자동 재시도 금지(AGENTS.md). 생성 계열은 `Idempotency-Key: <uuid>` 헤더
- 성공 시 변경된 리소스 반환
- 온체인 액션(예치·환불·정산·faucet)은 백엔드가 실행하고 `txHash`를 반환. 프론트 지갑 서명은 로그인 인증에만 사용 🔶

### 2.6 실시간 ✅

P8(진행률·Loss·로그), P6 Step2 페어링 대기, P6 Step5 업로드 후 매칭 시작이 실시간 갱신 대상.

- 1차: 폴링 (`GET /jobs/{id}/progress` 3~5초)
- 2차: SSE `GET /jobs/{id}/stream` — 이벤트 `progress | metric | event | status`
- 랜딩 시안 C의 Webhook(`job.completed`)은 개발자용, 프론트 범위 밖

---

## 3. 엔드포인트 요약

| # | Method | Path | 설명 | 근거 |
|---|---|---|---|---|
| **인증/계정** | | | | |
| 1 | POST | `/auth/signup` | 회원가입 | ✅ P3 |
| 2 | POST | `/auth/login` | 이메일 로그인 | ✅ P2 |
| 3 | POST | `/auth/logout` | 로그아웃 | 🔶 |
| 4 | POST | `/auth/refresh` | 토큰 갱신 | 🔶 |
| 5 | GET | `/me` | 내 정보·지갑·잔액 | ✅ 헤더 |
| 6 | GET | `/wallet/nonce` | 지갑 서명 nonce | ✅ P2 |
| 7 | POST | `/wallet/connect` | 지갑 연결/지갑 로그인 | ✅ P2 |
| **지갑** | | | | |
| 8 | GET | `/wallet` | 잔액·누적 지표 | ✅ P14 |
| 9 | GET | `/wallet/usage` | 이번 달/지난 달/작업당 평균 | ✅ P14 |
| 10 | GET | `/wallet/transactions` | 거래 내역 | ✅ P14, P15 |
| 11 | POST | `/wallet/topup` | 충전 (1차 faucet) | ✅ P14 |
| **GPU 노드 (마켓)** | | | | |
| 12 | GET | `/nodes` | GPU 목록 (필터·정렬) | ✅ P5, 🟢 `/workers` |
| 13 | GET | `/nodes/{nodeId}` | GPU 상세 | 🟢 |
| **워크로드 카탈로그** | | | | |
| 14 | GET | `/workloads` | 워크로드 카드 목록 | ✅ P6-1 |
| 15 | GET | `/workloads/{id}` | 베이스 모델 상세 | ✅ P6-2 |
| **작업** | | | | |
| 16 | POST | `/jobs/estimate` | AI 추천 GPU + 비용 견적 | ✅ P6-4 |
| 17 | POST | `/jobs` | 작업 생성 + 예치 | ✅ P6-4→5, 🟢 확장 |
| 18 | GET | `/jobs` | 내 작업 목록 | ✅ P7, P15, P17 |
| 19 | GET | `/jobs/counts` | 탭별 개수 | ✅ P7 |
| 20 | GET | `/jobs/{id}` | 작업 상세 | ✅ P8, 🟢 확장 |
| 21 | GET | `/jobs/{id}/progress` | 진행률·ETA·소진 | ✅ P8 |
| 22 | GET | `/jobs/{id}/metrics` | Loss 시계열 | ✅ P8 |
| 23 | GET | `/jobs/{id}/events` | 진행 로그(타임라인) | ✅ P8 |
| 24 | GET | `/jobs/{id}/stream` | SSE | 🔶 |
| 25 | POST | `/jobs/{id}/cancel` | 작업 취소 | ✅ P8, P9 |
| 26 | POST | `/jobs/{id}/retry` | 재시도 (같은 설정) | ✅ P9 실패 |
| 27 | POST | `/jobs/{id}/report` | 문제 신고 | ✅ P9 실패 |
| 28 | GET | `/jobs/{id}/result` | 결과 지표·아티팩트·해시 | ✅ P9 |
| 29 | GET | `/jobs/{id}/settlement` | 정산 내역 (확정) | ✅ P9 |
| 30 | GET | `/jobs/{id}/settlement/projected` | 정산 예정 (실시간) | ✅ P9 진행중 |
| 31 | GET | `/jobs/{id}/artifacts/{aid}/download` | 어댑터 다운로드 URL | ✅ P9, P11 |
| **데이터셋 업로드** | | | | |
| 32 | POST | `/jobs/{id}/dataset/upload-url` | presigned URL 발급 | ✅ P6-5 |
| 33 | POST | `/jobs/{id}/dataset/complete` | 업로드 완료·해시 검증 | ✅ P6-5 |
| 34 | POST | `/jobs/{id}/dataset/abort` | 업로드 취소 | ✅ P6-5 |
| 35 | POST | `/adapters/upload-url` | 외부 어댑터(.safetensors) 업로드 | ✅ P6-2 파일 가져오기 |
| **모델 프로젝트** | | | | |
| 36 | GET | `/projects` | 프로젝트 목록 | ✅ P10 |
| 37 | POST | `/projects` | 새 프로젝트 | ✅ P10 |
| 38 | GET | `/projects/{id}` | 프로젝트 상세 | ✅ P11 |
| 39 | GET | `/projects/{id}/versions` | 버전 타임라인 | ✅ P11 |
| 40 | POST | `/projects/{id}/versions/{v}/activate` | 이 버전으로 전환 | ✅ P11 |
| 41 | GET | `/projects/{id}/versions/compare` | 버전 비교 | ✅ P11 |
| **공급자** | | | | |
| 42 | GET | `/provider/summary` | 수익 타일·온라인 수·평점 | ✅ P4, P12 |
| 43 | GET | `/provider/nodes` | 내 노드 목록 | ✅ P12 |
| 44 | PATCH | `/provider/nodes/{id}` | 가격 수정 | ✅ P13 |
| 45 | GET | `/provider/income/daily` | 일별 수익 | ✅ P12 |
| 46 | GET | `/provider/agent/latest` | Worker 설치파일 정보 | ✅ P13 |
| 47 | POST | `/provider/pairing` | 페어링 코드 발급 | ✅ P13 |
| 48 | GET | `/provider/pairing/{code}` | 페어링 상태·감지 사양 | ✅ P13 |
| 49 | GET | `/provider/pricing-range` | 허용 가격 범위·동급 평균 | ✅ P13 |
| **평가·알림·통계** | | | | |
| 50 | POST | `/ratings` | 공급자 평가 | ✅ P9 |
| 51 | GET | `/ratings/mine` | 내가 남긴 평가 | ✅ P15 |
| 52 | GET | `/notifications` | 알림 | ✅ P12 |
| 53 | GET | `/stats/public` | 랜딩 지표 | ✅ P1 |
| 54 | GET | `/stats/platform` | 플랫폼 현황 (로그인) | ✅ P4 |
| 55 | GET | `/chain/recent` | 최근 온체인 정산 티커 | ✅ P1 |

---

## 4. 공통 타입

```ts
type Amount = string            // "0.44" GST 소수 문자열
type ISODate = string

// ---- 작업 상태 (P8 타임라인 + P7 탭 + PoC 통합) ----
type JobStatus =
  | 'CREATED'      // 작업 생성
  | 'DEPOSITED'    // 예치 완료 (P6-5 "예치 완료")
  | 'UPLOADING'    // 데이터셋 업로드 중 (P6-5)
  | 'MATCHING'     // AI 스케줄러 GPU 탐색 (P17 "대기")
  | 'ASSIGNED'     // 노드 배정 (PoC DISPATCHED)
  | 'RUNNING'      // 실행 중
  | 'COMPLETED'    // 완료 (PoC SUCCESS)
  | 'FAILED'       // 실패
  | 'CANCELLED'    // 취소 (P7 "취소됨")
  | 'RESULT_UNKNOWN' // PoC 유지
// P7 탭 매핑: 실행 중 = DEPOSITED~RUNNING, 완료 = COMPLETED, 실패 = FAILED|RESULT_UNKNOWN, 취소 = CANCELLED

type WorkloadKind = 'TRAIN_LORA' | 'INFERENCE_EVAL'          // P6-1 TypeToggle
type TrainingLevel = 'SIMPLE' | 'STANDARD' | 'PRECISE'       // P6-2 간단/표준/정밀
type NodeState = 'BUSY' | 'IDLE' | 'OFFLINE'                  // GpuIllustration State
type TransactionType = 'DEPOSIT' | 'REFUND' | 'PAYOUT' | 'FAUCET' | 'EXTRA_CHARGE'  // P14 배지: 예치/환불/지급/faucet 지급/추가 결제
type TransactionStatus = 'PENDING' | 'CONFIRMED' | 'FAILED'
type FailureCode = 'WORKER_TIMEOUT' | 'OOM' | 'IMAGE_PULL_FAILED' | 'DATASET_INVALID' | 'USER_CANCELLED' | 'UNKNOWN'  // P9 "WORKER_TIMEOUT" 확인, 나머지 🔶

interface Me {
  id: string
  nickname: string               // P3 닉네임
  email: string
  wallet: { address: string; connected: boolean } | null
  balance: Amount                // 헤더 TokenBalanceChip
  isProvider: boolean            // 노드 1개 이상 보유
}

interface GpuNode {              // 🟢 WorkerResponse 기반 + P5 카드
  nodeId: string
  name: string                   // "gpu_master", "node_busan" (P8/P15 공급자 노드명)
  gpuModel: string               // "RTX 4090"
  vramGb: number                 // 24
  cudaVersion: string            // "12.4"
  driverVersion: string          // "560.94"
  benchmarkScore: number         // 8340
  hourlyPrice: Amount            // "0.42"
  state: NodeState
  supports: WorkloadKind[]       // 필터 "학습 지원 / 추론 지원"
  rating: number | null          // 4.6
  reliability: number | null     // 신뢰도 92 (0~100)
  matchScore?: number            // 정렬 "매칭 점수" (조회자 컨텍스트 있을 때)
  lastHeartbeat: ISODate
}

interface Workload {             // P6-1 카드
  id: string                     // "qwen25-7b-lora"
  kind: WorkloadKind
  title: string                  // "Qwen2.5-7B LoRA 파인튜닝"
  tags: string[]                 // ["텍스트", "대화/지시"]
  minVramGb: number              // 24
  dataFormats: string[]          // ["jsonl", "csv"]
  image: string                  // "gshared/qwen25-7b-lora:1.0"
  baseModel: { name: string; sizeGb: number; source: string; cacheable: boolean }  // P6-2
  maxDatasetBytes: number        // 5GB
}

interface JobSummary {           // P7/P17 행
  jobId: string
  displayNo: number              // 301
  name: string
  status: JobStatus
  node: { nodeId: string; name: string; gpuModel: string } | null
  progressPct: number | null
  etaSec: number | null
  runtimeSec: number | null
  costSpent: Amount              // "0.21 GST 소진"
  costFinal: Amount | null       // 완료 후 "0.18 GST"
  refund: Amount | null          // "+0.31 GST 환불"
  failureCode: FailureCode | null
  createdAt: ISODate
}

interface Job extends JobSummary {   // P8 상세
  workloadId: string
  projectId: string | null
  baseVersion: string | null     // 이어서 학습한 버전 "v2"
  trainingLevel: TrainingLevel
  dataset: { fileName: string; sizeBytes: number; samples: number; tokens: number | null; sha256: string }
  deposit: Amount                // 0.44
  depositTxHash: string | null
  startedAt: ISODate | null
  completedAt: ISODate | null
  // 🟢 PoC 그대로 노출 (개발자 패널용)
  containerId: string | null
  imageDigest: string | null
  containerExitCode: number | null
}

interface JobProgress {          // P8 Metrics + CostTracker
  status: JobStatus
  progressPct: number            // 62.4
  epoch: { current: number; total: number }
  step: { current: number; total: number }
  loss: number | null
  elapsedSec: number
  etaSec: number | null
  stepsPerSec: number | null     // 0.86
  costSpent: Amount              // 0.21
  costProjected: Amount          // 0.33
  deposit: Amount                // 0.44
  updatedAt: ISODate
}

interface JobEvent {             // P8 Timeline
  type: 'CREATED' | 'DEPOSITED' | 'MATCHING' | 'ASSIGNED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
  at: ISODate
  message: string                // "RTX 4090 (gpu_master) 배정"
  txHash?: string
}

interface JobResult {            // P9 완료
  finalLoss: number | null
  evalLoss: number | null
  totalSteps: number
  samples: number
  resultHash: string             // "sha256:4c8e…a019"
  artifacts: Array<{ id: string; kind: 'LORA_ADAPTER'; fileName: string; sizeBytes: number; expiresAt: ISODate }>
  version: { projectId: string; version: string } | null   // 결과가 저장된 프로젝트 버전 (v3)
}

interface Settlement {           // P9 정산
  txHash: string | null
  deposit: Amount
  charged: Amount                // 실제 비용 (실패 시 0)
  refund: Amount
  providerPayout: Amount
  policy: 'ACTUAL_USAGE' | 'FULL_REFUND'   // P9 실패 "FULL_REFUND 정책"
  runtimeSec: number
  settledAt: ISODate | null
}

interface Transaction {          // P14/P15
  id: string
  occurredAt: ISODate
  type: TransactionType
  status: TransactionStatus
  description: string            // "Job #301 차액"
  amount: Amount                 // 부호 포함 "+0.11"
  jobId: string | null
  txHash: string | null
}

interface Project {              // P10
  id: string
  name: string                   // "고객문의 봇"
  workloadId: string
  baseModel: string              // "Qwen2.5-7B-Instruct"
  datasetLineage: string[]       // ["cs_qa.jsonl", "delivery.jsonl"]
  currentVersion: string         // "v3"
  versionCount: number
  updatedAt: ISODate
}

interface ProjectVersion {       // P11
  version: string                // "v3"
  label: string                  // "delivery.jsonl 추가 학습 (v2에서 분기)"
  parentVersion: string | null
  isActive: boolean
  jobId: string
  finalLoss: number
  evalLoss: number
  trainedSamples: number
  cost: Amount
  artifact: { id: string; fileName: string; sizeBytes: number }
  createdAt: ISODate
}

interface ProviderNode extends GpuNode {   // P12 NodeRow
  currentJob: { jobId: string; displayNo: number; progressPct: number } | null
  lastJobEndedAt: ISODate | null
  earningsToday: Amount
  warnings: string[]             // "드라이버 버전이 낮습니다"
}
```

---

## 5. 인증 / 계정

### 5.1 POST `/auth/signup` ✅ P3

```json
{ "nickname": "순철", "email": "you@example.com", "password": "********", "agreedTerms": true }
```

응답 `201`: `{ accessToken, me: Me }`. 에러 `409 EMAIL_TAKEN`, `400 VALIDATION_ERROR`(8자 이상 영문+숫자).

### 5.2 POST `/auth/login` ✅ P2

```json
{ "email": "you@example.com", "password": "********" }
```

응답 `200`: `{ accessToken, me: Me }`. 에러 `401 INVALID_CREDENTIALS`.

### 5.3 POST `/auth/logout`, POST `/auth/refresh` 🔶

refresh는 httpOnly 쿠키 기반, 본문 없음. 응답 `{ accessToken }`.

### 5.4 GET `/me` ✅

응답 `200`: `Me`. 헤더 `TokenBalanceChip`과 지갑 연결 여부 판단에 매 페이지 사용 → 캐시 짧게(30초).

### 5.5 GET `/wallet/nonce?address=` → POST `/wallet/connect` ✅ P2 "🦊 지갑으로 연결"

```json
// connect request
{ "address": "0xAb12…9F04", "nonce": "…", "signature": "0x…" }
// response
{ "accessToken": "…", "me": { "...Me" }, "linked": true }
```

- 로그인 상태에서 호출 → 현재 계정에 지갑 **연결**
- 비로그인 상태 → 지갑 주소로 계정 조회/생성 후 **로그인**
- 에러 `409 WALLET_LINKED_TO_OTHER`

---

## 6. 지갑 (P14)

### 6.1 GET `/wallet` ✅

MetricTile 4개 라벨 확정: 사용 가능 / 누적 충전 / 누적 사용 / 네트워크 차감.

```json
{
  "address": "0xAb12…9F04",
  "network": "testnet",
  "available": "1240.0",
  "escrowLocked": "0.44",
  "totalTopup": "1500.0",
  "totalSpent": "260.0",
  "networkFees": "0.0"
}
```

### 6.2 GET `/wallet/usage` ✅

```json
{ "thisMonth": "84.0", "lastMonth": "112.0", "avgPerJob": "0.27" }
```

### 6.3 GET `/wallet/transactions` ✅ P14, P15 결제 이력

| 쿼리 | 설명 |
|---|---|
| `type` | `TransactionType` 필터 |
| `cursor`, `limit` | 페이지 |

응답: `{ items: Transaction[], nextCursor }`

### 6.4 POST `/wallet/topup` ✅

프리셋 50/100/300/500은 프론트 상수. "테스트넷 faucet 지급 · 결제 연동은 2차".

```json
// request
{ "amount": "100" }
// response 202
{ "transaction": { "...Transaction", "status": "PENDING" } }
```

에러 `429 FAUCET_COOLDOWN`, `403 WALLET_REQUIRED`.

---

## 7. GPU 노드 (P5 마켓, P1/P4 카드)

### 7.1 GET `/nodes` ✅ (🟢 `/workers` 확장)

| 쿼리 | 설명 (P5 FilterBar) |
|---|---|
| `minVramGb` | 24, 12 |
| `supports` | `TRAIN_LORA` / `INFERENCE_EVAL` |
| `maxHourlyPrice` | "0.3" |
| `state` | `IDLE`, `BUSY`, `OFFLINE` |
| `sort` | `matchScore` (기본) / `price` / `benchmark` / `rating` |
| `workloadId` | 매칭 점수 계산 컨텍스트 (선택) |
| `cursor`, `limit` | |

응답

```json
{
  "summary": { "online": 14, "idle": 11 },
  "items": [ { "...GpuNode" } ],
  "nextCursor": null
}
```

`summary`는 P5 타이틀 "온라인 노드 14 · 유휴 11".

### 7.2 GET `/nodes/{nodeId}` 🟢

응답 `GpuNode` + `benchmark` 상세(🟢 `BenchmarkResponse`).

---

## 8. 워크로드 카탈로그 (P6 Step1·2)

### 8.1 GET `/workloads?kind=` ✅

응답 `Workload[]`. 카드 4종 확인: Qwen2.5-7B LoRA, YOLOv8-m, SDXL LoRA, Whisper-medium.

### 8.2 GET `/workloads/{id}` ✅

`Workload` + `baseModel` 상세 ("15.2GB · HuggingFace · 노드에 캐시된 경우 다운로드 생략").

---

## 9. 작업 (P6~P9, P17)

### 9.1 POST `/jobs/estimate` ✅ P6 Step4

AI 스케줄러 GPU 후보 비교 + 비용 요약. 데이터셋은 아직 미업로드 → 브라우저 분석값 전달.

```json
// request
{
  "workloadId": "qwen25-7b-lora",
  "trainingLevel": "STANDARD",
  "baseVersion": { "projectId": "p1", "version": "v2" },
  "dataset": { "sizeBytes": 52428800, "samples": 12480, "tokens": 3820000, "format": "jsonl", "sha256": "9f2a…c41b" }
}
// response
{
  "estimateId": "est-8d1",
  "expiresAt": "2026-09-02T09:20:00Z",
  "candidates": [
    { "nodeId": "n1", "gpuModel": "RTX 4090", "recommended": true, "etaSec": 2940, "cost": "0.34", "reliability": 92, "matchScore": 0.91 },
    { "nodeId": "n2", "gpuModel": "RTX 3090", "recommended": false, "etaSec": 4680, "cost": "0.29", "reliability": 78, "matchScore": 0.74 }
  ],
  "bufferRate": 0.3,
  "deposit": "0.44",
  "balance": "1240.0"
}
```

`deposit = cost × (1 + bufferRate)` — "예측 오차 버퍼 (+30%)".

### 9.2 POST `/jobs` ✅ P6 Step4 "승인하고 0.44 GST 예치하기" (🟢 확장)

헤더 `Idempotency-Key`.

```json
// request
{
  "estimateId": "est-8d1",
  "nodeId": "n1",
  "name": "Qwen2.5-7B LoRA 파인튜닝",
  "projectId": "p1"
}
// response 201
{ "job": { "...Job", "status": "DEPOSITED", "depositTxHash": "0x71cd…80a4" } }
```

- 예치를 원자적으로 수행. 에러 `422 INSUFFICIENT_BALANCE`, `410 ESTIMATE_EXPIRED`, `409 NODE_UNAVAILABLE`, `403 WALLET_REQUIRED`
- `projectId` 없으면 새 프로젝트 자동 생성(v1)
- PoC `{ workerId, workload }`는 `estimateId` 없이 직접 지정하는 개발용 경로로 유지 가능

### 9.3 데이터셋 업로드 ✅ P6 Step5

"예치 완료 후에만 업로드", "업로드한 해시가 3단계 SHA-256과 일치하는지 서버가 검증".

| Method | Path | 요청 | 응답 |
|---|---|---|---|
| POST | `/jobs/{id}/dataset/upload-url` | `{ fileName, sizeBytes, contentType }` | `{ url, method: "PUT", headers, expiresAt }` |
| POST | `/jobs/{id}/dataset/complete` | `{ sha256 }` | `Job` (status → `MATCHING`) 또는 `422 HASH_MISMATCH` |
| POST | `/jobs/{id}/dataset/abort` | 없음 | `Job` (status → `CANCELLED`, 예치 전액 환불) |

프론트는 presigned URL로 직접 PUT, 진행률(39.1/50.0 MB · 2.4 MB/s)은 `XMLHttpRequest.upload.onprogress`. 대용량은 멀티파트 presigned로 확장 🔶.

### 9.4 POST `/adapters/upload-url` ✅ P6 Step2 "파일 가져오기"

외부 `.safetensors` 어댑터 업로드. 응답 `{ adapterId, url, ... }`. `POST /jobs`에 `externalAdapterId`로 전달 🔶.

### 9.5 GET `/jobs` ✅ P7, P15, P17

| 쿼리 | 설명 |
|---|---|
| `status` | 콤마 구분. 별칭 `active`(DEPOSITED~RUNNING), `terminal`(COMPLETED,FAILED,CANCELLED) |
| `sort` | `createdAt` 기본 |
| `cursor`, `limit` / `page`, `size` | |

응답 `{ items: JobSummary[], nextCursor, total }`. P17 칸반은 프론트에서 status로 그룹핑.

### 9.6 GET `/jobs/counts` ✅ P7 탭

```json
{ "all": 12, "active": 1, "completed": 9, "failed": 1, "cancelled": 1 }
```

### 9.7 GET `/jobs/{id}` ✅ 🟢

응답 `Job`. 에러 `404 JOB_NOT_FOUND`, `403 FORBIDDEN`.

### 9.8 GET `/jobs/{id}/progress` ✅ P8

응답 `JobProgress`. 폴링 대상. 종료 상태면 마지막 스냅샷.

### 9.9 GET `/jobs/{id}/metrics?metric=loss&from=&to=` ✅ P8 LossChart

```json
{ "metric": "loss", "points": [{ "step": 100, "value": 2.31 }, { "step": 1560, "value": 0.842 }], "downsampled": true }
```

### 9.10 GET `/jobs/{id}/events` ✅ P8 Timeline

응답 `{ items: JobEvent[] }`. 상세 로그 라인은 2차(`/jobs/{id}/logs`).

### 9.11 GET `/jobs/{id}/stream` 🔶 SSE

```
event: progress   data: JobProgress
event: metric     data: { step, loss }
event: event      data: JobEvent
event: status     data: { status }
```

### 9.12 POST `/jobs/{id}/cancel` ✅ P8, P9 진행중

`DEPOSITED`~`RUNNING`에서 가능. 응답 `Job`. 미사용 예치금은 `REFUND` 거래로 환급 (P7 "매칭 전 취소 +0.15 GST 환불"). 에러 `409 JOB_NOT_CANCELLABLE`.

### 9.13 POST `/jobs/{id}/retry` ✅ P9 실패

```json
{ "mode": "SAME" | "EDIT", "excludeFailedNode": true }
```

- `SAME`: 같은 설정으로 새 견적·예치까지 진행 → `201 { job }` (잔액 부족 시 `422`)
- `EDIT`: 설정 복원용 초안 반환 → `200 { draft: { workloadId, trainingLevel, baseVersion, dataset } }`, 프론트가 P6 Step1로 이동

### 9.14 POST `/jobs/{id}/report` ✅ P9 "문제 신고"

```json
{ "category": "NODE_FAULT" | "RESULT_INVALID" | "BILLING" | "OTHER", "message": "…" }
```

응답 `201 { reportId }`.

### 9.15 GET `/jobs/{id}/result` ✅ P9 완료

응답 `JobResult`. 미완료면 `409 JOB_NOT_COMPLETED`.

### 9.16 GET `/jobs/{id}/settlement` ✅ P9

응답 `Settlement`. 완료/실패/취소 후에만. P9 실패 정보(`failureCode`, `failureMessage`, 중단 시점 `progressPct/epoch/step`, `lastHeartbeatAt`, 노드명, 부분 결과 여부)는 `Job`에 `failure` 객체로 포함 🔶:

```json
"failure": { "code": "WORKER_TIMEOUT", "message": "Worker가 10분 이상 응답하지 않아 작업이 중단되었습니다.", "stoppedAt": { "progressPct": 34, "epoch": 1, "step": 850 }, "lastHeartbeatAt": "2026-08-29T14:11:48Z", "checkpointSaved": false }
```

### 9.17 GET `/jobs/{id}/settlement/projected` ✅ P9 진행중

```json
{ "deposit": "0.44", "spent": "0.21", "projectedFinal": "0.33", "projectedRefund": "0.11" }
```

`/progress`에 포함돼 있으면 생략 가능.

### 9.18 GET `/jobs/{id}/artifacts/{artifactId}/download` ✅ P9, P11

응답 `{ url, expiresAt }` (presigned GET). 보관 기한(P9 "9/7까지 보관") 지나면 `410 ARTIFACT_EXPIRED`.

---

## 10. 모델 프로젝트 (P10, P11)

### 10.1 GET `/projects` ✅

응답 `{ items: Project[] }`.

### 10.2 POST `/projects` ✅ "+ 새 프로젝트"

```json
{ "name": "고객문의 봇", "workloadId": "qwen25-7b-lora" }
```

응답 `201 Project`. 실제 v1은 첫 작업 완료 시 생성.

### 10.3 GET `/projects/{id}` ✅ P11 헤더

`Project` + `lastTrainedAt`.

### 10.4 GET `/projects/{id}/versions` ✅

응답 `{ items: ProjectVersion[] }` (최신 순). "여기서 추가 학습" → P6 Step2에 `baseVersion` 프리셋 (API 아님).

### 10.5 POST `/projects/{id}/versions/{version}/activate` ✅ "이 버전으로 전환"

응답 `Project` (`currentVersion` 갱신).

### 10.6 GET `/projects/{id}/versions/compare?versions=v1,v2,v3` ✅

```json
{ "versions": ["v1","v2","v3"], "finalLoss": [1.020, 0.741, 0.612], "evalLoss": [1.115, 0.802, 0.688], "trainedSamples": [12480, 12480, 3240], "cost": ["0.31","0.28","0.33"] }
```

`/versions` 응답으로 프론트 계산 가능 → **없어도 됨**, 우선순위 낮음.

---

## 11. 공급자 (P4 MyNodes, P12, P13)

Worker 등록·heartbeat·공유 중단/재시작은 **에이전트가 WebSocket으로 수행** (P12 알림 문구). 프론트는 조회·페어링·가격만.

### 11.1 GET `/provider/summary` ✅ P4, P12

```json
{
  "onlineNodes": 2, "totalNodes": 2, "rating": 4.6,
  "earnings": { "total": "265.0", "afterSettlement": "246.6", "thisMonth": "84.2", "today": "18.4" }
}
```

### 11.2 GET `/provider/nodes` ✅ P12

응답 `{ items: ProviderNode[] }`.

### 11.3 PATCH `/provider/nodes/{nodeId}` ✅ P13 Step3 / P12

```json
{ "hourlyPrice": "0.42" }
```

응답 `ProviderNode`. 에러 `422 PRICE_OUT_OF_RANGE` ("허용 범위를 벗어난 금액은 저장되지 않습니다").

### 11.4 GET `/provider/income/daily?from=&to=` ✅ P12

```json
{ "items": [{ "date": "2026-08-31", "jobs": 2, "uptimeSec": 15120, "amount": "18.4" }] }
```

### 11.5 GET `/provider/agent/latest` ✅ P13 Step1

```json
{ "version": "1.0", "fileName": "gshared-worker-1.0.exe", "sizeBytes": 50331648, "url": "https://…", "sha256": "…", "minDriver": "560.94" }
```

### 11.6 POST `/provider/pairing` ✅ P13 Step2

응답 `201 { code: "GS-4F7K-92XQ", expiresAt }` (10분).

### 11.7 GET `/provider/pairing/{code}` ✅ P13 Step2→3 (폴링)

```json
{
  "status": "WAITING" | "CONNECTED" | "EXPIRED",
  "node": {
    "nodeId": "n9", "gpuModel": "RTX 4090", "vramGb": 24, "cudaVersion": "12.4",
    "driverVersion": "560.94", "os": "Windows 11", "benchmarkScore": 8340
  } | null
}
```

`CONNECTED` 시 감지 사양 표시. 벤치마크는 Worker 자동 측정("직접 입력 불가").

### 11.8 GET `/provider/pricing-range?gpuModel=` ✅ P13 Step3

```json
{ "min": "0.18", "max": "0.60", "peerAverage": "0.40" }
```

### 11.9 등록 완료 ✅ "등록하고 공유 시작"

`PATCH /provider/nodes/{nodeId}` `{ hourlyPrice, acceptingJobs: true }`로 처리. 별도 엔드포인트 불필요.

---

## 12. 평가 · 알림 · 통계

### 12.1 POST `/ratings` ✅ P9 "평가 남기기"

```json
{ "jobId": "301", "score": 5, "comment": "예상보다 빨리 끝났고 Loss도 잘 떨어졌습니다." }
```

응답 `201`. 작업당 1회 → `409 ALREADY_RATED`.

### 12.2 GET `/ratings/mine` ✅ P15 내 평가

```json
{ "items": [{ "jobId": "301", "node": { "name": "gpu_master", "gpuModel": "RTX 4090" }, "score": 5, "comment": "…", "createdAt": "…" }] }
```

### 12.3 GET `/notifications?unread=` ✅ P12 알림

```json
{ "items": [{ "id": "nt1", "level": "WARN" | "INFO", "message": "RTX 3060 드라이버 버전이 낮습니다. 560.94 이상으로 업데이트하세요.", "nodeId": "n2", "createdAt": "…", "readAt": null }] }
```

`PATCH /notifications/{id}/read` 🔶.

### 12.4 GET `/stats/public` ✅ P1 (비인증)

```json
{ "onlineNodes": 14, "runningJobs": 3, "completedJobs": 128, "avgHourlyPrice": "0.42", "registeredGpus": 47, "users": 312, "cloudDiscountPct": 77 }
```

### 12.5 GET `/stats/platform` ✅ P4 "플랫폼 현황"

```json
{ "waitingJobs": 2, "onlineNodes": 14, "utilizationPct": 21.4 }
```

### 12.6 GET `/chain/recent?limit=` ✅ P1 티커

```json
{ "items": [{ "type": "PAYOUT" | "DEPOSIT" | "REFUND", "txHash": "0x3fe0…1b77", "amount": "+24.1", "at": "…" }] }
```

---

## 13. 작업 상태 머신

```
CREATED → DEPOSITED → UPLOADING → MATCHING → ASSIGNED → RUNNING → COMPLETED
                │           │          │          │          └→ FAILED (FULL_REFUND 또는 부분 청구)
                └───────────┴──────────┴──────────┴→ CANCELLED (미사용 예치 환불)
```

| 전이 | 트리거 | 거래 |
|---|---|---|
| CREATED→DEPOSITED | `POST /jobs` | `DEPOSIT` −deposit |
| DEPOSITED→UPLOADING | 첫 `upload-url` 발급 | |
| UPLOADING→MATCHING | `dataset/complete` 해시 일치 | |
| MATCHING→ASSIGNED | 스케줄러 | |
| RUNNING→COMPLETED | 결과 검증 | `PAYOUT` 공급자, `REFUND` 차액 |
| RUNNING→FAILED | Worker 타임아웃 등 | `REFUND` 전액(공급자 귀책) |
| 예치 초과 | 실행 중 예치금 소진 | `EXTRA_CHARGE` (P14 "Job #295 예치 초과") |
| *→CANCELLED | `cancel` / `dataset/abort` | `REFUND` 미사용분 |

PoC 매핑: `DISPATCHED→ASSIGNED`, `SUCCESS→COMPLETED`, `RESULT_UNKNOWN` 유지(실패 탭).

---

## 14. 에러 코드

| HTTP | code | 화면 근거 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 공통 |
| 401 | `UNAUTHORIZED`, `INVALID_CREDENTIALS` | P2 |
| 403 | `FORBIDDEN`, `WALLET_REQUIRED` | P2/P3 "정산·예치에는 지갑 연결이 필요" |
| 404 | `JOB_NOT_FOUND`, `NODE_NOT_FOUND`, `PROJECT_NOT_FOUND` | |
| 409 | `EMAIL_TAKEN`, `WALLET_LINKED_TO_OTHER`, `JOB_CONFLICT` 🟢, `JOB_NOT_CANCELLABLE`, `JOB_NOT_COMPLETED`, `NODE_UNAVAILABLE`, `ALREADY_RATED` | |
| 410 | `ESTIMATE_EXPIRED`, `PAIRING_EXPIRED`, `ARTIFACT_EXPIRED` | P6-4, P13, P9 |
| 413 | `DATASET_TOO_LARGE` | P6-3 "최대 5GB" |
| 422 | `INSUFFICIENT_BALANCE`, `HASH_MISMATCH`, `PRICE_OUT_OF_RANGE`, `DATASET_INVALID` | P6-4, P6-5, P13 |
| 429 | `FAUCET_COOLDOWN` | P14 |
| 503 | `WORKER_OFFLINE` | |

작업 실패 사유(`Job.failure.code`)는 HTTP 에러가 아니라 데이터: `WORKER_TIMEOUT` ✅ 외 목록은 백엔드 정의.

---

## 15. 미결 사항 (백엔드 협의)

| # | 항목 | 프론트 제안 |
|---|---|---|
| 1 | 인증 | 이메일 계정 + 선택적 지갑 연결. 지갑 단독 로그인도 허용 |
| 2 | 금액 표현 | 소수 GST 문자열. 온체인 단위 변환은 백엔드 |
| 3 | 경로 접두 | `/api/v1` (랜딩 시안 C 표기 근거) |
| 4 | 견적 유효기간 | `estimateId` 10분. 만료 시 재견적 |
| 5 | 업로드 | presigned PUT 단일. 5GB 상한이면 멀티파트 필요 여부 확인 |
| 6 | 실시간 | 1차 폴링 3~5초, 2차 SSE |
| 7 | 예치 초과(`EXTRA_CHARGE`) | 자동 차감인지 사용자 승인인지. P14에 "추가 결제"만 있음 |
| 8 | `RESULT_UNKNOWN` | 사용자에게 실패로 보일지, 정산 정책은 무엇인지 |
| 9 | 페어링 | 코드 → 노드 연결 방식이 에이전트 구현과 일치하는지 (PoC는 `worker-config.json` 방식) |
| 10 | `GET /jobs` `total` | 탭 카운트용. 비싸면 `/jobs/counts`만 제공 |
| 11 | 결제 이력(P15) | 지갑 거래 내역과 동일 데이터임을 화면이 명시 → 별도 API 없음 |
