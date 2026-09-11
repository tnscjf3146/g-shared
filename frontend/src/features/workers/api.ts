import { authFetch } from '@/features/auth/api'
import { NETWORK_STATS_PATH, WORKERS_PATH, networkStatsSchema, workerListSchema } from './schema'
import type { NetworkStats, Worker } from './types'

/** GET /workers/me → 내 워커 목록(없으면 []) */
export async function getMyWorkers(): Promise<Worker[]> {
  return workerListSchema.parse(await authFetch<unknown>(`${WORKERS_PATH}/me`))
}

/** GET /network/stats → 네트워크 전체 워커 현황. 백엔드 미구현 — 계약만 확정, MSW 목업이 응답(handlers.ts) */
export async function getNetworkStats(): Promise<NetworkStats> {
  return networkStatsSchema.parse(await authFetch<unknown>(NETWORK_STATS_PATH))
}

/** DELETE /workers/{id} → 204. 등록 해제 — 워커 프로그램은 다음 인증에서 WORKER_NOT_FOUND 를 받아 "다시 등록" 안내 */
export function deleteWorker(workerId: string): Promise<void> {
  return authFetch<void>(`${WORKERS_PATH}/${encodeURIComponent(workerId)}`, { method: 'DELETE' })
}
