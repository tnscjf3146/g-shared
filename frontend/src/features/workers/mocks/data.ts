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
    walletLinked: true,
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
    walletLinked: false,
  },
])

/** 첫 화면에 보일 목록. 빈 상태(등록 안내 카드)를 보려면 [] 로. 목업 상태는 페이지 메모리라 새로고침하면 이 값으로 돌아간다 */
export const MOCK_WORKERS_INITIAL: Worker[] = MOCK_WORKERS
