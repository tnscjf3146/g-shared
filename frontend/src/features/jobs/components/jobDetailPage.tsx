'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/fetcher'
import { cancelJob, getCatalog, getJob } from '../api'
import { jobMessageFor } from '../errors'
import { ACTIVE_STATUSES, CANCELLABLE_STATUSES, STATUS_META, STATUS_SENTENCE, formatBytes, formatCost, formatDateTime, formatDuration } from '../format'
import type { Catalog, JobDetail } from '../types'
import { JobProgress } from './jobProgress'
import { JobDetailSkeleton } from './jobSkeleton'
import { PlainTable } from './plainTable'

// /jobs/[id] 본문(job.md §5) — 대시보드와 같은 톤: 배지·모션 없이 제목 줄 + 격자 표. 진행 중 상태면 10초 폴링, 끝나면 중단. 이름(workload·모델·GPU)은 카탈로그로 id→이름
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
        <Link href="/dashboard/jobs" className="text-[14px] text-primary hover:underline">
          ← 돌아가기
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

  const dash = '—'
  return (
    <div className="flex w-full flex-col gap-6">
      {/* 헤더: [← 돌아가기] 한 줄, 그 아래 워크로드 이름 · 상태(글자) | 작업 취소 */}
      <header className="flex flex-col gap-3">
        <Link href="/dashboard/jobs" className="self-start text-[15px] text-text-muted hover:text-dark">
          ← 돌아가기
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex min-w-0 flex-wrap items-center gap-4">
            <h1 className="text-h1 font-bold tracking-heading text-dark">{name.workload}</h1>
            <span className="text-[17px] font-medium text-dark">{meta.label}</span>
          </div>
          {CANCELLABLE_STATUSES.has(job.status) && (
            <Button variant="secondary" size="sm" disabled={pending} onClick={cancel}>
              {pending ? '취소 중…' : '작업 취소'}
            </Button>
          )}
        </div>
      </header>
      {error && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-[15px] text-danger">
          {error}
        </p>
      )}

      <section className="card flex flex-col gap-3 px-6 py-5">
        <h2 className="text-[17px] font-bold tracking-heading text-dark">진행</h2>
        <p className={`text-[16px] ${job.status === 'FAILED' ? 'text-danger' : 'text-dark'}`}>{STATUS_SENTENCE[job.status]}</p>
        {job.status === 'RUNNING' && <JobProgress job={job} />}
      </section>

      <section className="card flex flex-col gap-3 px-6 py-5">
        <h2 className="text-[17px] font-bold tracking-heading text-dark">설정</h2>
        <PlainTable cols={[['작업 종류', name.workload], ['베이스 모델', name.model], ['GPU 등급', name.gpu], ['학습 설정', job.trainingMode === 'AUTO' ? '자동' : '직접 설정']]} widths={['20%', '30%', '30%', '20%']} />
      </section>

      {/* 데이터셋 — 3단계에서 브라우저가 뽑은 메타 5값 + 파일명. 백엔드 응답에 실리면 보인다 */}
      {job.datasetMetadata && (
        <section className="card flex flex-col gap-3 px-6 py-5">
          <h2 className="text-[17px] font-bold tracking-heading text-dark">데이터셋</h2>
          <PlainTable
            widths={['30%', '12%', '12%', '16%', '15%', '15%']}
            minWidth={760}
            cols={[
              ['파일', job.datasetName ?? dash],
              ['크기', formatBytes(job.datasetMetadata.sizeBytes)],
              ['샘플', `${job.datasetMetadata.sampleCount.toLocaleString('ko-KR')}개`],
              ['토큰(근사)', job.datasetMetadata.totalTokens.toLocaleString('ko-KR')],
              ['평균 길이', String(Math.round(job.datasetMetadata.avgSeqLength))],
              ['최대 길이', String(job.datasetMetadata.maxSeqLength)],
            ]}
          />
        </section>
      )}

      <section className="card flex flex-col gap-3 px-6 py-5">
        <h2 className="text-[17px] font-bold tracking-heading text-dark">비용 · 시간</h2>
        <PlainTable
          minWidth={900}
          widths={['11%', '11%', '12%', '12%', '18%', '18%', '18%']} // 날짜 칸을 넓게
          cols={[
            ['예상 비용', formatCost(job.estimatedCost)],
            ['실제 비용', job.actualCost != null ? formatCost(job.actualCost) : dash],
            ['예상 소요', formatDuration(job.predictedRuntimeSec)],
            ['실제 소요', job.actualRuntimeSec != null ? formatDuration(job.actualRuntimeSec) : dash],
            ['생성', formatDateTime(job.createdAt)],
            ['시작', job.startedAt ? formatDateTime(job.startedAt) : dash],
            ['종료', job.finishedAt ? formatDateTime(job.finishedAt) : dash],
          ]}
        />
      </section>

      {job.status === 'COMPLETED' && (job.resultUrl || job.resultHash) && (
        <section className="card flex flex-col gap-3 px-6 py-5">
          <h2 className="text-[17px] font-bold tracking-heading text-dark">결과</h2>
          <div className="flex flex-wrap items-center gap-3">
            {job.resultUrl && (
              <a href={job.resultUrl} rel="noreferrer" className="btn btn-md btn-primary">
                결과 다운로드
              </a>
            )}
            {job.resultHash && (
              <button type="button" title={job.resultHash} onClick={() => navigator.clipboard?.writeText(job.resultHash!).catch(() => {})} className="btn btn-md btn-secondary tabular-nums">
                해시 {job.resultHash.slice(0, 10)}… 복사
              </button>
            )}
          </div>
        </section>
      )}
    </div>
  )
}
