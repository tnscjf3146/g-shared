import * as z from 'zod/mini'

// 경로 상수는 여기(React 의존 없음) — mocks/handlers 가 api.ts 를 import 하면 auth store 가 서버(instrumentation MSW) 번들로 끌려감(wallet.md §3)
export const WORKERS_PATH = '/workers'

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
})

export const workerListSchema = z.array(workerSchema)
