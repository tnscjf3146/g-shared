import { workerListSchema } from '../schema'
import type { Worker } from '../types'

// 고정 fixture(schema 통과값만). 시각은 핸들러가 호출 시점 기준으로 다시 채운다(ONLINE 은 20초 전, OFFLINE 은 2시간 전)
export const MOCK_WORKERS: Worker[] = workerListSchema.parse([
  {
    workerId: 'worker-11111111-1111-4111-8111-111111111111',
    deviceName: 'DESKTOP-4070',
    osVersion: 'Windows 11 10.0.26200',
    gpuModel: 'NVIDIA GeForce RTX 4070',
    vramMb: 12282,
    driverVersion: '560.94',
    benchmarkTokensPerSec: 1834.5,
    status: 'ONLINE',
    currentJobId: null,
    lastHeartbeatAt: '2026-09-08T00:00:00Z',
    createdAt: '2026-09-01T09:00:00Z',
    walletLinked: true, // 목록 응답에서는 지갑 목업의 연결 상태로 덮인다(handlers.ts)
    operatorAddress: '0x1111111111111111111111111111111111111111', // 미승인 → 지갑 연결 뒤 '실행 권한: 승인 필요' + [GPU 실행 권한 승인](서명은 MetaMask 필요)
  },
  {
    workerId: 'worker-33333333-3333-4333-8333-333333333333',
    deviceName: 'STUDIO-4090',
    osVersion: 'Windows 11 10.0.26100',
    gpuModel: 'NVIDIA GeForce RTX 4090',
    vramMb: 24564,
    driverVersion: '560.94',
    benchmarkTokensPerSec: 4210,
    status: 'ONLINE',
    currentJobId: null,
    lastHeartbeatAt: '2026-09-08T00:00:00Z',
    createdAt: '2026-09-05T09:00:00Z',
    walletLinked: true, // 목록 응답에서는 지갑 목업의 연결 상태로 덮인다(handlers.ts)
    operatorAddress: '0x2222222222222222222222222222222222222222', // 승인 완료(MOCK_OPERATOR_APPROVED) → '실행 권한: 승인 완료 · 상태: 작업 가능'
  },
  {
    workerId: 'worker-22222222-2222-4222-8222-222222222222',
    deviceName: 'HOME-PC',
    osVersion: 'Windows 10 10.0.19045',
    gpuModel: 'NVIDIA GeForce RTX 3060',
    vramMb: 8192,
    driverVersion: '552.22',
    benchmarkTokensPerSec: null,
    status: 'OFFLINE',
    currentJobId: null,
    lastHeartbeatAt: '2026-09-08T00:00:00Z',
    createdAt: '2026-08-20T13:30:00Z',
    walletLinked: false, // 목록 응답에서는 지갑 목업의 연결 상태로 덮인다(handlers.ts)
    operatorAddress: null, // 옛 버전으로 등록 — 키 미등록
  },
])

/** operator-status 목업에서 approved=true 로 답할 워커(실서버는 체인 isOperator 로 판정) */
export const MOCK_OPERATOR_APPROVED = new Set(['worker-33333333-3333-4333-8333-333333333333'])

/** 첫 화면에 보일 목록. 빈 상태(등록 안내 카드)를 보려면 [] 로. 목업 상태는 페이지 메모리라 새로고침하면 이 값으로 돌아간다 */
export const MOCK_WORKERS_INITIAL: Worker[] = MOCK_WORKERS
