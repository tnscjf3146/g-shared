'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirmDialog'
import { cancelJob, getCatalog, getJobs } from '@/features/jobs/api'
import { jobMessageFor } from '@/features/jobs/errors'
import type { Catalog, JobSummary } from '@/features/jobs/types'
import { deleteWorker, getMyWorkers, getNetworkStats } from '@/features/workers/api'
import { WorkerRegisterCard } from '@/features/workers/components/workerRegisterCard'
import { workerMessageFor } from '@/features/workers/errors'
import { formatVram } from '@/features/workers/format'
import type { NetworkStats, Worker } from '@/features/workers/types'
import { DashboardJobRow } from './dashboardJobRow'
import { DashboardWorkerCard } from './dashboardWorkerCard'
import { JobsOverview } from './jobsOverview'
import { Pager } from './pager'
import { StatTiles } from './statTiles'

// 대시보드 본문(/dashboard/jobs · /dashboard/workers, 참고 디자인 구조): 헤더(제목 — 작업/공급, 전환은 사이드바) → 지표 4개 → 작업·워커 모두 카드 세로 목록.
// 데이터는 지금 API 에 있는 것만: 작업 목록(GET /jobs), 내 워커(GET /workers/me), 카탈로그(워크로드 이름). 15초 폴링, 시계는 1초
export type DashboardTab = 'jobs' | 'workers'
const POLL_MS = 15_000
const PAGE_SIZE = 50
const JOBS_PER_PAGE = 5 // 내 학습 작업 목록 — 클라이언트 페이징(받아 둔 50건 안에서)

type Loaded<T> = { status: 'loading' } | { status: 'error' } | { status: 'ready'; data: T }

export function DashboardView({ tab }: { tab: DashboardTab }) {
  const [jobs, setJobs] = useState<Loaded<JobSummary[]>>({ status: 'loading' })
  const [workers, setWorkers] = useState<Loaded<Worker[]>>({ status: 'loading' })
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [network, setNetwork] = useState<NetworkStats | null>(null) // 실패해도 카드만 '—' — 치명적이지 않다
  const [now, setNow] = useState(() => Date.now())
  const [notice, setNotice] = useState<string | null>(null)
  const [jobPage, setJobPage] = useState(0)
  const [jobView, setJobView] = useState<'latest' | 'oldest' | 'done'>('latest') // 목록 정렬·필터

  // 응답이 온 뒤에만 setState. 폴링 실패는 조용히(직전 데이터 유지)
  const load = useCallback((silent = false) => {
    getJobs({ page: 0, size: PAGE_SIZE })
      .then((p) => setJobs({ status: 'ready', data: p.content }))
      .catch(() => {
        if (!silent) setJobs({ status: 'error' })
      })
    getMyWorkers()
      .then((w) => setWorkers({ status: 'ready', data: w }))
      .catch(() => {
        if (!silent) setWorkers({ status: 'error' })
      })
  }, [])

  useEffect(() => {
    void load()
    const poll = setInterval(() => {
      setNow(Date.now()) // 워커 카드의 "n분 전" 갱신용. 폴링 주기면 충분
      if (document.visibilityState === 'visible') load(true)
    }, POLL_MS)
    return () => clearInterval(poll)
  }, [load])
  useEffect(() => {
    getNetworkStats()
      .then(setNetwork)
      .catch(() => {})
    getCatalog()
      .then(setCatalog)
      .catch(() => {})
  }, [])

  // 도넛·KPI 는 전체 기준이라 서버가 준 그대로(최신순) 쓴다
  const jobList = jobs.status === 'ready' ? jobs.data : []
  // "내 학습 작업" 목록에 보여줄 것 — 선택한 분류로 정렬/필터(받아둔 목록 안에서, 백엔드 불필요).
  // 생성일 기준으로 프론트에서 직접 정렬(ISO 문자열은 사전순=시간순). 서버 순서에 의존하지 않는다
  const newestFirst = (a: JobSummary, b: JobSummary) => b.createdAt.localeCompare(a.createdAt)
  const displayList =
    jobView === 'oldest'
      ? jobList.slice().sort((a, b) => a.createdAt.localeCompare(b.createdAt))
      : jobView === 'done'
        ? jobList.filter((j) => j.status === 'COMPLETED' || j.status === 'FAILED' || j.status === 'CANCELLED').sort(newestFirst)
        : jobList.slice().sort(newestFirst) // 'latest'
  // 폴링으로 목록이 줄면 현재 페이지가 범위를 넘을 수 있다 → 마지막 페이지로 클램프(빈 화면 방지)
  const totalJobPages = Math.max(1, Math.ceil(displayList.length / JOBS_PER_PAGE))
  const safeJobPage = Math.min(jobPage, totalJobPages - 1) // 렌더는 항상 범위 안(폴링으로 줄어도 빈 화면 안 남)
  const workerList = workers.status === 'ready' ? workers.data : []
  const nameOf = (id: number) => catalog?.workloads.find((w) => w.id === id)?.name ?? `워크로드 ${id}`

  // 작업 취소: 네이티브 confirm 대신 접근성 있는 ConfirmDialog(Esc·backdrop·포커스 트랩). 목록 행의 취소 버튼이 대상 id 를 넘긴다
  const [cancelTarget, setCancelTarget] = useState<number | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const onCancel = (id: number) => setCancelTarget(id)
  const confirmCancel = async () => {
    if (cancelTarget == null) return
    setCancelling(true)
    try {
      await cancelJob(cancelTarget)
    } catch (e) {
      setNotice(jobMessageFor(e))
    } finally {
      setCancelling(false)
      setCancelTarget(null)
      load(true)
    }
  }
  // 등록 해제: 확인 대화상자 한 줄 → 확정 시 삭제
  const [unlinkTarget, setUnlinkTarget] = useState<Worker | null>(null)
  const [unlinking, setUnlinking] = useState(false)
  const onUnlink = (w: Worker) => setUnlinkTarget(w)
  const confirmUnlink = async () => {
    if (!unlinkTarget) return
    setUnlinking(true)
    try {
      await deleteWorker(unlinkTarget.workerId)
    } catch (e) {
      setNotice(workerMessageFor(e))
    } finally {
      setUnlinking(false)
      setUnlinkTarget(null)
      load(true)
    }
  }

  // 지표 — 있는 필드로만. 작업 탭 지표는 JobsOverview 의 KPI 카드가 담당
  const online = workerList.filter((w) => w.status !== 'OFFLINE').length
  const busy = workerList.filter((w) => w.status === 'RUNNING').length
  const vram = workerList.reduce((a, w) => a + (w.vramMb ?? 0), 0)
  const benches = workerList.map((w) => w.benchmarkTokensPerSec).filter((v): v is number => v != null)
  const stats = [
    { label: '연결된 워커', value: `${online} / ${workerList.length}`, sub: `${workerList.length - online}대 오프라인` },
    { label: '작업 중 GPU', value: String(busy), sub: `대기 중 ${online - busy}대` },
    { label: '총 VRAM', value: vram ? formatVram(vram) : '—', sub: '등록된 GPU 합계' },
    { label: '평균 벤치마크', value: benches.length ? `${Math.round(benches.reduce((a, b) => a + b, 0) / benches.length).toLocaleString('ko-KR')} tok/s` : '측정 전', sub: `${benches.length}대 측정` },
  ]

  return (
    <>
      {/* 헤더: 제목만. 작업/공급 전환은 왼쪽 사이드바 메뉴 */}
      <section className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-h1 font-bold tracking-heading text-dark">{tab === 'jobs' ? '작업' : '공급'}</h1>
      </section>

      {notice && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-[16px] text-danger">
          {notice}
        </p>
      )}

      {/* 요약(도넛·KPI·진행 중 / 지표 4칸)은 데이터가 있을 때만 — 0건이면 빈 카드만 잔뜩 뜨므로, 아래 빈 상태 안내에 맡긴다 */}
      {tab === 'jobs'
        ? (jobs.status === 'loading' || jobList.length > 0) && <JobsOverview jobList={jobList} loading={jobs.status === 'loading'} nameOf={nameOf} network={network} now={now} />
        : (workers.status === 'loading' || workerList.length > 0) && <StatTiles items={stats} loading={workers.status === 'loading'} />}

      {tab === 'jobs' ? (
        <section className="flex flex-col gap-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-[24px] font-bold tracking-heading text-dark">내 학습 작업</h2>
            {jobs.status === 'ready' && jobList.length > 0 && (
              <label className="flex items-center gap-2 text-[13px] text-text-muted">
                분류
                <select
                  value={jobView}
                  onChange={(e) => {
                    setJobView(e.target.value as typeof jobView)
                    setJobPage(0) // 분류 바뀌면 첫 페이지로
                  }}
                  className="rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-[14px] text-dark focus:border-primary focus:outline-none"
                >
                  <option value="latest">최신순</option>
                  <option value="oldest">오래된순</option>
                  <option value="done">완료·취소·실패</option>
                </select>
              </label>
            )}
          </div>
          {jobs.status === 'error' && <Retry what="작업 목록" onRetry={() => { setJobs({ status: 'loading' }); load() }} />}
          {jobs.status === 'loading' && <ListSkeleton />}
          {jobs.status === 'ready' && jobList.length === 0 && (
            <section className="card animate-enter mx-auto flex w-full max-w-[640px] flex-col items-start gap-4 p-10">
              <h3 className="text-h2 font-bold tracking-heading text-dark">아직 작업이 없습니다</h3>
              <p className="text-[17px] text-text-muted">워크로드·모델·데이터셋을 고르고 견적을 확인하면 GPU 학습을 주문할 수 있습니다.</p>
              <Button href="/jobs/new" size="lg">첫 워크로드 등록</Button>
            </section>
          )}
          {jobs.status === 'ready' && jobList.length > 0 && displayList.length === 0 && (
            <p className="rounded-md bg-neutral-tint px-4 py-3 text-[14px] text-text-muted">이 분류에 해당하는 작업이 없습니다.</p>
          )}
          {jobs.status === 'ready' && displayList.length > 0 && (
            <>
              <ul className="flex flex-col gap-3">
                {displayList.slice(safeJobPage * JOBS_PER_PAGE, (safeJobPage + 1) * JOBS_PER_PAGE).map((j, i) => (
                  <DashboardJobRow key={j.jobId} job={j} index={safeJobPage * JOBS_PER_PAGE + i + 1} workloadName={nameOf(j.workloadId)} catalog={catalog} onCancel={onCancel} style={{ animationDelay: `${Math.min(i, 7) * 40}ms` }} />
                ))}
              </ul>
              {/* 메인 목록은 실무 관례대로 페이지 번호 + 화살표 */}
              <div className="flex justify-center">
                <Pager page={safeJobPage} totalPages={totalJobPages} onChange={setJobPage} numbered />
              </div>
            </>
          )}
        </section>
      ) : (
        <section className="flex flex-col gap-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-[24px] font-bold tracking-heading text-dark">내 워커</h2>
          </div>
          {workers.status === 'error' && <Retry what="워커 목록" onRetry={() => { setWorkers({ status: 'loading' }); load() }} />}
          {workers.status === 'loading' && <ListSkeleton />}
          {workers.status === 'ready' && workerList.length === 0 && <WorkerRegisterCard />}
          {workers.status === 'ready' && workerList.length > 0 && (
            <>
              <ul className="flex flex-col gap-3">
                {workerList.map((w, i) => (
                  <DashboardWorkerCard key={w.workerId} worker={w} index={i + 1} now={now} onUnlink={onUnlink} style={{ animationDelay: `${Math.min(i, 7) * 40}ms` }} />
                ))}
              </ul>
              {/* 공급 페이지의 주 CTA — primary 그린(화면당 하나) */}
              <Link href="/workers/new" className="btn btn-lg btn-primary w-full justify-between">
                내 GPU 등록하기
                <span aria-hidden>→</span>
              </Link>
            </>
          )}
        </section>
      )}
      <ConfirmDialog open={cancelTarget !== null} message={`작업 #${cancelTarget} 을(를) 취소할까요?`} confirmLabel="작업 취소" danger pending={cancelling} onConfirm={confirmCancel} onCancel={() => !cancelling && setCancelTarget(null)} />
      <ConfirmDialog open={unlinkTarget !== null} message="정말 해제할까요?" confirmLabel="해제" danger pending={unlinking} onConfirm={confirmUnlink} onCancel={() => !unlinking && setUnlinkTarget(null)} />
    </>
  )
}

function Retry({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <section className="card mx-auto flex w-full max-w-[640px] flex-col items-start gap-3 p-8">
      <p className="text-[17px] text-dark">{what}을 불러오지 못했습니다.</p>
      <button type="button" onClick={onRetry} className="text-[16px] text-primary hover:underline">
        다시 시도 →
      </button>
    </section>
  )
}

function ListSkeleton() {
  return (
    <div aria-busy aria-label="불러오는 중" className="flex flex-col gap-3">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="card h-[76px] animate-pulse" />
      ))}
    </div>
  )
}

