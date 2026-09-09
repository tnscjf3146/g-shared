import { authFetch } from '@/features/auth/api'
import {
  CATALOG_PATHS,
  JOBS_PATH,
  UPLOADS_PATH,
  baseModelSchema,
  createJobResponseSchema,
  datasetSchema,
  estimateSchema,
  gpuOptionSchema,
  gpuSchema,
  jobDetailSchema,
  jobPageSchema,
  uploadResponseSchema,
  workloadSchema,
} from './schema'
import type { Catalog, CreateJobRequest, DatasetMetadata, Estimate, EstimateRequest, GpuOption, JobDetail, JobPage, JobSpec, JobStatus, UploadResponse } from './types'
import * as z from 'zod/mini'

const JSON_HEADERS = { 'Content-Type': 'application/json' }
const post = (path: string, body: unknown, init: RequestInit = {}) =>
  authFetch<unknown>(path, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body), ...init })

/** GET /jobs?status&page&size — 최신순(서버 고정) */
export async function getJobs(params: { status?: JobStatus; page?: number; size?: number } = {}): Promise<JobPage> {
  const q = new URLSearchParams()
  if (params.status) q.set('status', params.status)
  q.set('page', String(params.page ?? 0))
  q.set('size', String(params.size ?? 10))
  return jobPageSchema.parse(await authFetch<unknown>(`${JOBS_PATH}?${q}`))
}

/** GET /jobs/{id} — 본인 것만, 아니면 404 JOB_NOT_FOUND */
export async function getJob(jobId: number): Promise<JobDetail> {
  return jobDetailSchema.parse(await authFetch<unknown>(`${JOBS_PATH}/${jobId}`))
}

/** POST /jobs — 견적 승인 → 201 { jobId }. 상태는 CREATED(예치 대기) */
export async function createJob(body: CreateJobRequest): Promise<number> {
  return createJobResponseSchema.parse(await post(JOBS_PATH, body)).jobId
}

/** POST /jobs/{id}/cancel — CREATED/FUNDED/ASSIGNED 만, 아니면 409 INVALID_JOB_STATUS */
export function cancelJob(jobId: number): Promise<void> {
  return authFetch<void>(`${JOBS_PATH}/${jobId}/cancel`, { method: 'POST' })
}

/** POST /jobs/gpu-options(제안) — 메타데이터·모델·설정으로 GPU 등급별 적합도. 파일은 보내지 않는다 */
export async function getGpuOptions(spec: JobSpec): Promise<GpuOption[]> {
  return z.array(gpuOptionSchema).parse(await post(`${JOBS_PATH}/gpu-options`, spec))
}

/** POST /jobs/estimate(제안) — 설정 확정 + 예상 시간·비용. 백엔드가 AI /scheduler 를 감싼다 */
export async function estimateJob(body: EstimateRequest): Promise<Estimate> {
  return estimateSchema.parse(await post(`${JOBS_PATH}/estimate`, body))
}

/** POST /uploads(제안, multipart) — 등록 마지막 단계에서만. 데이터셋은 프론트가 분석한 메타데이터를 함께 보낸다 */
export async function uploadFile(kind: 'dataset' | 'adapter' | 'model', file: File, metadata?: DatasetMetadata): Promise<UploadResponse> {
  const form = new FormData()
  form.set('kind', kind)
  form.set('file', file, file.name)
  if (metadata) form.set('metadata', JSON.stringify(metadata))
  // Content-Type 은 브라우저가 boundary 와 함께 붙인다 → 헤더를 직접 넣지 않는다
  return uploadResponseSchema.parse(await authFetch<unknown>(UPLOADS_PATH, { method: 'POST', body: form }))
}

/** 카탈로그 4종(제안) 한 번에 */
export async function getCatalog(): Promise<Catalog> {
  const [workloads, baseModels, datasets, gpus] = await Promise.all([
    authFetch<unknown>(CATALOG_PATHS.workloads).then((r) => z.array(workloadSchema).parse(r)),
    authFetch<unknown>(CATALOG_PATHS.baseModels).then((r) => z.array(baseModelSchema).parse(r)),
    authFetch<unknown>(CATALOG_PATHS.datasets).then((r) => z.array(datasetSchema).parse(r)),
    authFetch<unknown>(CATALOG_PATHS.gpus).then((r) => z.array(gpuSchema).parse(r)),
  ])
  return { workloads, baseModels, datasets, gpus }
}
