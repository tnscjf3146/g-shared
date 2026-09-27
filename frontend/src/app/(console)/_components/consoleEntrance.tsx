'use client'

import { useSyncExternalStore } from 'react'

// 콘솔 진입 전환 — 어두운 랜딩에서 클라이언트 이동으로 들어올 때만 검정 → 밝음(globals.css .console-brighten).
// 문서 자체가 콘솔 경로에서 로드됐다면(F5·주소 직접 진입) 모션 없음. 값이 변하지 않는 클라이언트 전용 판정이라
// effect 의 setState 대신 useSyncExternalStore(서버 false → 클라이언트 판정값)로 하이드레이션한다
const CONSOLE_PATH = /^\/(dashboard|jobs|workers|wallet|account)/
const subscribe = () => () => {}
const fromOutside = () => {
  const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
  return !!nav && !CONSOLE_PATH.test(new URL(nav.name).pathname)
}

export function ConsoleEntrance() {
  const show = useSyncExternalStore(subscribe, fromOutside, () => false)
  if (!show) return null
  return <div aria-hidden className="console-brighten pointer-events-none fixed inset-0 z-50" />
}
