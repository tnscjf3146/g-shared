import { authFetch } from '@/features/auth/api'
import { WORKERS_PATH, workerListSchema } from './schema'
import type { Worker } from './types'

/** GET /workers/me → 내 워커 목록(없으면 []) */
export async function getMyWorkers(): Promise<Worker[]> {
  return workerListSchema.parse(await authFetch<unknown>(`${WORKERS_PATH}/me`))
}

/** DELETE /workers/{id} → 204. 등록 해제 — 워커 프로그램은 다음 인증에서 WORKER_NOT_FOUND 를 받아 "다시 등록" 안내 */
export function deleteWorker(workerId: string): Promise<void> {
  return authFetch<void>(`${WORKERS_PATH}/${encodeURIComponent(workerId)}`, { method: 'DELETE' })
}

// 다운로드는 시스템 담당이 exe 를 올리는 곳에 따라 바뀐다(worker.md §8) → env 하나, 없으면 저장소 raw 파일
export const WORKER_DOWNLOAD_URL =
  process.env.NEXT_PUBLIC_WORKER_DOWNLOAD_URL ??
  'https://lab.ssafy.com/s15-blochain-sub1/S15P21E204/-/raw/develop/system/G-shared/G-shared.exe?inline=false'
