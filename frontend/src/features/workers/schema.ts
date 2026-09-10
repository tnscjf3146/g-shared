import * as z from 'zod/mini'

// 경로 상수는 여기(React 의존 없음) — mocks/handlers 가 api.ts 를 import 하면 auth store 가 서버(instrumentation MSW) 번들로 끌려감(wallet.md §3)
export const WORKERS_PATH = '/workers'

// 다운로드는 시스템 담당이 exe 를 올리는 곳에 따라 바뀐다(worker.md §8). 서버 컴포넌트(안내 카드)도 쓰므로 인증 스토어를 끌고 오는 api.ts 가 아니라 여기 → env 하나, 없으면 저장소 raw 파일
export const WORKER_DOWNLOAD_URL =
  process.env.NEXT_PUBLIC_WORKER_DOWNLOAD_URL ??
  'https://lab.ssafy.com/s15-blochain-sub1/S15P21E204/-/raw/develop/system/G-shared/G-shared.exe?inline=false'

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
