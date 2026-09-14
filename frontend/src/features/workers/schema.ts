import * as z from 'zod/mini'

// 경로 상수는 여기(React 의존 없음) — mocks/handlers 가 api.ts 를 import 하면 auth store 가 서버(instrumentation MSW) 번들로 끌려감(wallet.md §3)
export const WORKERS_PATH = '/workers'
// 네트워크 전체 워커 현황(대시보드 지표) — 백엔드 미구현, 계약 먼저 고정(MSW 목업이 응답). 구현되면 목업 핸들러만 지우면 된다
export const NETWORK_STATS_PATH = '/network/stats'

// 다운로드는 같은 오리진 /downloads/G-shared.exe(worker.md §8) — 프론트 이미지가 저장소 system/G-shared/G-shared.exe 를 public/downloads 로 담아 내려준다.
// 서버 컴포넌트(안내 카드)도 쓰므로 인증 스토어를 끌고 오는 api.ts 가 아니라 여기 → env 가 있으면 그 값, 없으면 서비스 경로
export const WORKER_DOWNLOAD_URL = process.env.NEXT_PUBLIC_WORKER_DOWNLOAD_URL || '/downloads/G-shared.exe'

/** back WorkerStatus: OFFLINE(미연결·하트비트 90초 초과) / ONLINE(대기) / RUNNING(작업 중) */
export const workerStatusSchema = z.enum(['OFFLINE', 'ONLINE', 'RUNNING'])

/** GET /workers/me 의 원소 — back Worker 엔티티 스냅샷. 스펙 필드는 워커 프로그램이 못 읽었으면 null */
export const workerSchema = z.object({
  workerId: z.string().check(z.minLength(1)),
  deviceName: z.string(),
  osVersion: z.nullable(z.string()),
  gpuModel: z.string(),
  vramMb: z.nullable(z.number()),
  driverVersion: z.nullable(z.string()),
  benchmarkTokensPerSec: z.nullable(z.number()),
  status: workerStatusSchema,
  currentJobId: z.nullable(z.string()),
  lastHeartbeatAt: z.nullable(z.string()),
  createdAt: z.string(),
  walletLinked: z.boolean(), // 주인 지갑 연결 여부. false 면 예치 때 넣을 공급자 주소가 없어 작업 후보에 오르지 못한다
})

export const workerListSchema = z.array(workerSchema)

/** GET /network/stats — 네트워크 전체 워커 현황(소비자 대시보드용). 계약 확정 2026-09-11, 백엔드 구현 전까지 MSW 목업 응답 */
export const networkStatsSchema = z.object({
  onlineWorkers: z.number(), // 지금 작업을 받을 수 있는(ONLINE·RUNNING) 워커 수
  totalWorkers: z.number(), // 등록된 전체 워커 수
})
