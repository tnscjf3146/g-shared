import { delay, http, HttpResponse } from 'msw'
import { API_BASE_URL } from '@/lib/fetcher'
import { CATALOG_PATHS, JOBS_PATH, UPLOADS_PATH, datasetMetadataSchema, estimateRequestSchema, jobDetailSchema, jobSpecSchema, jobStatusSchema } from '../schema'
import type { AutoMode, DatasetMetadata, GpuFit, JobDetail, JobStatus, TrainingConfig } from '../types'
import { MOCK_ADVANCE_MS, MOCK_CATALOG, MOCK_JOBS } from './data'
import * as z from 'zod/mini'

const URL = `${API_BASE_URL}${JOBS_PATH}`
const error = (status: number, code: string, message: string) => HttpResponse.json({ code, message }, { status })
const authed = (request: Request) => !!request.headers.get('Authorization')
const nowLocal = () => new Date().toISOString().replace(/\.\d{3}Z$/, '') // back LocalDateTime 모양(Z 없음)

// 상태는 sessionStorage 에 보존 — 새로고침해도 만든 작업이 남고 자동 전이가 이어진다(탭을 닫으면 fixture 로).
// 워커 목업처럼 페이지 메모리에만 두면 새로고침마다 사라져 헷갈린다(2026-09-08 경험). 핸들러는 서버(instrumentation)에서도 로드되므로 storage 접근은 방어
const KEY = 'gshared.mock.jobs'
const datasetUploads = new Map<number, { name: string; metadata: DatasetMetadata }>() // 업로드 id → 파일명·메타(세션 한정)
const fresh = () => MOCK_JOBS.map((j) => ({ ...j }))
function loadJobs(): JobDetail[] {
  try {
    const raw = typeof sessionStorage !== 'undefined' ? sessionStorage.getItem(KEY) : null
    if (raw) return z.array(jobDetailSchema).parse(JSON.parse(raw))
  } catch {
    /* 깨진 저장값은 버린다 */
  }
  return fresh()
}
let jobs: JobDetail[] = loadJobs()
let nextId = Math.max(999, ...jobs.map((j) => j.jobId)) + 1
function persist() {
  try {
    if (typeof sessionStorage !== 'undefined') sessionStorage.setItem(KEY, JSON.stringify(jobs))
  } catch {
    /* 저장 불가 환경이면 메모리로만 */
  }
}
function set(jobId: number, patch: Partial<JobDetail>) {
  jobs = jobs.map((j) => (j.jobId === jobId ? { ...j, ...patch } : j))
  persist()
}
const find = (jobId: number) => jobs.find((j) => j.jobId === jobId)

// AUTO 방향 → 설정 (AI build_auto_config 를 단순화)
const AUTO_CONFIG: Record<AutoMode, (maxSeq: number) => TrainingConfig> = {
  SPEED: (m) => ({ epochs: 1, batchSize: 32, learningRate: 5e-5, maxLength: Math.min(128, m) }),
  BALANCED: (m) => ({ epochs: 3, batchSize: 16, learningRate: 3e-5, maxLength: Math.min(512, m) }),
  TRAINING: (m) => ({ epochs: 5, batchSize: 8, learningRate: 2e-5, maxLength: m }),
}

// 화면용 단순식: 샘플수 × epochs × 길이 계수 / GPU 계수. 정확할 필요 없음
function estimate(meta: DatasetMetadata, gpuId: number, cfg: TrainingConfig) {
  const gpu = MOCK_CATALOG.gpus.find((g) => g.id === gpuId)!
  const gpuFactor = gpu.hourlyPrice / 600 // 4070 기준 1.0
  const predictedRuntimeSec = Math.max(60, Math.round((meta.sampleCount * cfg.epochs * (Math.min(cfg.maxLength, meta.maxSeqLength) / 512)) / (12 * gpuFactor)))
  const estimatedCost = Math.ceil(predictedRuntimeSec / 3600) * gpu.hourlyPrice
  return { trainingConfig: cfg, predictedRuntimeSec, estimatedCost }
}

// GPU 적합도(화면용): 필요 VRAM ≈ 모델 가중치(fp16, params×2) + LoRA/옵티마이저 여유 + 활성값(batch×len). 여유 비율로 등급
const PARAMS_GB: Record<number, number> = { 11: 0.36, 12: 0.22, 21: 2.4, 22: 3.0 } // 모델 id → fp16 가중치 GB
function gpuOptions(spec: { baseModelId: number; datasetMetadata: DatasetMetadata; trainingMode: string; autoMode?: AutoMode; trainingConfig?: TrainingConfig; ownModel: boolean }) {
  const cfg = spec.trainingMode === 'AUTO' ? AUTO_CONFIG[spec.autoMode ?? 'BALANCED'](spec.datasetMetadata.maxSeqLength) : spec.trainingConfig!
  const weights = PARAMS_GB[spec.baseModelId] ?? 2
  const activations = (cfg.batchSize * Math.min(cfg.maxLength, spec.datasetMetadata.maxSeqLength)) / 4096 // GB
  const requiredVramMb = Math.round((weights * 1.6 + activations + 0.8) * 1024)
  return MOCK_CATALOG.gpus.map((g) => {
    const ratio = g.vramMb / requiredVramMb
    const fit: GpuFit = ratio >= 1.6 ? 'RECOMMENDED' : ratio >= 1.15 ? 'OK' : ratio >= 1 ? 'TIGHT' : 'INSUFFICIENT'
    const reason =
      fit === 'RECOMMENDED' ? '여유 있게 돌아갑니다. 배치를 키울 수 있어요.'
      : fit === 'OK' ? '문제없이 돌아갑니다.'
      : fit === 'TIGHT' ? 'VRAM 이 빡빡합니다. 최대 길이를 줄이는 걸 권합니다.'
      : '필요 VRAM 보다 작아 실행할 수 없습니다.'
    return { ...g, fit, requiredVramMb, reason, availableWorkers: g.id === 2 ? 3 : g.id === 1 ? 1 : 0 }
  })
}
let nextUploadId = 5000

// ── 목업 전용 자동 전이: CREATED → FUNDED → ASSIGNED → RUNNING(진행률) → COMPLETED. 단계별 함수라 새로고침 뒤 중간 상태에서 재개 가능 ──
const timers = new Map<number, ReturnType<typeof setTimeout>>()
function schedule(jobId: number, ms: number, fn: () => void) {
  clearTimeout(timers.get(jobId))
  timers.set(jobId, setTimeout(fn, ms))
}
function toFunded(jobId: number) {
  set(jobId, { status: 'FUNDED' })
  schedule(jobId, MOCK_ADVANCE_MS.toAssigned, () => toAssigned(jobId))
}
function toAssigned(jobId: number) {
  set(jobId, { status: 'ASSIGNED' })
  schedule(jobId, MOCK_ADVANCE_MS.toRunning, () => toRunning(jobId))
}
function toRunning(jobId: number) {
  const total = find(jobId)?.progressTotalEpochs ?? 3
  set(jobId, { status: 'RUNNING', startedAt: nowLocal(), progressPercent: 0, progressEpoch: 0, progressTotalEpochs: total })
  schedule(jobId, MOCK_ADVANCE_MS.progressTick, () => tick(jobId))
}
function tick(jobId: number) {
  const j = find(jobId)
  if (!j || j.status !== 'RUNNING') return
  const total = j.progressTotalEpochs ?? 3
  const pct = Math.min(100, (j.progressPercent ?? 0) + 10)
  if (pct < 100) {
    set(jobId, { progressPercent: pct, progressEpoch: Math.max(1, Math.ceil((pct / 100) * total)) })
    schedule(jobId, MOCK_ADVANCE_MS.progressTick, () => tick(jobId))
    return
  }
  set(jobId, {
    status: 'COMPLETED', progressPercent: 100, progressEpoch: total, finishedAt: nowLocal(),
    actualRuntimeSec: Math.round(j.predictedRuntimeSec * 0.92), actualCost: j.estimatedCost,
    resultHash: '0x' + jobId.toString(16).padStart(64, 'a'), resultUrl: `https://example.com/results/job-${jobId}.tar.gz`,
  })
  timers.delete(jobId)
}
/** 현재 상태에서 다음 단계 예약(생성 직후·새로고침 뒤 재개 공용) */
function resume(j: JobDetail) {
  const next: Partial<Record<JobStatus, [number, () => void]>> = {
    CREATED: [MOCK_ADVANCE_MS.toFunded, () => toFunded(j.jobId)],
    FUNDED: [MOCK_ADVANCE_MS.toAssigned, () => toAssigned(j.jobId)],
    ASSIGNED: [MOCK_ADVANCE_MS.toRunning, () => toRunning(j.jobId)],
    RUNNING: [MOCK_ADVANCE_MS.progressTick, () => tick(j.jobId)],
  }
  const step = next[j.status]
  if (step) schedule(j.jobId, step[0], step[1])
}
// 사용자가 만든 작업(id ≥ 1000)만 자동 전이. fixture 는 고정
if (typeof window !== 'undefined') jobs.filter((j) => j.jobId >= 1000).forEach(resume)

const toSummary = (j: JobDetail) => ({ jobId: j.jobId, status: j.status, workloadId: j.workloadId, progressPercent: j.progressPercent, estimatedCost: j.estimatedCost, createdAt: j.createdAt })
const catalogGet = (path: string, rows: unknown[]) =>
  http.get(`${API_BASE_URL}${path}`, async ({ request }) => {
    await delay()
    return authed(request) ? HttpResponse.json(rows) : error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
  })

export const jobHandlers = [
  // ── 카탈로그(제안 스펙, job.md §1-2) ──
  catalogGet(CATALOG_PATHS.workloads, MOCK_CATALOG.workloads),
  catalogGet(CATALOG_PATHS.baseModels, MOCK_CATALOG.baseModels),
  catalogGet(CATALOG_PATHS.gpus, MOCK_CATALOG.gpus),

  // ── 견적(제안) ──
  http.post(`${URL}/estimate`, async ({ request }) => {
    await delay()
    if (!authed(request)) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const r = estimateRequestSchema.safeParse(await request.json().catch(() => null))
    if (!r.success) return error(400, 'INVALID_INPUT', r.error.issues[0]?.message ?? '요청이 올바르지 않습니다.')
    const { datasetMetadata, gpuId, trainingMode, autoMode, trainingConfig } = r.data
    if (!MOCK_CATALOG.gpus.some((g) => g.id === gpuId)) return error(400, 'INVALID_INPUT', '선택한 GPU 등급을 찾을 수 없습니다.')
    const cfg = trainingMode === 'AUTO' ? AUTO_CONFIG[autoMode ?? 'BALANCED'](datasetMetadata.maxSeqLength) : trainingConfig
    if (!cfg) return error(400, 'INVALID_INPUT', 'trainingConfig는 필수값입니다.')
    return HttpResponse.json(estimate(datasetMetadata, gpuId, cfg))
  }),

  // ── GPU 추천(제안) — 메타데이터만 받는다 ──
  http.post(`${URL}/gpu-options`, async ({ request }) => {
    await delay()
    if (!authed(request)) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const r = jobSpecSchema.safeParse(await request.json().catch(() => null))
    if (!r.success) return error(400, 'INVALID_INPUT', r.error.issues[0]?.message ?? '요청이 올바르지 않습니다.')
    return HttpResponse.json(gpuOptions(r.data))
  }),

  // ── 업로드(제안, multipart) — 마지막 단계에서만 ──
  http.post(`${API_BASE_URL}${UPLOADS_PATH}`, async ({ request }) => {
    await delay(600)
    if (!authed(request)) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const form = await request.formData().catch(() => null)
    const file = form?.get('file')
    const kind = form?.get('kind')
    if (!(file instanceof File) || !['dataset', 'adapter', 'model'].includes(String(kind))) return error(400, 'INVALID_INPUT', 'file 과 kind 는 필수값입니다.')
    const id = nextUploadId++
    if (kind === 'dataset') {
      const meta = datasetMetadataSchema.safeParse(JSON.parse(String(form?.get('metadata') ?? 'null')))
      if (!meta.success) return error(400, 'INVALID_INPUT', '데이터셋 metadata 가 올바르지 않습니다.')
      datasetUploads.set(id, { name: file.name, metadata: meta.data }) // 작업 생성 때 상세에 붙인다
    }
    return HttpResponse.json({ id, kind, name: file.name, sizeBytes: file.size }, { status: 201 })
  }),

  // ── Job(back 스펙) ──
  http.get(URL, async ({ request }) => {
    await delay()
    if (!authed(request)) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const u = new globalThis.URL(request.url)
    const statusParam = u.searchParams.get('status')
    const status = statusParam ? jobStatusSchema.safeParse(statusParam) : null
    const page = Number(u.searchParams.get('page') ?? 0)
    const size = Number(u.searchParams.get('size') ?? 10)
    const all = [...jobs].sort((a, b) => b.jobId - a.jobId).filter((j) => !status || (status.success && j.status === status.data))
    const content = all.slice(page * size, page * size + size).map(toSummary)
    return HttpResponse.json({ content, page, totalPages: Math.max(1, Math.ceil(all.length / size)), totalElements: all.length })
  }),

  http.get(`${URL}/:jobId`, async ({ request, params }) => {
    await delay()
    if (!authed(request)) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const job = find(Number(params.jobId))
    return job ? HttpResponse.json(job) : error(404, 'JOB_NOT_FOUND', 'Job을 찾을 수 없습니다.')
  }),

  http.post(URL, async ({ request }) => {
    await delay()
    if (!authed(request)) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const body = (await request.json().catch(() => null)) as Record<string, unknown> | null
    for (const k of ['workloadId', 'baseModelId', 'datasetId', 'gpuId', 'predictedRuntimeSec', 'estimatedCost']) {
      if (typeof body?.[k] !== 'number') return error(400, 'INVALID_INPUT', `${k}는 필수값입니다.`)
    }
    const mode = body!.trainingMode
    if (mode !== 'AUTO' && mode !== 'CUSTOM') return error(400, 'INVALID_INPUT', 'trainingMode는 AUTO 또는 CUSTOM이어야 합니다.')
    const cfg = body!.trainingConfig as TrainingConfig | undefined
    const job: JobDetail = {
      jobId: nextId++, status: 'CREATED',
      workloadId: body!.workloadId as number, baseModelId: body!.baseModelId as number, gpuId: body!.gpuId as number,
      trainingMode: mode, predictedRuntimeSec: body!.predictedRuntimeSec as number, estimatedCost: body!.estimatedCost as number,
      actualRuntimeSec: null, actualCost: null, progressPercent: null, progressEpoch: null, progressTotalEpochs: cfg?.epochs ?? 3,
      resultHash: null, resultUrl: null, createdAt: nowLocal(), startedAt: null, finishedAt: null,
      datasetName: datasetUploads.get(body!.datasetId as number)?.name ?? null,
      datasetMetadata: datasetUploads.get(body!.datasetId as number)?.metadata ?? null,
    }
    jobs = [job, ...jobs]
    persist()
    resume(job)
    return HttpResponse.json({ jobId: job.jobId }, { status: 201 })
  }),

  http.post(`${URL}/:jobId/cancel`, async ({ request, params }) => {
    await delay()
    if (!authed(request)) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const id = Number(params.jobId)
    const job = find(id)
    if (!job) return error(404, 'JOB_NOT_FOUND', 'Job을 찾을 수 없습니다.')
    if (!['CREATED', 'FUNDED', 'ASSIGNED'].includes(job.status)) return error(409, 'INVALID_JOB_STATUS', '현재 상태에서 할 수 없는 작업입니다.')
    clearTimeout(timers.get(id))
    timers.delete(id)
    set(id, { status: 'CANCELLED', finishedAt: nowLocal() })
    return new HttpResponse(null, { status: 200 })
  }),
]
