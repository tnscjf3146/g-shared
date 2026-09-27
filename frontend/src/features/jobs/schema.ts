import * as z from 'zod/mini'

// 경로 상수는 여기(React 의존 없음) — mocks 가 api.ts 를 import 하면 auth store 가 서버 번들로 끌려감(wallet.md §3)
export const JOBS_PATH = '/jobs'
/** 카탈로그 3종 — develop 에 있음(2026-09-10 be-feature/job-wizard-apis). gpus 는 살아 있는 워커를 모델명으로 묶어 만든 등급이라 시간에 따라 바뀌고 비어 있을 수 있다 */
export const CATALOG_PATHS = { workloads: '/workloads', baseModels: '/base-models', gpus: '/gpus' } as const
/** 데이터셋 메타데이터 등록·조회(develop). 원본 파일 업로드가 아니라 분석 결과 저장 — 파일 저장 위치는 미정(블록체인 회의 6-3) */
export const DATASETS_PATH = '/datasets'

/** back JobStatus 8단계 — CANCELLING 은 취소·실패 후 예치금 정리(환불) 대기 중인 중간 상태 */
export const jobStatusSchema = z.enum(['CREATED', 'FUNDED', 'ASSIGNED', 'RUNNING', 'CANCELLING', 'COMPLETED', 'FAILED', 'CANCELLED'])
export const trainingModeSchema = z.enum(['AUTO', 'CUSTOM'])
/** AUTO 학습 방향. 견적(estimate·gpu-options)에서는 `autoMode`, 주문(POST /jobs)에서는 `trainingPriority` 이름으로 보낸다(값은 같음) */
export const autoModeSchema = z.enum(['SPEED', 'BALANCED', 'TRAINING'])

const positiveInt = (name: string) =>
  z.number().check(z.refine(Number.isInteger, `${name}은(는) 정수여야 합니다.`), z.positive(`${name}은(는) 1 이상이어야 합니다.`))

/** 어댑터 이름 — 그대로 다운로드 파일 이름이 된다. 경로 구분자·제어문자를 막아야 저장이 안전하다(한글·영숫자·공백·`-_.` 만, 앞뒤 공백 금지) */
export const ADAPTER_NAME_MAX = 60
export const adapterNameSchema = z
  .string()
  .check(
    z.minLength(1, '어댑터 이름을 입력해 주세요.'),
    z.maxLength(ADAPTER_NAME_MAX, `어댑터 이름은 ${ADAPTER_NAME_MAX}자까지 입력할 수 있습니다.`),
    z.regex(/^[가-힣a-zA-Z0-9][가-힣a-zA-Z0-9 ._-]*[가-힣a-zA-Z0-9._-]$|^[가-힣a-zA-Z0-9]$/, '한글·영문·숫자와 - _ . 공백만 쓸 수 있고, 공백으로 끝날 수 없습니다.'),
  )

/** 학습 설정 4값(back SchedulerTrainingConfig 와 동일). 상한 없음 — 실행 가능성은 서버가 판단 */
export const trainingConfigSchema = z.object({
  epochs: positiveInt('epochs'),
  batchSize: positiveInt('batchSize'),
  learningRate: z.number().check(z.positive('learningRate 는 0 보다 커야 합니다.')),
  maxLength: positiveInt('maxLength'),
})

/** CUSTOM 폼 입력(문자열) → 숫자. 비어 있으면 coerce 가 0 → positive 에서 걸린다 */
export const customConfigFormSchema = z.object({
  epochs: z.coerce.number().check(z.refine(Number.isInteger, '정수를 입력해 주세요.'), z.positive('1 이상을 입력해 주세요.')),
  batchSize: z.coerce.number().check(z.refine(Number.isInteger, '정수를 입력해 주세요.'), z.positive('1 이상을 입력해 주세요.')),
  learningRate: z.coerce.number().check(z.positive('0 보다 큰 값을 입력해 주세요.')),
  maxLength: z.coerce.number().check(z.refine(Number.isInteger, '정수를 입력해 주세요.'), z.positive('1 이상을 입력해 주세요.')),
})

// ── 카탈로그(back WorkloadResponse · BaseModelResponse · GpuResponse) ──────────
/** 워크로드 유형 — 학습(LoRA) / 추론 / 평가. 유형에 따라 2단계(학습 수준)·3단계(데이터셋 의미)가 달라진다. 지금 seed 는 TRAIN_LORA 하나 */
export const workloadKindSchema = z.enum(['TRAIN_LORA', 'INFERENCE', 'EVALUATION'])
export const workloadSchema = z.object({ id: z.number(), kind: workloadKindSchema, name: z.string(), description: z.string() })
export const baseModelSchema = z.object({ id: z.number(), name: z.string(), params: z.string(), workloadIds: z.array(z.number()) })
/** AI DatasetMetadata 5값(camelCase). 프론트가 파일을 분석해 만들고 백엔드는 그대로 AI 에 넘긴다(job.md §4-3) */
export const datasetMetadataSchema = z.object({
  sizeBytes: z.number().check(z.positive()),
  sampleCount: z.number().check(z.positive()),
  totalTokens: z.number().check(z.positive()),
  avgSeqLength: z.number().check(z.positive()),
  maxSeqLength: z.number().check(z.positive()),
})
/** GPU 등급 = 살아 있는 워커를 모델명으로 묶은 것. id 는 모델명에서 계산(재기동해도 같음). hourlyPrice 는 과금이 토큰 기준이라 0 으로 온다 → 화면에 안 쓴다 */
export const gpuSchema = z.object({ id: z.number(), model: z.string(), vramMb: z.number(), hourlyPrice: z.number() })
/** POST /jobs/gpu-options 응답 원소 — INSUFFICIENT 도 목록에 남아 온다(선택 차단은 화면) */
export const gpuFitSchema = z.enum(['RECOMMENDED', 'OK', 'TIGHT', 'INSUFFICIENT'])
export const gpuOptionSchema = z.object({ ...gpuSchema.shape, fit: gpuFitSchema, requiredVramMb: z.number(), reason: z.string(), availableWorkers: z.number() })
// 이름표용 카탈로그 — 목록·상세·위저드가 id 를 사람이 읽는 이름으로 바꿀 때 쓴다.
// gpus 는 여기 없다: 살아 있는 워커에 따라 시시각각 변하는 무거운 조회라, 이름표와 한 묶음으로
// 받으면 그쪽 실패 하나에 워크로드·모델 이름까지 통째로 사라진다(수정요청서 FE-03).
// 고른 GPU 등급 표시는 4단계에서 고르는 순간 위저드 상태에 담는다.
export const catalogSchema = z.object({
  workloads: z.array(workloadSchema),
  baseModels: z.array(baseModelSchema),
})

// ── 데이터셋(back CreateDatasetRequest · DatasetResponse) ──────────
/** POST /datasets 본문 — 메타 5값 + effectiveTokens(과금 기준) + dataHash(파일 SHA-256, 0x+64) + storageUri(파일 저장 전이라 null) */
export const createDatasetRequestSchema = z.object({
  ...datasetMetadataSchema.shape,
  effectiveTokens: z.number().check(z.positive()),
  dataHash: z.nullable(z.string().check(z.maxLength(66))),
  storageUri: z.nullable(z.string().check(z.maxLength(512))),
})
export const datasetSchema = z.object({
  datasetId: z.number(),
  ...datasetMetadataSchema.shape,
  effectiveTokens: z.number(),
  dataHash: z.nullable(z.string()),
  storageUri: z.nullable(z.string()),
  createdAt: z.string(),
})

// ── GPU 추천·견적(back JobSpecRequest · EstimateJobRequest · EstimateResponse) — 파일 없이 메타데이터만 ──────────
/** 워크로드·모델·설정·데이터셋 메타 — GPU 추천과 견적이 공통으로 받는 몸통(평평한 형태) */
export const jobSpecSchema = z.object({
  workloadId: z.number(),
  baseModelId: z.number(),
  datasetMetadata: datasetMetadataSchema,
  trainingMode: trainingModeSchema,
  autoMode: z.optional(autoModeSchema),
  trainingConfig: z.optional(trainingConfigSchema),
  hasAdapter: z.boolean(),
  ownModel: z.boolean(),
})
export const estimateRequestSchema = z.object({ ...jobSpecSchema.shape, gpuId: z.number() })
/** 아무것도 저장하지 않는다 — 설정을 만질 때마다 불러도 된다. AUTO 면 trainingConfig 는 Scheduler 가 정한 값 */
export const estimateSchema = z.object({
  trainingConfig: trainingConfigSchema,
  predictedRuntimeSec: z.number().check(z.positive()),
  estimatedCost: z.number().check(z.positive()),
})

// ── Job(back DTO 그대로) ─────────────────────────────────────────
/** POST /jobs 본문 — 견적·GPU 선택은 서버가 Scheduler 로 다시 계산한다(가격 조작 방지). AUTO 면 trainingPriority, CUSTOM 이면 trainingConfig */
export const createJobRequestSchema = z.object({
  workloadId: z.number(),
  baseModelId: z.number(),
  datasetId: z.number(),
  trainingMode: trainingModeSchema,
  trainingPriority: z.optional(autoModeSchema),
  trainingConfig: z.optional(trainingConfigSchema),
  /** 결과 어댑터 이름(다운로드 파일 이름). 비워 보내면 서버가 기본값을 붙인다 — 백엔드 요청 §11 */
  adapterName: z.optional(adapterNameSchema),
  /** 이어서 학습할 완료 작업 — 그 작업의 어댑터에서 출발해 버전이 하나 올라간다(v1 → v2 → v3). null/생략이면 처음부터 */
  parentJobId: z.optional(z.nullable(z.number())),
  /** 어댑터를 얹고 시작하는가(이어서 학습 또는 .tar 업로드). true 면 서버는 adapter-upload 검증까지 끝나야 예치를 허용한다 */
  hasAdapter: z.optional(z.boolean()),
  /** 사용자가 고른 GPU 등급(gpu-options 의 id). 비우면 서버가 1순위를 쓴다.
   *  등급 안에 워커가 여럿이면 서버가 그중 순위가 가장 높은 한 대를 고른다 — 예치 때 체인이
   *  그 공급자 지갑을 잠그므로 한 대로 확정돼야 한다.
   *  보내는 건 선택뿐 — 예상 시간·비용·정산 지갑은 서버가 추천을 다시 받아 계산한다.
   *  그 등급에 지금 돌릴 수 있는 워커가 없으면 409 SELECTED_WORKER_UNAVAILABLE */
  gpuId: z.optional(z.nullable(z.number())),
})
export const createJobResponseSchema = z.object({
  jobId: z.number(),
  recommendedWorkerId: z.nullable(z.string()),
  predictedRuntimeSec: z.number(),
  estimatedCost: z.number(),
  schedulerModelVersion: z.nullable(z.string()),
  adapterName: z.optional(z.nullable(z.string())), // 서버가 확정한 이름·버전(백엔드 반영 전에는 안 온다)
  adapterVersion: z.optional(z.nullable(z.number())),
})

export const jobSummarySchema = z.object({
  jobId: z.number(),
  status: jobStatusSchema,
  workloadId: z.number(),
  progressPercent: z.nullable(z.number()),
  estimatedCost: z.number(),
  failReason: z.optional(z.nullable(z.string())), // 있으면 실패한 작업 — 상태와 조합해 "실패·환불 처리 중" 등으로 표시 (배포 전 서버 대비 optional)
  cancelReason: z.optional(z.nullable(z.string())), // 취소 원인 제공자(V22) — USER_REQUEST 가 아니면 "실패"로 표시 (배포 전 서버 대비 optional)
  // 어댑터 이름·버전 — 2단계 "이어서 학습" 목록이 목록 API 만으로 "이름 v2" 를 보여 줄 수 있게(백엔드 요청 §11, 반영 전 optional)
  adapterName: z.optional(z.nullable(z.string())),
  adapterVersion: z.optional(z.nullable(z.number())),
  createdAt: z.string(), // LocalDateTime "2026-09-09T10:00:00" (Z 없음)
})
export const jobPageSchema = z.object({
  content: z.array(jobSummarySchema),
  page: z.number(),
  totalPages: z.number(),
  totalElements: z.number(),
})
const hexAddress = z.string().check(z.regex(/^0x[0-9a-fA-F]{40}$/))
/** back JobDetailResponse(V7 이후). gpuId 는 없다 — 워커 1대 = GPU 1개라 assignedWorkerId 가 곧 GPU. 학습 설정 4값은 AUTO/CUSTOM 모두 항상 채워져 온다.
 *  예치 필드는 Escrow_Fund_계약_정리(2026-09-15) — CreateJobResponse 와 같은 값이 상세에도 실린다(재진입·새로고침 대비).
 *  백엔드 반영 전에도 화면이 죽지 않게 optional, priceWei 는 정밀도 때문에 문자열(Number 금지, BigInt 로만) */
export const jobDetailSchema = z.object({
  jobId: z.number(),
  externalRef: z.optional(z.nullable(z.string())),
  escrowJobId: z.optional(z.nullable(z.string())), // 온체인 번호 — 예치 전이면 null, FUNDED 취소에 사용
  // 예치 트랜잭션 해시. CREATED 인데 값이 있으면 "보낸 건 맞는데 서버가 영수증을 아직 못 읽은" 보류 상태다.
  // 예전에는 이 필드를 스키마에서 버려서 보류가 FundPanel 의 React state 에만 있었다 — 새로고침하면 예치 버튼이
  // 다시 나오고, 두 번째 서명은 컨트랙트가 ExternalRefAlreadyUsed 로 되돌려 실패 문구로 끝났다(수정요청서 FE-01)
  fundTxHash: z.optional(z.nullable(z.string())),
  providerWallet: z.optional(z.nullable(hexAddress)),
  priceWei: z.optional(z.nullable(z.string().check(z.regex(/^[0-9]+$/)))),
  clientWallet: z.optional(z.nullable(hexAddress)),
  chainId: z.optional(z.nullable(z.number())),
  tokenAddress: z.optional(z.nullable(hexAddress)),
  escrowAddress: z.optional(z.nullable(hexAddress)),
  // 예치가 끝난 Job 은 예치 당시 값이 따로 박힌다 — 예치 후 화면(취소·탐색기 링크)은 이쪽 우선
  escrowChainId: z.optional(z.nullable(z.number())),
  escrowContractAddress: z.optional(z.nullable(hexAddress)),
  datasetUploaded: z.optional(z.boolean()), // 원본 파일이 올라와 검증됐는가 — false 면 /fund 가 409 DATASET_NOT_UPLOADED 로 거절한다(예치 버튼을 열면 안 된다)
  hasAdapter: z.optional(z.boolean()), // 입력 어댑터를 얹는 작업인가(이어서 학습 또는 업로드)
  adapterUploaded: z.optional(z.boolean()), // 입력 어댑터 .tar 가 올라와 검증됐는가 — hasAdapter && !adapterUploaded 면 /fund 가 409 ADAPTER_NOT_UPLOADED
  status: jobStatusSchema,
  workloadId: z.number(),
  baseModelId: z.number(),
  datasetId: z.number(),
  trainingMode: trainingModeSchema,
  epochs: z.number(),
  batchSize: z.number(),
  learningRate: z.number(),
  maxLength: z.number(),
  predictedRuntimeSec: z.number(),
  actualRuntimeSec: z.nullable(z.number()),
  estimatedCost: z.number(),
  actualCost: z.nullable(z.number()),
  assignedWorkerId: z.nullable(z.string()),
  progressPercent: z.nullable(z.number()),
  progressEpoch: z.nullable(z.number()),
  progressTotalEpochs: z.nullable(z.number()),
  resultHash: z.nullable(z.string()),
  resultUrl: z.nullable(z.string()),
  // 학습 결과물(어댑터)의 이름·버전·계보 — 백엔드 요청 §11, 반영 전에도 화면이 죽지 않게 optional
  adapterName: z.optional(z.nullable(z.string())),
  adapterVersion: z.optional(z.nullable(z.number())),
  parentJobId: z.optional(z.nullable(z.number())),
  failReason: z.nullable(z.string()),
  cancelReason: z.optional(z.nullable(z.string())), // 취소 원인 제공자(V22) — 배포 전 서버 대비 optional
  createdAt: z.string(),
  startedAt: z.nullable(z.string()),
  finishedAt: z.nullable(z.string()),
})

/** POST /jobs/{id}/fund 응답 — 백엔드가 txHash 영수증을 검증해 확정. PENDING 은 실패가 아니라 "영수증 대기"(같은 txHash 로 재시도) */
export const fundResponseSchema = z.object({
  jobId: z.number(),
  status: z.enum(['FUNDED', 'PENDING']),
  txHash: z.string(),
  escrowJobId: z.nullable(z.string()),
})

/** POST /jobs/{id}/dataset-upload — Dataset 원본 업로드용 서명 URL 발급(Dataset_Upload_Handoff).
 *  objectKey·storageUri 는 프론트가 만들지 않는다 — URL 에 담긴 서명이 곧 인증 */
export const datasetUploadSchema = z.object({ uploadUrl: z.string().check(z.minLength(1)) })
/** POST /jobs/{id}/dataset-upload/complete — 업로드 검증 결과. verified=true 여야 다음 단계 진행 */
export const datasetUploadCompleteSchema = z.object({ verified: z.boolean() })

/** 입력 어댑터 .tar 업로드 — 데이터셋과 같은 모양(서명 URL 발급 → PUT → 검증). lora-adapter-frontend-작업 §2 */
export const adapterUploadSchema = z.object({ uploadUrl: z.string().check(z.minLength(1)) })
export const adapterUploadCompleteSchema = z.object({ verified: z.boolean() })

/** PATCH /jobs/{id}/adapter-name — 결과 어댑터 이름만 바꾼다(파일은 그대로). 입력 어댑터 업로드(/adapter-upload)와 다른 경로다 */
export const renameAdapterRequestSchema = z.object({ adapterName: adapterNameSchema })

// ── 학습 결과 검증(GET /jobs/{id}/result-verification) — Training_Result_Verification_Panel_Guide 계약.
//    백엔드 미구현(2026-09-22) → MSW stub 으로 계약 고정. 프론트는 safetensors 를 직접 읽지 않는다 ──
export const lossPointSchema = z.object({ step: z.number(), loss: z.number() })
export const resultVerificationSchema = z.object({
  available: z.boolean(), // false = 검증 데이터가 없는 legacy 작업(패널은 중립 빈 상태)
  reason: z.optional(z.nullable(z.string())),
  // 요약 숫자들은 nullable — 백엔드는 Runtime 이 준 키만 채우고 없는 값은 null 로 보낸다.
  // 필수로 두면 키 하나 빠진 정상 응답이 zod 에서 통째로 죽어 화면이 ERROR 가 된다(제3자 리뷰 차단 #1)
  training: z.optional(
    z.nullable(
      z.object({
        sampleCount: z.nullable(z.number()),
        totalSteps: z.nullable(z.number()),
        finalLoss: z.nullable(z.number()),
        actualRuntimeSec: z.nullable(z.number()),
        lossHistory: z.array(lossPointSchema), // 비어 있을 수 있다 — 차트 영역만 빈 상태, 패널은 유지
      }),
    ),
  ),
  adapter: z.optional(
    z.nullable(
      z.object({
        tensorCount: z.nullable(z.number()),
        changedTensorCount: z.nullable(z.number()),
        nonZeroTensorCount: z.nullable(z.number()),
        nanCount: z.nullable(z.number()),
        infCount: z.nullable(z.number()),
        valid: z.nullable(z.boolean()),
        /** layer 번호("0"…"27") → module(q_proj…) → 학습 전후 LoRA weight 변화량 */
        layerModuleDeltaMagnitude: z.record(z.string(), z.record(z.string(), z.number())),
      }),
    ),
  ),
  integrity: z.optional(z.nullable(z.object({ resultHash: z.string() }))),
})

/** SSE `GET /jobs/{id}/events` 가 흘려보내는 이벤트 (back `JobRealtimeEvent`).
 *  프레임은 `event: <type>` + `data: <json>` 인데, type 은 payload 안에도 있어 payload 만 읽는다.
 *  네트워크 경계이므로 여기서도 검증한다 — 모르는 형태면 그 프레임만 버리고 스트림은 유지한다. */
export const jobRealtimeEventSchema = z.object({
  type: z.enum(['JOB_PROGRESS', 'JOB_COMPLETED', 'JOB_FAILED', 'JOB_CANCELLED']),
  jobId: z.number(),
  status: z.optional(z.nullable(jobStatusSchema)),
  progress: z.optional(z.nullable(z.number())),
  currentStep: z.optional(z.nullable(z.number())),
  totalSteps: z.optional(z.nullable(z.number())),
  resultAvailable: z.optional(z.nullable(z.boolean())),
  reason: z.optional(z.nullable(z.string())),
  updatedAt: z.optional(z.nullable(z.string())),
})

/** 에스크로 컨트랙트가 말하는 Job 상태 5단계 (back `EscrowJobState`). NONE = 그 슬롯이 비어 있음 */
export const escrowStateSchema = z.enum(['NONE', 'FUNDED', 'ASSIGNED', 'COMPLETED', 'CANCELLED'])
/** 온체인 거래 종류 — 예치 / 워커 수락 / 결과 제출 / 취소 */
export const onchainTxTypeSchema = z.enum(['FUND', 'ACCEPT', 'SUBMIT', 'ABORT'])

/** GET /jobs/{jobId}/onchain — **DB 가 아니라 백엔드가 RPC 로 체인을 다시 읽은 값**.
 *  DB 는 온체인 위치(escrowJobId·컨트랙트 주소)만 주고, 상태·resultHash·영수증은 노드에서 재조회한다.
 *
 *  resultVerification 과 같은 교훈을 적용한다 — 키 하나 빠진 정상 응답에 zod 가 통째로 죽으면
 *  패널이 아니라 화면 전체가 ERROR 가 된다. 백엔드가 채울 때만 오는 값은 전부 optional·nullable. */
export const jobOnchainSchema = z.object({
  available: z.boolean(), // false = 아직 예치 전이거나 체인을 못 읽음 → reason 으로 구분
  reason: z.optional(z.nullable(z.string())),
  jobId: z.optional(z.nullable(z.number())),
  escrowJobId: z.optional(z.nullable(z.string())), // 문자열이다 — uint256 이라 number 로 좁히지 않는다
  chain: z.optional(
    z.nullable(z.object({ chainId: z.nullable(z.number()), contractAddress: z.nullable(z.string()) })),
  ),
  onchain: z.optional(
    z.nullable(
      z.object({
        state: z.nullable(escrowStateSchema),
        client: z.optional(z.nullable(z.string())),
        provider: z.optional(z.nullable(z.string())),
        operator: z.optional(z.nullable(z.string())),
        price: z.optional(z.nullable(z.string())), // wei 단위 문자열
        resultHash: z.optional(z.nullable(z.string())),
        deadline: z.optional(z.nullable(z.number())), // epoch seconds
      }),
    ),
  ),
  /** "우리 DB 가 아니라 체인에서 읽었다"를 화면이 스스로 말하게 하는 근거 */
  evidence: z.optional(
    z.nullable(
      z.object({
        latestBlockNumber: z.optional(z.nullable(z.number())),
        verifiedAt: z.optional(z.nullable(z.string())),
        source: z.optional(z.nullable(z.string())), // "BLOCKCHAIN_RPC"
      }),
    ),
  ),
  /** 영수증이 아직 없으면 status 가 비어 올 수 있다 */
  transactions: z.optional(
    z.nullable(
      z.array(
        z.object({
          type: z.nullable(z.string()),
          txHash: z.string(),
          blockNumber: z.optional(z.nullable(z.number())),
          status: z.optional(z.nullable(z.string())), // SUCCESS | REVERTED | null
        }),
      ),
    ),
  ),
})
