import { authFetch } from '@/features/auth/api'
import { ApiError } from '@/lib/fetcher'
import {
  CATALOG_PATHS,
  DATASETS_PATH,
  JOBS_PATH,
  baseModelSchema,
  createJobResponseSchema,
  datasetUploadCompleteSchema,
  datasetUploadSchema,
  datasetSchema,
  estimateSchema,
  gpuOptionSchema,
  gpuSchema,
  fundResponseSchema,
  jobDetailSchema,
  jobPageSchema,
  workloadSchema,
} from './schema'
import type { Catalog, FundResponse, CreateDatasetRequest, CreateJobRequest, CreateJobResponse, Dataset, Estimate, EstimateRequest, GpuOption, JobDetail, JobPage, JobSpec, JobStatus } from './types'
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

/** POST /jobs — 서버가 Scheduler 로 GPU·견적을 다시 계산해 201. 409 NO_AVAILABLE_WORKER(기다리면 됨) / 422 NO_FEASIBLE_GPU(설정 바꿔야) 구분 */
export async function createJob(body: CreateJobRequest): Promise<CreateJobResponse> {
  return createJobResponseSchema.parse(await post(JOBS_PATH, body))
}

/** POST /jobs/{id}/cancel — CREATED/FUNDED/ASSIGNED 만, 아니면 409 INVALID_JOB_STATUS */
export function cancelJob(jobId: number): Promise<void> {
  return authFetch<void>(`${JOBS_PATH}/${jobId}/cancel`, { method: 'POST' })
}

/** POST /jobs/gpu-options — 메타데이터·모델·설정으로 GPU 등급별 적합도. 저장 안 함. 워커가 한 대도 없으면 409 NO_AVAILABLE_WORKER */
export async function getGpuOptions(spec: JobSpec): Promise<GpuOption[]> {
  return z.array(gpuOptionSchema).parse(await post(`${JOBS_PATH}/gpu-options`, spec))
}

/** POST /jobs/estimate — 설정 확정 + 예상 시간·비용. 저장 안 함 */
export async function estimateJob(body: EstimateRequest): Promise<Estimate> {
  return estimateSchema.parse(await post(`${JOBS_PATH}/estimate`, body))
}

/** POST /datasets — 분석 메타데이터 등록(원본 파일은 저장 위치 확정 전까지 안 보낸다) → 201 { datasetId, … } */
export async function createDataset(body: CreateDatasetRequest): Promise<Dataset> {
  return datasetSchema.parse(await post(DATASETS_PATH, body))
}

/** GET /datasets/{id} — 본인 것만, 아니면 404 DATASET_NOT_FOUND. 작업 상세 [데이터셋] 표에 쓴다 */
export async function getDataset(datasetId: number): Promise<Dataset> {
  return datasetSchema.parse(await authFetch<unknown>(`${DATASETS_PATH}/${datasetId}`))
}

/** 카탈로그 3종 한 번에. gpus 는 살아 있는 워커 기준이라 빈 배열일 수 있다 */
export async function getCatalog(): Promise<Catalog> {
  const [workloads, baseModels, gpus] = await Promise.all([
    authFetch<unknown>(CATALOG_PATHS.workloads).then((r) => z.array(workloadSchema).parse(r)),
    authFetch<unknown>(CATALOG_PATHS.baseModels).then((r) => z.array(baseModelSchema).parse(r)),
    authFetch<unknown>(CATALOG_PATHS.gpus).then((r) => z.array(gpuSchema).parse(r)),
  ])
  return { workloads, baseModels, gpus }
}

/** POST /jobs/{jobId}/fund { txHash } — 백엔드가 영수증을 직접 읽어 JobFunded 이벤트를 검증한 뒤 FUNDED 로.
 *  멱등: 같은 txHash 재전송 시 같은 답. PENDING 이면 잠시 후 같은 txHash 로 재시도(새 트랜잭션 금지) */
export async function fundJob(jobId: number, txHash: string): Promise<FundResponse> {
  return fundResponseSchema.parse(await authFetch<unknown>(`${JOBS_PATH}/${jobId}/fund`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ txHash }) }))
}

/** POST /jobs/{jobId}/dataset-upload → 서명 uploadUrl. Job 생성 직후, 원본 파일 업로드 전에 호출 */
export async function requestDatasetUpload(jobId: number): Promise<string> {
  const res = datasetUploadSchema.parse(await authFetch<unknown>(`${JOBS_PATH}/${jobId}/dataset-upload`, { method: 'POST' }))
  return res.uploadUrl
}

/** 서명 URL 에 원본 파일 PUT — 서명이 곧 인증이라 Authorization 없이 원본 fetch. 실패는 상태코드로만 판단 */
export async function uploadDatasetFile(uploadUrl: string, file: File): Promise<void> {
  const res = await fetch(uploadUrl, { method: 'PUT', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file })
  if (!res.ok) throw new ApiError(res.status, 'UPLOAD_FAILED', '데이터셋 업로드에 실패했습니다.')
}

/** POST /jobs/{jobId}/dataset-upload/complete → verified. false 면 Job 진행(예치)을 중단해야 한다 */
export async function completeDatasetUpload(jobId: number): Promise<boolean> {
  const res = datasetUploadCompleteSchema.parse(await authFetch<unknown>(`${JOBS_PATH}/${jobId}/dataset-upload/complete`, { method: 'POST' }))
  return res.verified
}
