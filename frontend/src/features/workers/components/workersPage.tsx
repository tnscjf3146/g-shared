'use client'

import { useCallback, useEffect, useState } from 'react'
import { getMyWorkers } from '../api'
import type { Worker } from '../types'
import { WorkerCard } from './workerCard'
import { WorkerRegisterCard } from './workerRegisterCard'
import { WorkerSkeleton } from './workerSkeleton'

// /workers 본문(worker.md §2): GET → [] 이면 등록 안내, 있으면 카드 목록. 등록은 앱이 하므로 웹은 폴링으로 결과만 본다
// 하트비트 30초·타임아웃 90초 → 15초 폴링이면 상태 변화가 늦어도 한 주기 안에 보인다. 탭이 숨겨져 있으면 건너뜀
const POLL_MS = 15_000

type State = { status: 'loading' } | { status: 'error' } | { status: 'ready'; workers: Worker[]; now: number }

export function WorkersPage() {
  const [state, setState] = useState<State>({ status: 'loading' })

  // 응답이 온 뒤에만 setState(effect 안 동기 setState 금지). 폴링 실패는 조용히 — 직전 목록을 유지한다
  const load = useCallback(
    (silent = false) =>
      getMyWorkers()
        .then((workers) => setState({ status: 'ready', workers, now: Date.now() }))
        .catch(() => {
          if (!silent) setState({ status: 'error' })
        }),
    [],
  )

  useEffect(() => {
    void load()
    const id = setInterval(() => {
      if (document.visibilityState === 'visible') void load(true)
    }, POLL_MS)
    return () => clearInterval(id)
  }, [load])

  if (state.status === 'loading') return <WorkerSkeleton />
  if (state.status === 'error') {
    return (
      <section className="card flex w-full max-w-[640px] flex-col items-start gap-3 p-10">
        <p className="text-[15px] text-dark">워커 목록을 불러오지 못했습니다.</p>
        <button
          type="button"
          onClick={() => {
            setState({ status: 'loading' })
            void load()
          }}
          className="text-[14px] text-primary hover:underline"
        >
          다시 시도 →
        </button>
      </section>
    )
  }
  if (state.workers.length === 0) return <WorkerRegisterCard />

  const { workers, now } = state
  return (
    <div className="flex w-full flex-col gap-8">
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        {workers.map((w, i) => (
          <WorkerCard
            key={w.workerId}
            worker={w}
            now={now}
            style={{ animationDelay: `${Math.min(i, 7) * 40}ms` }} // 스태거 등장(AGENTS.md), 8개부터는 동시
            onDeleted={() => setState({ status: 'ready', workers: workers.filter((x) => x.workerId !== w.workerId), now })}
          />
        ))}
      </div>
      {/* 네이티브 <details> — 접힘 상태 컴포넌트 없이. 다른 PC 등록 안내는 같은 카드 재사용 */}
      <details className="card-quiet group">
        <summary className="cursor-pointer text-[15px] font-semibold text-dark marker:text-text-muted">다른 PC 도 등록하기</summary>
        <div className="pt-4">
          <WorkerRegisterCard compact />
        </div>
      </details>
    </div>
  )
}
