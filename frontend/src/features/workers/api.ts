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
