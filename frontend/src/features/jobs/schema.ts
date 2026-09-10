import * as z from 'zod/mini'

// 경로 상수는 여기(React 의존 없음) — mocks 가 api.ts 를 import 하면 auth store 가 서버 번들로 끌려감(wallet.md §3)
export const JOBS_PATH = '/jobs'
// 카탈로그·견적은 백엔드에 아직 없다(job.md §1-2 요청 스펙). 경로만 먼저 정해 두고 목업으로 개발
export const CATALOG_PATHS = { workloads: '/workloads', baseModels: '/base-models', gpus: '/gpus' } as const
/** 파일 업로드(제안) — 등록 마지막 단계에서만 호출. multipart: file, kind(dataset|adapter|model), metadata(JSON) */
export const UPLOADS_PATH = '/uploads'

/** back JobStatus 7단계 */
export const jobStatusSchema = z.enum(['CREATED', 'FUNDED', 'ASSIGNED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED'])
export const trainingModeSchema = z.enum(['AUTO', 'CUSTOM'])
/** AI /scheduler/training-config 의 mode — AUTO 에서 고르는 학습 방향 */
export const autoModeSchema = z.enum(['SPEED', 'BALANCED', 'TRAINING'])

const positiveInt = (name: string) =>
  z.number().check(z.refine(Number.isInteger, `${name}은(는) 정수여야 합니다.`), z.positive(`${name}은(는) 1 이상이어야 합니다.`))

/** 학습 설정 4값(AI TrainingConfig 와 동일). 상한 없음 — 실행 가능성은 서버가 판단 */
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

// ── 카탈로그(제안 스펙) ──────────────────────────────────────────
/** 워크로드 유형 — 학습(LoRA) / 추론 / 평가. 유형에 따라 2단계(학습 수준)·3단계(데이터셋 의미)가 달라진다 */
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
/** Job.gpuId 는 특정 워커가 아니라 GPU 등급 */
export const gpuSchema = z.object({ id: z.number(), model: z.string(), vramMb: z.number(), hourlyPrice: z.number() })
/** POST /jobs/gpu-options(제안) 응답 원소 — 메타데이터·모델·설정 기준으로 백엔드가 적합도를 매긴 GPU 등급 */
export const gpuFitSchema = z.enum(['RECOMMENDED', 'OK', 'TIGHT', 'INSUFFICIENT'])
export const gpuOptionSchema = z.object({ ...gpuSchema.shape, fit: gpuFitSchema, requiredVramMb: z.number(), reason: z.string(), availableWorkers: z.number() })
export const catalogSchema = z.object({
  workloads: z.array(workloadSchema),
  baseModels: z.array(baseModelSchema),
  gpus: z.array(gpuSchema),
})

// ── GPU 추천·견적(제안 스펙) — 파일 없이 메타데이터만 보낸다 ──────────
/** 워크로드·모델·설정·데이터셋 메타 — GPU 추천과 견적이 공통으로 받는 몸통 */
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
export const estimateSchema = z.object({
  trainingConfig: trainingConfigSchema,
  predictedRuntimeSec: z.number().check(z.positive()),
  estimatedCost: z.number().check(z.positive()),
})

// ── Job(back DTO 그대로) ─────────────────────────────────────────
/** POST /jobs 본문 — 견적 승인. predicted/estimated 는 견적 응답 값을 그대로. adapterId/modelId 는 백엔드 확장 요청(job.md §4) */
export const createJobRequestSchema = z.object({
  workloadId: z.number(),
  baseModelId: z.number(),
  datasetId: z.number(),
  gpuId: z.number(),
  trainingMode: trainingModeSchema,
  trainingConfig: z.optional(trainingConfigSchema), // CUSTOM 일 때만
  predictedRuntimeSec: z.number(),
  estimatedCost: z.number(),
  adapterId: z.optional(z.number()),
  modelId: z.optional(z.number()),
})
/** POST /uploads 응답(제안) */
export const uploadResponseSchema = z.object({ id: z.number(), kind: z.enum(['dataset', 'adapter', 'model']), name: z.string(), sizeBytes: z.number() })
export const createJobResponseSchema = z.object({ jobId: z.number() })

export const jobSummarySchema = z.object({
  jobId: z.number(),
  status: jobStatusSchema,
  workloadId: z.number(),
  progressPercent: z.nullable(z.number()),
  estimatedCost: z.number(),
  createdAt: z.string(), // LocalDateTime "2026-09-09T10:00:00" (Z 없음)
})
export const jobPageSchema = z.object({
  content: z.array(jobSummarySchema),
  page: z.number(),
  totalPages: z.number(),
  totalElements: z.number(),
})
export const jobDetailSchema = z.object({
  jobId: z.number(),
  status: jobStatusSchema,
  workloadId: z.number(),
  baseModelId: z.number(),
  gpuId: z.number(),
  trainingMode: trainingModeSchema,
  predictedRuntimeSec: z.number(),
  actualRuntimeSec: z.nullable(z.number()),
  estimatedCost: z.number(),
  actualCost: z.nullable(z.number()),
  progressPercent: z.nullable(z.number()),
  progressEpoch: z.nullable(z.number()),
  progressTotalEpochs: z.nullable(z.number()),
  resultHash: z.nullable(z.string()),
  resultUrl: z.nullable(z.string()),
  createdAt: z.string(),
  startedAt: z.nullable(z.string()),
  finishedAt: z.nullable(z.string()),
  // 데이터셋 파일명·분석 메타(3단계에서 업로드 때 보낸 5값). 백엔드 JobDetailResponse 에 아직 없어 optional — 오면 상세 [데이터셋] 표에 보인다
  datasetName: z.optional(z.nullable(z.string())),
  datasetMetadata: z.optional(z.nullable(datasetMetadataSchema)),
})
