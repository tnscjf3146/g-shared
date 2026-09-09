'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/statusBadge'
import { ApiError } from '@/lib/fetcher'
import { cancelJob, getCatalog, getJob } from '../api'
import { jobMessageFor } from '../errors'
import { ACTIVE_STATUSES, CANCELLABLE_STATUSES, STATUS_META, formatCost, formatDateTime, formatDuration } from '../format'
import type { Catalog, JobDetail } from '../types'
import { JobProgress } from './jobProgress'
import { JobDetailSkeleton } from './jobSkeleton'

// /jobs/[id] 본문(job.md §5). 진행 중 상태면 10초 폴링, 끝나면 중단. 이름(workload·모델·GPU)은 카탈로그로 id→이름
const POLL_MS = 10_000
type State = { status: 'loading' } | { status: 'notfound' } | { status: 'error' } | { status: 'ready'; job: JobDetail }

export function JobDetailPage({ jobId }: { jobId: number }) {
  const [state, setState] = useState<State>({ status: 'loading' })
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    (silent = false) =>
      getJob(jobId)
        .then((job) => setState({ status: 'ready', job }))
        .catch((e) => {
          if (e instanceof ApiError && e.status === 404) setState({ status: 'notfound' })
          else if (!silent) setState({ status: 'error' })
        }),
    [jobId],
  )

  const active = state.status === 'ready' && ACTIVE_STATUSES.has(state.job.status)
  useEffect(() => {
    void load()
  }, [load])
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => {
      if (document.visibilityState === 'visible') void load(true)
    }, POLL_MS)
    return () => clearInterval(id)
  }, [active, load])
  useEffect(() => {
    getCatalog()
      .then(setCatalog)
      .catch(() => {})
  }, [])

  if (state.status === 'loading') return <JobDetailSkeleton />
  if (state.status === 'notfound') {
    return (
      <section className="card flex w-full max-w-[640px] flex-col items-start gap-3 p-10">
        <p className="text-[15px] text-dark">작업을 찾을 수 없습니다.</p>
        <Link href="/jobs" className="text-[14px] text-primary hover:underline">
          목록으로 →
        </Link>
      </section>
    )
  }
  if (state.status === 'error') {
    return (
      <section className="card flex w-full max-w-[640px] flex-col items-start gap-3 p-10">
        <p className="text-[15px] text-dark">작업 정보를 불러오지 못했습니다.</p>
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

  const { job } = state
  const meta = STATUS_META[job.status]
  const name = {
    workload: catalog?.workloads.find((w) => w.id === job.workloadId)?.name ?? `#${job.workloadId}`,
    model: catalog?.baseModels.find((m) => m.id === job.baseModelId)?.name ?? `#${job.baseModelId}`,
    gpu: catalog?.gpus.find((g) => g.id === job.gpuId)?.model ?? `#${job.gpuId}`,
  }

  const cancel = async () => {
    if (!confirm(`작업 #${job.jobId} 을(를) 취소할까요?`)) return
    setPending(true)
    setError(null)
    try {
      await cancelJob(job.jobId)
      await load()
    } catch (e) {
      setError(jobMessageFor(e))
      if (e instanceof ApiError && e.status === 409) void load(true) // 상태가 이미 바뀜 → 최신으로
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link href="/jobs" className="text-[14px] text-text-muted hover:text-dark">
            ← 내 작업
          </Link>
          <h1 className="text-h1 font-bold tracking-heading text-dark">작업 #{job.jobId}</h1>
          <StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
        </div>
        {CANCELLABLE_STATUSES.has(job.status) && (
          <Button variant="secondary" size="sm" disabled={pending} onClick={cancel}>
            {pending ? '취소 중…' : '작업 취소'}
          </Button>
        )}
      </header>
      {error && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-[14px] text-danger">
          {error}
        </p>
      )}

      {/* 진행 카드 — 상태별 한 문장 + RUNNING 이면 진행률 */}
      <section className="card animate-enter flex flex-col gap-4">
        <h2 className="text-[15px] font-semibold text-dark">진행</h2>
        {job.status === 'CREATED' && <p className="text-[14px] text-text-muted">예치 대기 — 지갑에서 예치하면 워커 배정이 시작됩니다. (예치 기능은 준비 중)</p>}
        {job.status === 'FUNDED' && <p className="text-[14px] text-text-muted">예치 확인됨. 조건에 맞는 워커에 배정되는 중입니다.</p>}
        {job.status === 'ASSIGNED' && <p className="text-[14px] text-text-muted">워커에 배정됐습니다. 곧 학습이 시작됩니다.</p>}
        {job.status === 'RUNNING' && (
          <>
            <JobProgress job={job} />
            <p className="text-[13px] tabular-nums text-text-muted">
              시작 {job.startedAt ? formatDateTime(job.startedAt) : '—'} · 예상 소요 {formatDuration(job.predictedRuntimeSec)}
            </p>
          </>
        )}
        {job.status === 'COMPLETED' && <p className="text-[14px] text-text-muted">학습이 끝났습니다. 아래에서 결과를 받을 수 있습니다.</p>}
        {job.status === 'FAILED' && <p className="text-[14px] text-danger">학습이 실패했습니다. (실패 사유 표시는 백엔드 응답 추가 후)</p>}
        {job.status === 'CANCELLED' && <p className="text-[14px] text-text-muted">취소된 작업입니다.</p>}
      </section>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <section className="card animate-enter flex flex-col gap-3" style={{ animationDelay: '40ms' }}>
          <h2 className="text-[15px] font-semibold text-dark">설정</h2>
          <Rows rows={[['작업 종류', name.workload], ['베이스 모델', name.model], ['GPU 등급', name.gpu], ['학습 설정', job.trainingMode === 'AUTO' ? '자동' : '직접 설정']]} />
        </section>
        <section className="card animate-enter flex flex-col gap-3" style={{ animationDelay: '80ms' }}>
          <h2 className="text-[15px] font-semibold text-dark">비용 · 시간</h2>
          <Rows
            rows={[
              ['예상 비용', formatCost(job.estimatedCost)],
              ['실제 비용', job.actualCost != null ? formatCost(job.actualCost) : '—'],
              ['예상 소요', formatDuration(job.predictedRuntimeSec)],
              ['실제 소요', job.actualRuntimeSec != null ? formatDuration(job.actualRuntimeSec) : '—'],
              ['생성', formatDateTime(job.createdAt)],
              ['종료', job.finishedAt ? formatDateTime(job.finishedAt) : '—'],
            ]}
          />
        </section>
      </div>

      {job.status === 'COMPLETED' && (
        <section className="card animate-enter flex flex-col gap-3" style={{ animationDelay: '120ms' }}>
          <h2 className="text-[15px] font-semibold text-dark">결과</h2>
          <div className="flex flex-wrap items-center gap-3">
            {job.resultUrl && (
              <a href={job.resultUrl} rel="noreferrer" className="btn btn-md btn-primary">
                결과 다운로드
              </a>
            )}
            {job.resultHash && (
              <button
                type="button"
                title={job.resultHash}
                onClick={() => navigator.clipboard?.writeText(job.resultHash!).catch(() => {})}
                className="rounded-md border border-hairline-strong bg-surface-1 px-3 py-2 text-[13px] tabular-nums text-text-muted hover:text-dark"
              >
                해시 {job.resultHash.slice(0, 10)}… 복사
              </button>
            )}
          </div>
        </section>
      )}
    </div>
  )
}

function Rows({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-[14px]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-text-muted">{k}</dt>
          <dd className="text-right font-medium tabular-nums text-dark">{v}</dd>
        </div>
      ))}
    </dl>
  )
}
