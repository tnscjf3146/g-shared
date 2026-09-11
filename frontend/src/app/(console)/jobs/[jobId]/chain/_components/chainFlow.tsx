'use client'

import Link from 'next/link'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError } from '@/lib/fetcher'
import { getJob } from '@/features/jobs/api'
import { ACTIVE_STATUSES, STATUS_META } from '@/features/jobs/format'
import type { JobDetail, JobStatus } from '@/features/jobs/types'

// 블록체인 시각화 가이드 기반 Job Flow — 고정 2D 배치(다 펴놓기) + 별배경.
// 실제 Job 상태를 폴링해 현재 온체인 노드가 빛난다.
const POLL_MS = 5_000
const W = 1120
const H = 800

type Kind = 'wallet' | 'token' | 'state' | 'offchain' | 'keeper'
type NodeDef = { id: string; title: string; sub: string; kind: Kind; x: number; y: number }
// 정상 흐름 = 좌→하→우 완만한 호. 취소·환불/키퍼 = 우상단 구석 (빨간 선이 중앙을 안 가로지르게)
const NODES: NodeDef[] = [
  { id: 'clientWallet', title: '의뢰자 지갑', sub: 'Client Wallet', kind: 'wallet', x: 150, y: 400 },
  { id: 'gcoin', title: '지코인', sub: 'GCoin (GSC)', kind: 'token', x: 250, y: 170 },
  { id: 'funded', title: '예치 완료', sub: 'FUNDED', kind: 'state', x: 390, y: 250 },
  { id: 'assigned', title: '배정됨', sub: 'ASSIGNED', kind: 'state', x: 540, y: 370 },
  { id: 'exec', title: 'GPU 실행', sub: 'off-chain', kind: 'offchain', x: 600, y: 520 },
  { id: 'artifact', title: '결과물', sub: 'Result Artifact', kind: 'offchain', x: 480, y: 650 },
  { id: 'storage', title: '결과 저장소', sub: 'Result Storage', kind: 'offchain', x: 270, y: 700 },
  { id: 'hash', title: '결과 해시', sub: 'SHA-256', kind: 'offchain', x: 700, y: 700 },
  { id: 'completed', title: '완료', sub: 'COMPLETED', kind: 'state', x: 890, y: 620 },
  { id: 'providerWallet', title: '공급자 지갑', sub: 'Provider Wallet', kind: 'wallet', x: 980, y: 420 },
  { id: 'workerOp', title: '워커 노드', sub: 'Worker Operator', kind: 'wallet', x: 800, y: 300 },
  { id: 'keeper', title: '키퍼', sub: 'settleTimeout 스케줄러', kind: 'keeper', x: 1000, y: 140 },
  { id: 'cancelled', title: '취소·환불', sub: 'CANCELLED', kind: 'state', x: 728, y: 192 },
]
const NODE = Object.fromEntries(NODES.map((n) => [n.id, n])) as Record<string, NodeDef>

type EdgeDef = { from: string; to: string; label: string; dashed?: boolean; bad?: boolean; bow?: number }
const EDGES: EdgeDef[] = [
  { from: 'providerWallet', to: 'workerOp', label: '운영자 지정', dashed: true },
  { from: 'clientWallet', to: 'gcoin', label: '사용 승인' },
  { from: 'clientWallet', to: 'funded', label: '생성·예치' },
  { from: 'funded', to: 'assigned', label: '작업 수락' },
  { from: 'workerOp', to: 'assigned', label: '서명', dashed: true },
  { from: 'assigned', to: 'exec', label: '실행' },
  { from: 'exec', to: 'artifact', label: '' },
  { from: 'artifact', to: 'storage', label: '저장' },
  { from: 'artifact', to: 'hash', label: '해시 계산' },
  { from: 'hash', to: 'completed', label: '결과 제출' },
  { from: 'completed', to: 'providerWallet', label: '토큰 지급', dashed: true, bow: 50 },
  { from: 'funded', to: 'cancelled', label: '취소·만료', bad: true, bow: 34 },
  { from: 'assigned', to: 'cancelled', label: '중단·만료', bad: true, bow: 20 },
  { from: 'keeper', to: 'cancelled', label: '만료 정산', dashed: true, bad: true },
  { from: 'cancelled', to: 'clientWallet', label: '환불', bad: true, bow: 80 },
]

function view(status: JobStatus): { done: Set<string>; glow: Set<string>; bad: boolean } {
  const done = new Set<string>()
  const glow = new Set<string>()
  const add = (s: Set<string>, ...ids: string[]) => ids.forEach((i) => s.add(i))
  switch (status) {
    case 'CREATED': add(glow, 'clientWallet'); break
    case 'FUNDED': add(glow, 'funded'); break
    case 'ASSIGNED': add(done, 'funded'); add(glow, 'assigned'); break
    case 'RUNNING': add(done, 'funded', 'assigned'); add(glow, 'assigned', 'exec'); break
    case 'COMPLETED': add(done, 'funded', 'assigned', 'exec'); add(glow, 'completed'); break
    case 'FAILED':
    case 'CANCELLED': add(glow, 'cancelled'); return { done, glow, bad: true }
  }
  return { done, glow, bad: false }
}

const DOT: Record<Kind, string> = { wallet: '#a78bfa', token: '#30e2ad', keeper: '#f0a13a', state: '#8a8f98', offchain: '#5b6270' }
const RADIUS: Record<Kind, number> = { wallet: 12, token: 11, keeper: 11, state: 15, offchain: 10 }
const ACTIVE_GREEN = '#86e600'
const BAD_RED = '#ff5a5f'
const DONE_GREEN = '#76b900'

const GLOW_CSS = `
@keyframes jfGlow { 0%,100%{filter:drop-shadow(0 0 4px rgba(134,224,0,.7))} 50%{filter:drop-shadow(0 0 14px rgba(134,224,0,1))} }
@keyframes jfGlowBad { 0%,100%{filter:drop-shadow(0 0 4px rgba(255,90,95,.65))} 50%{filter:drop-shadow(0 0 13px rgba(255,90,95,1))} }
@keyframes jfHalo { 0%,100%{opacity:.16} 50%{opacity:.32} }
.jf-glow{animation:jfGlow 1.1s ease-in-out infinite}
.jf-glow-bad{animation:jfGlowBad 1.1s ease-in-out infinite}
.jf-halo{transform-box:fill-box;transform-origin:center;animation:jfHalo 1.1s ease-in-out infinite}
@media (prefers-reduced-motion: reduce){.jf-glow,.jf-glow-bad,.jf-halo{animation:none}}
`

// 결정적 별배경 (SSR/클라 동일 → 하이드레이션 안전)
function useStars(n: number) {
  return useMemo(() => {
    let s = 987654321
    const rnd = () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff }
    return Array.from({ length: n }, () => ({ x: rnd() * W, y: rnd() * H, r: rnd() * 1.1 + 0.3, o: rnd() * 0.55 + 0.15 }))
  }, [n])
}

type State = { s: 'loading' } | { s: 'notfound' } | { s: 'error' } | { s: 'ready'; job: JobDetail }

export function ChainFlow({ jobId }: { jobId: number }) {
  const [state, setState] = useState<State>({ s: 'loading' })

  const load = useCallback(
    async (silent = false) => {
      try {
        setState({ s: 'ready', job: await getJob(jobId) })
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) setState({ s: 'notfound' })
        else if (!silent) setState({ s: 'error' })
      }
    },
    [jobId],
  )

  const active = state.s === 'ready' && ACTIVE_STATUSES.has(state.job.status)
  useEffect(() => {
    void load()
  }, [load])
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') void load(true)
    }, POLL_MS)
    return () => clearInterval(t)
  }, [active, load])

  const status = state.s === 'ready' ? state.job.status : null

  return (
    <>
      <style>{GLOW_CSS}</style>
      <header className="flex flex-col">
        {/* [← 돌아가기] ─ 가운데 제목 ─ (우측 여백으로 균형). 제목·설명은 살짝 아래로 */}
        <div className="grid grid-cols-[1fr_auto_1fr] items-start">
          <Link href={`/jobs/${jobId}`} className="flex items-center gap-2 justify-self-start text-text-muted transition-colors hover:text-dark">
            <span className="text-[26px] leading-none">←</span>
            <span className="text-[15px]">돌아가기</span>
          </Link>
          <h1 className="mt-6 text-h1 font-bold tracking-heading text-dark">작업 #{jobId} 흐름</h1>
          <span aria-hidden />
        </div>
        <p className="mt-2 text-center text-[14px] text-text-muted">
          이 작업이 예치→수락→실행→정산까지 흐르는 관계. 현재 상태는 {POLL_MS / 1000}초마다 갱신돼 빛납니다.
        </p>
      </header>

      {state.s === 'loading' && <p className="text-[14px] text-text-muted">불러오는 중…</p>}
      {state.s === 'notfound' && (
        <section className="card flex flex-col items-start gap-3">
          <p className="text-[15px] text-dark">작업을 찾을 수 없습니다.</p>
          <Link href="/jobs" className="text-[14px] text-primary hover:underline">
            목록으로 →
          </Link>
        </section>
      )}
      {state.s === 'error' && (
        <section className="card flex flex-col items-start gap-3">
          <p className="text-[15px] text-dark">작업 정보를 불러오지 못했습니다.</p>
          <button type="button" onClick={() => { setState({ s: 'loading' }); void load() }} className="text-[14px] text-primary hover:underline">
            다시 시도 →
          </button>
        </section>
      )}
      {state.s === 'ready' && (
        <section className="flex flex-col gap-2">
          {/* 상태 — 그래프 바로 위, 오른쪽 끝을 그래프와 정렬. 실패·취소는 빨강, 그 외 메인 그린 */}
          <span className="self-end text-[15px] text-text-muted">
            상태{' '}
            <span className={`text-[19px] font-bold ${state.job.status === 'FAILED' || state.job.status === 'CANCELLED' ? 'text-danger' : 'text-primary'}`}>
              {STATUS_META[state.job.status].label}
            </span>
          </span>
          <Graph status={status} />
        </section>
      )}
    </>
  )
}

// 고정 배치 정적 렌더 — 물리·드래그 없음 (배치 확정: 2026-09-11)
function Graph({ status }: { status: JobStatus | null }) {
  const { done, glow, bad } = status ? view(status) : { done: new Set<string>(), glow: new Set<string>(), bad: false }
  const stars = useStars(150)

  return (
    <div className="relative w-full overflow-x-auto rounded-2xl" style={{ background: 'radial-gradient(130% 130% at 50% 0%, #12151b 0%, #0b0e14 72%)' }}>
      {/* 범례 — 캔버스 안 오버레이 (왼쪽아래: 노드 색 / 오른쪽아래: 선 종류) */}
      <DotLegend />
      <LineLegend />
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full min-w-[820px] select-none" role="img" aria-label="GPU Job 블록체인 흐름">
        {stars.map((s, i) => (
          <circle key={i} cx={s.x} cy={s.y} r={s.r} fill="#cdd6ff" opacity={s.o} />
        ))}

        {/* 간선 */}
        {EDGES.map((e, i) => {
          const p = NODE[e.from], q = NODE[e.to]
          return <line key={i} x1={p.x} y1={p.y} x2={q.x} y2={q.y} strokeLinecap="round" strokeWidth={e.dashed ? 1.2 : 1.6} strokeDasharray={e.dashed ? '4 6' : undefined} stroke={e.bad ? 'rgba(255,90,95,.32)' : 'rgba(255,255,255,.18)'} />
        })}

        {/* 노드 + 라벨 */}
        {NODES.map((n) => {
          const isGlow = glow.has(n.id), isDone = done.has(n.id), isBad = bad && isGlow
          const baseR = RADIUS[n.kind]
          const r = isGlow ? baseR + 4 : baseR
          const fill = isGlow ? (isBad ? BAD_RED : ACTIVE_GREEN) : isDone ? DONE_GREEN : DOT[n.kind]
          const labelFill = isGlow ? (isBad ? '#ffb4b6' : '#c9f27a') : 'rgba(255,255,255,.88)'
          return (
            <g key={n.id}>
              {isGlow && <circle className="jf-halo" cx={n.x} cy={n.y} r={r * 2.1} fill={isBad ? BAD_RED : ACTIVE_GREEN} />}
              <g className={isGlow ? (isBad ? 'jf-glow-bad' : 'jf-glow') : undefined}>
                <circle cx={n.x} cy={n.y} r={r} fill={fill} stroke="rgba(255,255,255,.16)" strokeWidth={1} />
                {n.kind === 'offchain' && <circle cx={n.x} cy={n.y} r={r} fill="none" stroke="rgba(255,255,255,.4)" strokeWidth={1} strokeDasharray="2 3" />}
                {isDone && !isGlow && <path d={`M${n.x - 4},${n.y} l3,3 l5,-6`} fill="none" stroke="#0b0e14" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" />}
              </g>
              <text x={n.x} y={n.y + r + 15} textAnchor="middle" fontSize="12.5" fontWeight={isGlow || isDone ? 700 : 600} fill={labelFill} stroke="#0b0e14" strokeWidth={3} paintOrder="stroke" style={{ strokeLinejoin: 'round' }}>
                {n.title}
              </text>
              {isGlow && (
                <text x={n.x} y={n.y + r + 29} textAnchor="middle" fontSize="10.5" fill="rgba(201,242,122,.85)" stroke="#0b0e14" strokeWidth={2.5} paintOrder="stroke" style={{ strokeLinejoin: 'round' }}>
                  {n.sub}
                </text>
              )}
            </g>
          )
        })}
      </svg>
    </div>
  )
}

// 왼쪽 아래 — 노드 색
function DotLegend() {
  const dots: [string, string][] = [
    ['현재 상태 (빛남)', ACTIVE_GREEN],
    ['지나온 상태 ✓', DONE_GREEN],
    ['지코인', DOT.token],
    ['지갑/역할', DOT.wallet],
    ['키퍼', DOT.keeper],
    ['오프체인', DOT.offchain],
  ]
  return (
    <ul className="pointer-events-none absolute bottom-3 left-4 z-10 flex max-w-[46%] flex-wrap gap-x-4 gap-y-1.5 text-[11.5px]" style={{ color: 'rgba(226,228,233,.75)' }}>
      {dots.map(([label, color]) => (
        <li key={label} className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: color }} />
          {label}
        </li>
      ))}
    </ul>
  )
}

// 오른쪽 아래 — 선 종류
function LineLegend() {
  const lines: [string, string, boolean][] = [
    ['정상 진행', 'rgba(255,255,255,.75)', false],
    ['위임·지급 등 보조 동작', 'rgba(255,255,255,.75)', true],
    ['취소·만료 → 환불', BAD_RED, false],
  ]
  return (
    <ul className="pointer-events-none absolute right-4 bottom-3 z-10 flex flex-col items-end gap-1.5 text-[11.5px]" style={{ color: 'rgba(226,228,233,.75)' }}>
      {lines.map(([label, color, dashed]) => (
        <li key={label} className="flex items-center gap-1.5">
          {label}
          <svg width="30" height="8" aria-hidden>
            <line x1="1" y1="4" x2="29" y2="4" stroke={color} strokeWidth="1.8" strokeLinecap="round" strokeDasharray={dashed ? '3 4' : undefined} />
          </svg>
        </li>
      ))}
    </ul>
  )
}
