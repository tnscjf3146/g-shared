import { delay, http, HttpResponse } from 'msw'
import { API_BASE_URL } from '@/lib/fetcher'
import { NETWORK_STATS_PATH, WORKERS_PATH } from '../schema'
import type { Worker } from '../types'
import { MOCK_WORKERS_INITIAL } from './data'

const URL = `${API_BASE_URL}${WORKERS_PATH}`
const error = (status: number, code: string, message: string) => HttpResponse.json({ code, message }, { status })

// 상태는 페이지 모듈 변수 — 새로고침하면 초기값으로. 등록은 워커 프로그램이 하므로 register 목업은 없다
let workers: Worker[] = [...MOCK_WORKERS_INITIAL]

// 살아있는 느낌: ONLINE/RUNNING 은 20초 전, OFFLINE 은 2시간 전 하트비트로 매 호출 갱신
const withFreshHeartbeat = (w: Worker): Worker => ({
  ...w,
  lastHeartbeatAt: new Date(Date.now() - (w.status === 'OFFLINE' ? 2 * 3600_000 : 20_000)).toISOString(),
})

export const workerHandlers = [
  // 네트워크 전체 워커 현황 — back NetworkController 와 같은 응답(로컬 목업용)
  http.get(`${API_BASE_URL}${NETWORK_STATS_PATH}`, async ({ request }) => {
    await delay()
    if (!request.headers.get('Authorization')) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    return HttpResponse.json({ onlineWorkers: 128, totalWorkers: 162 })
  }),

  http.get(`${URL}/me`, async ({ request }) => {
    await delay()
    if (!request.headers.get('Authorization')) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    return HttpResponse.json(workers.map(withFreshHeartbeat))
  }),

  http.delete(`${URL}/:workerId`, async ({ request, params }) => {
    await delay()
    if (!request.headers.get('Authorization')) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const target = workers.find((w) => w.workerId === params.workerId)
    if (!target) return error(404, 'WORKER_NOT_FOUND', 'Worker를 찾을 수 없습니다.')
    if (target.status === 'RUNNING' || target.currentJobId) return error(409, 'WORKER_BUSY', '작업 중인 Worker는 등록 해제할 수 없습니다. 작업을 먼저 취소해주세요.')
    workers = workers.filter((w) => w.workerId !== params.workerId)
    return new HttpResponse(null, { status: 204 })
  }),
]
