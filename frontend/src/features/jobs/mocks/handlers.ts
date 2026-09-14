import { delay, http, HttpResponse } from 'msw'
import { API_BASE_URL } from '@/lib/fetcher'
import { CATALOG_PATHS, DATASETS_PATH, JOBS_PATH, createDatasetRequestSchema, createJobRequestSchema, datasetSchema, estimateRequestSchema, jobDetailSchema, jobSpecSchema, jobStatusSchema } from '../schema'
import type { AutoMode, Dataset, DatasetMetadata, GpuFit, JobDetail, JobStatus, TrainingConfig } from '../types'
import { getLinkedWallet } from '@/features/wallet/mocks/handlers'
import { MOCK_ADVANCE_MS, MOCK_CATALOG, MOCK_DATASETS, MOCK_JOBS } from './data'
import * as z from 'zod/mini'

const URL = `${API_BASE_URL}${JOBS_PATH}`
const error = (status: number, code: string, message: string) => HttpResponse.json({ code, message }, { status })
const authed = (request: Request) => !!request.headers.get('Authorization')
const nowLocal = () => new Date().toISOString().replace(/\.\d{3}Z$/, '') // back LocalDateTime 모양(Z 없음)

// 상태는 sessionStorage 에 보존 — 새로고침해도 만든 작업이 남고 자동 전이가 이어진다(탭을 닫으면 fixture 로).
// 워커 목업처럼 페이지 메모리에만 두면 새로고침마다 사라져 헷갈린다(2026-09-08 경험). 핸들러는 서버(instrumentation)에서도 로드되므로 storage 접근은 방어
const KEY = 'gshared.mock.jobs'
const DATASETS_KEY = 'gshared.mock.datasets'
function loadDatasets(): Dataset[] {
  try {
    const raw = typeof sessionStorage !== 'undefined' ? sessionStorage.getItem(DATASETS_KEY) : null
    if (raw) return z.array(datasetSchema).parse(JSON.parse(raw))
  } catch {
    /* 깨진 저장값은 버린다 */
  }
  return MOCK_DATASETS.map((d) => ({ ...d }))
}
let datasets: Dataset[] = loadDatasets()
let nextDatasetId = Math.max(4999, ...datasets.map((d) => d.datasetId)) + 1
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
  set(jobId, { status: 'ASSIGNED', assignedWorkerId: 'worker-11111111-1111-4111-8111-111111111111' })
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

  // ── 데이터셋 메타 등록·조회(back DatasetController) — 원본 파일은 받지 않는다 ──
  http.post(`${API_BASE_URL}${DATASETS_PATH}`, async ({ request }) => {
    await delay(400)
    if (!authed(request)) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const r = createDatasetRequestSchema.safeParse(await request.json().catch(() => null))
    if (!r.success) return error(400, 'INVALID_INPUT', r.error.issues[0]?.message ?? '요청이 올바르지 않습니다.')
    const d: Dataset = { datasetId: nextDatasetId++, ...r.data, createdAt: nowLocal() }
    datasets = [d, ...datasets]
    try {
      if (typeof sessionStorage !== 'undefined') sessionStorage.setItem(DATASETS_KEY, JSON.stringify(datasets))
    } catch {
      /* 메모리로만 */
    }
    return HttpResponse.json(d, { status: 201 })
  }),
  http.get(`${API_BASE_URL}${DATASETS_PATH}/:datasetId`, async ({ request, params }) => {
    await delay()
    if (!authed(request)) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const d = datasets.find((x) => x.datasetId === Number(params.datasetId))
    return d ? HttpResponse.json(d) : error(404, 'DATASET_NOT_FOUND', 'Dataset을 찾을 수 없습니다.')
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
    const r = createJobRequestSchema.safeParse(await request.json().catch(() => null))
    if (!r.success) return error(400, 'INVALID_INPUT', r.error.issues[0]?.message ?? '요청이 올바르지 않습니다.')
    const body = r.data
    if (!MOCK_CATALOG.workloads.some((w) => w.id === body.workloadId)) return error(404, 'WORKLOAD_NOT_FOUND', '지원하지 않는 Workload입니다.')
    if (!MOCK_CATALOG.baseModels.some((m) => m.id === body.baseModelId && m.workloadIds.includes(body.workloadId))) return error(404, 'BASE_MODEL_NOT_FOUND', '지원하지 않는 Base Model입니다.')
    const dataset = datasets.find((d) => d.datasetId === body.datasetId)
    if (!dataset) return error(404, 'DATASET_NOT_FOUND', 'Dataset을 찾을 수 없습니다.')
    // 서버가 Scheduler 로 확정하는 부분을 흉내: AUTO 는 방향(trainingPriority)으로, CUSTOM 은 4값 검증
    const cfg = body.trainingMode === 'AUTO' ? AUTO_CONFIG[body.trainingPriority ?? 'BALANCED'](dataset.maxSeqLength) : body.trainingConfig
    if (!cfg) return error(400, 'INVALID_TRAINING_CONFIG', 'trainingConfig가 올바르지 않습니다. (epochs·batchSize·learningRate·maxLength 4값 필수)')
    const est = estimate(dataset, 2, cfg) // 추천 GPU 는 4070 등급으로 고정
    const job: JobDetail = {
      jobId: nextId++, status: 'CREATED',
      workloadId: body.workloadId, baseModelId: body.baseModelId, datasetId: body.datasetId,
      trainingMode: body.trainingMode, epochs: cfg.epochs, batchSize: cfg.batchSize, learningRate: cfg.learningRate, maxLength: cfg.maxLength,
      predictedRuntimeSec: est.predictedRuntimeSec, estimatedCost: est.estimatedCost, actualRuntimeSec: null, actualCost: null, assignedWorkerId: null,
      progressPercent: null, progressEpoch: null, progressTotalEpochs: cfg.epochs, resultHash: null, resultUrl: null, failReason: null,
      createdAt: nowLocal(), startedAt: null, finishedAt: null,
    }
    jobs = [job, ...jobs]
    persist()
    resume(job)
    return HttpResponse.json({ jobId: job.jobId, recommendedWorkerId: 'worker-11111111-1111-4111-8111-111111111111', predictedRuntimeSec: est.predictedRuntimeSec, estimatedCost: est.estimatedCost, schedulerModelVersion: 'mock-1' }, { status: 201 })
  }),

  // 예치 조건 — 백엔드 미구현 stub(계약: schema.ts escrowTermsSchema). 구현되면 이 핸들러만 삭제.
  // priceWei 는 estimatedCost(TOKEN) × 10^18 문자열, 주소들은 고정 더미. escrowAddress 는 배포 전이라 env 우선
  http.get(`${URL}/:jobId/escrow-terms`, async ({ request, params }) => {
    await delay()
    if (!authed(request)) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const job = find(Number(params.jobId))
    if (!job) return error(404, 'JOB_NOT_FOUND', 'Job을 찾을 수 없습니다.')
    if (job.status !== 'CREATED' && job.status !== 'FUNDED') return error(409, 'INVALID_JOB_STATUS', '현재 상태에서 할 수 없는 작업입니다.') // CREATED=예치용, FUNDED=온체인 취소용
    // clientWallet 은 실제 연결된 지갑(지갑 목업 상태) — 진짜 MetaMask 계정으로 예치 검사를 통과할 수 있게
    const clientWallet = getLinkedWallet()
    if (!clientWallet) return error(409, 'WALLET_NOT_LINKED', '지갑이 연결되어 있지 않습니다. 지갑을 먼저 연결해 주세요.')
    return HttpResponse.json({
      jobId: job.jobId,
      escrowJobId: job.status === 'FUNDED' ? String(job.jobId) : null,
      externalRef: `0x${String(job.jobId).padStart(64, '0')}`,
      priceWei: `${job.estimatedCost}${'0'.repeat(18)}`,
      providerWallet: '0x1111111111111111111111111111111111111111',
      clientWallet,
      chainId: 31221,
      tokenAddress: '0xaCe3c977A539C16668528619F9417589f12C7B22',
      escrowAddress: process.env.NEXT_PUBLIC_ESCROW_ADDRESS ?? '0x3333333333333333333333333333333333333333',
    })
  }),

  // 예치 확인 — 현행 백엔드 규격 { escrowJobId }. 성공 시 FUNDED 로 전이
  http.post(`${URL}/:jobId/fund`, async ({ request, params }) => {
    await delay()
    if (!authed(request)) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const id = Number(params.jobId)
    const job = find(id)
    if (!job) return error(404, 'JOB_NOT_FOUND', 'Job을 찾을 수 없습니다.')
    if (job.status !== 'CREATED') return error(409, 'INVALID_JOB_STATUS', '현재 상태에서 할 수 없는 작업입니다.')
    const body = (await request.json().catch(() => null)) as { escrowJobId?: string } | null
    if (!body?.escrowJobId || !/^[0-9]{1,78}$/.test(body.escrowJobId)) return error(400, 'INVALID_INPUT', 'escrowJobId는 0 이상의 정수 문자열이어야 합니다.')
    set(id, { status: 'FUNDED' })
    resume(find(id)!)
    return new HttpResponse(null, { status: 200 })
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
