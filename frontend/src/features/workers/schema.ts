import * as z from 'zod/mini'

// 경로 상수는 여기(React 의존 없음) — mocks/handlers 가 api.ts 를 import 하면 auth store 가 서버(instrumentation MSW) 번들로 끌려감(wallet.md §3)
export const WORKERS_PATH = '/workers'
// 네트워크 전체 워커 현황(대시보드 지표) — back NetworkController(NetworkStatsResponse) 와 같은 모양
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
  operatorAddress: z.optional(z.nullable(z.string())), // 워커 프로그램의 온체인 서명 키(공개 주소). 승인 여부는 operator-status 로 따로 조회
  // 작업 이력 — 이 워커가 실제로 얼마나 끝까지 해냈나. 백엔드가 완료·실패를 세서 준다(V23).
  // 아직 이 필드를 안 주는 서버(구버전)가 있을 수 있으니 optional — 없으면 화면이 이력 칸만 감춘다.
  // successRate 는 **실행 0건이면 null** 이다. 0 과 구분해야 한다 — 신규 워커를 "성공률 0%"로 낙인찍지 않기 위해서(스펙 §6)
  completedCount: z.optional(z.number()),
  failedCount: z.optional(z.number()),
  totalExecutionCount: z.optional(z.number()),
  successRate: z.optional(z.nullable(z.number())),
})

export const workerListSchema = z.array(workerSchema)

/** back OperatorApproval — "아니오" 의 종류가 셋이라 안내가 다르다: 지갑 연결 / MetaMask 승인 / 잠시 후 다시 */
export const operatorApprovalSchema = z.enum(['APPROVED', 'NOT_APPROVED', 'PROVIDER_WALLET_MISSING', 'OPERATOR_MISSING', 'UNVERIFIED'])
/** GET /workers/{workerId}/operator-status — back OperatorStatusResponse(develop 2026-09-16). 공급자 지갑이 이 워커의 서명 키를 에스크로에 setOperator 로 승인했는가.
 *  approved = status === 'APPROVED'. 백엔드가 물을 때마다 체인 isOperator(provider, operator) 로 판정(저장 안 함). chainId·escrowAddress 는 체인 미설정이면 null */
export const operatorStatusSchema = z.object({
  workerId: z.string(),
  providerAddress: z.nullable(z.string()),
  operatorAddress: z.nullable(z.string()),
  approved: z.boolean(),
  status: operatorApprovalSchema,
  chainId: z.nullable(z.number()),
  escrowAddress: z.nullable(z.string()),
})

/** GET /network/stats — 네트워크 전체 워커 현황(소비자 대시보드용) */
export const networkStatsSchema = z.object({
  onlineWorkers: z.number(), // 지금 작업을 받을 수 있는(ONLINE·RUNNING) 워커 수
  totalWorkers: z.number(), // 등록된 전체 워커 수
})
