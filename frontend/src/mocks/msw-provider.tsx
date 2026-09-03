'use client'

import { use } from 'react'

const MOCKING_ENABLED = process.env.NEXT_PUBLIC_API_MOCKING === 'enabled'

// 브라우저에서 모듈 로드 시 한 번만 생성 → 이중 start 없음
const workerReady =
  typeof window !== 'undefined' && MOCKING_ENABLED
    ? import('./browser').then(({ startWorker }) => startWorker())
    : null

/**
 * 서버: children 그대로 렌더 (SSR HTML 정상 출력)
 * 브라우저: worker.start() 완료까지 서스펜드 → 하이드레이션이 지연되는 동안 서버 HTML이 그대로 보이고,
 *          클라이언트 fetch는 워커 준비 뒤에만 시작된다
 */
export function MswProvider({ children }: { children: React.ReactNode }) {
  if (workerReady) use(workerReady)
  return children
}
