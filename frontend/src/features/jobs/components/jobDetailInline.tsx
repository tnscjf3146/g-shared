'use client'

import { useEffect, useState } from 'react'
import { getJob } from '../api'
import { STATUS_META, formatDuration } from '../format'
import type { Catalog, JobDetail, JobSummary } from '../types'
import { JobProgress } from './jobProgress'
import { PlainTable } from './plainTable'

// 대시보드 작업 줄 아래에 펼쳐지는 표: 상태 | 작업 종류 | GPU 등급 | 예상 소요 | 진척도.
// 상태·작업 종류·진척도는 목록 응답(JobSummary)으로 바로, GPU 등급·예상 소요는 GET /jobs/{id} 로 채운다. 목록 폴링으로 상태·진행률이 바뀌면 다시 받는다
type State = { status: 'loading' } | { status: 'error' } | { status: 'ready'; job: JobDetail }
const COLS = ['14%', '20%', '30%', '14%', '22%'] // 상태 | 작업 종류 | GPU 등급 | 예상 소요 | 진척도

export function JobDetailInline({ job, catalog }: { job: JobSummary; catalog: Catalog | null }) {
  const [state, setState] = useState<State>({ status: 'loading' })
  const { jobId, status, progressPercent } = job
  useEffect(() => {
    let alive = true
    getJob(jobId)
      .then((j) => alive && setState({ status: 'ready', job: j }))
      .catch(() => alive && setState((s) => (s.status === 'ready' ? s : { status: 'error' })))
    return () => {
      alive = false
    }
  }, [jobId, status, progressPercent])

  const workload = catalog?.workloads.find((w) => w.id === job.workloadId)?.name ?? `#${job.workloadId}`
  const detail = state.status === 'ready' ? state.job : null
  const gpu = detail ? (catalog?.gpus.find((g) => g.id === detail.gpuId)?.model ?? `#${detail.gpuId}`) : null
  // 상세 응답 대기 중엔 회색 막대, 실패면 —
  const pendingCell = state.status === 'loading' ? <span aria-busy aria-label="불러오는 중" className="inline-block h-4 w-24 animate-pulse rounded-xs bg-neutral-tint align-middle" /> : <span className="text-text-muted">—</span>

  return (
    <PlainTable
      widths={COLS}
      cols={[
        ['상태', STATUS_META[job.status].label],
        ['작업 종류', workload],
        ['GPU 등급', gpu ?? pendingCell],
        ['예상 소요', detail ? formatDuration(detail.predictedRuntimeSec) : pendingCell],
        ['진척도', job.status === 'RUNNING' ? <JobProgress job={job} /> : <span className="text-text-muted">—</span>],
      ]}
    />
  )
}
