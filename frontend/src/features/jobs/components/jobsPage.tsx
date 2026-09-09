'use client'

import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { getCatalog, getJobs } from '../api'
import { ACTIVE_STATUSES } from '../format'
import type { JobPage, JobStatus, Workload } from '../types'
import { JobRow } from './jobRow'
import { JobListSkeleton } from './jobSkeleton'

// /jobs 본문(job.md §5). 서버 status 는 단일 값이라 "진행 중"(4상태)은 한 페이지(50건)를 받아 클라이언트에서 거른다 — 작업이 수십 건을 넘으면 status 파라미터로 전환
const PAGE_SIZE = 50
const POLL_MS = 15_000
type Filter = 'ALL' | 'ACTIVE' | 'COMPLETED' | 'ENDED'
const FILTERS: { key: Filter; label: string; match: (s: JobStatus) => boolean }[] = [
  { key: 'ALL', label: '전체', match: () => true },
  { key: 'ACTIVE', label: '진행 중', match: (s) => ACTIVE_STATUSES.has(s) },
  { key: 'COMPLETED', label: '완료', match: (s) => s === 'COMPLETED' },
  { key: 'ENDED', label: '실패·취소', match: (s) => s === 'FAILED' || s === 'CANCELLED' },
]

type State = { status: 'loading' } | { status: 'error' } | { status: 'ready'; page: JobPage }

export function JobsPage() {
  const [state, setState] = useState<State>({ status: 'loading' })
  const [pageNo, setPageNo] = useState(0)
  const [filter, setFilter] = useState<Filter>('ALL')
  const [workloads, setWorkloads] = useState<Workload[]>([])

  // 응답이 온 뒤에만 setState. 폴링 실패는 조용히(직전 목록 유지)
  const load = useCallback(
    (silent = false) =>
      getJobs({ page: pageNo, size: PAGE_SIZE })
        .then((page) => setState({ status: 'ready', page }))
        .catch(() => {
          if (!silent) setState({ status: 'error' })
        }),
    [pageNo],
  )

  useEffect(() => {
    void load()
    const id = setInterval(() => {
      if (document.visibilityState === 'visible') void load(true)
    }, POLL_MS)
    return () => clearInterval(id)
  }, [load])

  // workload id → 이름. 카탈로그 실패는 이름 대신 #id 로 보이기만 하므로 무시
  useEffect(() => {
    getCatalog()
      .then((c) => setWorkloads(c.workloads))
      .catch(() => {})
  }, [])

  if (state.status === 'loading') return <JobListSkeleton />
  if (state.status === 'error') {
    return (
      <section className="card flex w-full max-w-[640px] flex-col items-start gap-3 p-10">
        <p className="text-[15px] text-dark">작업 목록을 불러오지 못했습니다.</p>
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

  const { page } = state
  if (page.totalElements === 0) {
    return (
      <section className="card animate-enter flex w-full max-w-[640px] flex-col items-start gap-4 p-10">
        <h2 className="text-h2 font-bold tracking-heading text-dark">아직 작업이 없습니다</h2>
        <p className="text-[15px] text-text-muted">베이스 모델과 데이터셋을 고르고 견적을 확인하면 GPU 학습을 주문할 수 있습니다.</p>
        <Button href="/jobs/new" size="lg">
          첫 워크로드 등록
        </Button>
      </section>
    )
  }

  const rows = page.content.filter((j) => FILTERS.find((f) => f.key === filter)!.match(j.status))
  const nameOf = (id: number) => workloads.find((w) => w.id === id)?.name

  return (
    <div className="flex w-full flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* 필터 필 — pill 은 선택/상태 표시용(AGENTS.md) */}
        <div role="tablist" aria-label="상태 필터" className="flex gap-2">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              role="tab"
              aria-selected={filter === f.key}
              onClick={() => setFilter(f.key)}
              className={`h-9 rounded-pill border px-4 text-[13px] font-semibold transition-colors ${
                filter === f.key ? 'border-dark bg-dark text-white' : 'border-hairline-strong bg-surface-1 text-text-muted hover:text-dark'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <Button href="/jobs/new">+ 워크로드 등록</Button>
      </div>

      {rows.length === 0 ? (
        <p className="card-quiet py-8 text-center text-[14px] text-text-muted">이 조건에 맞는 작업이 없습니다.</p>
      ) : (
        <div className="flex flex-col gap-3">
          {rows.map((j, i) => (
            <JobRow key={j.jobId} job={j} workloadName={nameOf(j.workloadId)} style={{ animationDelay: `${Math.min(i, 7) * 40}ms` }} />
          ))}
        </div>
      )}

      {page.totalPages > 1 && (
        <div className="flex items-center justify-center gap-4 text-[14px] tabular-nums text-text-muted">
          <Button variant="secondary" size="sm" disabled={pageNo === 0} onClick={() => setPageNo((p) => p - 1)}>
            이전
          </Button>
          <span>
            {page.page + 1} / {page.totalPages}
          </span>
          <Button variant="secondary" size="sm" disabled={pageNo + 1 >= page.totalPages} onClick={() => setPageNo((p) => p + 1)}>
            다음
          </Button>
        </div>
      )}
    </div>
  )
}
