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
export const catalogSchema = z.object({
  workloads: z.array(workloadSchema),
  baseModels: z.array(baseModelSchema),
  gpus: z.array(gpuSchema),
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
})
export const createJobResponseSchema = z.object({
  jobId: z.number(),
  recommendedWorkerId: z.nullable(z.string()),
  predictedRuntimeSec: z.number(),
  estimatedCost: z.number(),
  schedulerModelVersion: z.nullable(z.string()),
})

export const jobSummarySchema = z.object({
  jobId: z.number(),
  status: jobStatusSchema,
  workloadId: z.number(),
  progressPercent: z.nullable(z.number()),
  estimatedCost: z.number(),
  failReason: z.optional(z.nullable(z.string())), // 있으면 실패한 작업 — 상태와 조합해 "실패·환불 처리 중" 등으로 표시 (배포 전 서버 대비 optional)
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
  failReason: z.nullable(z.string()),
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
