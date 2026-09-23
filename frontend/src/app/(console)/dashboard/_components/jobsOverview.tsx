import Link from 'next/link'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ACTIVE_STATUSES, formatCost } from '@/features/jobs/format'
import type { JobSummary } from '@/features/jobs/types'
import type { NetworkStats } from '@/features/workers/types'
import { Pager } from './pager'

// 작업 탭 상단(대시보드.png 참고 구조, 프로필 사진 없음): 히어로 배너 + 상태 도넛 → KPI 카드 4개 → 워크로드별 작업 · 진행 중 작업(진행률)
// 데이터는 DashboardView 가 이미 들고 있는 작업 목록·카탈로그를 그대로 받는다(추가 fetch 없음, 15초 폴링도 거기서)

// 도넛 조각 — 상태 4묶음. 채도·명도를 맞춘 조화 팔레트(초록·앰버·슬레이트·로즈, 모두 중간 톤)로 한 벌처럼 보이게 한다.
// 브랜드 그린(버튼용 #76B900)은 쨍해서 다른 색과 따로 놀아, 차트에는 톤을 낮춘 초록을 쓴다
const SEGMENTS = [
  { key: '진행', match: (j: JobSummary) => j.status === 'RUNNING', color: 'var(--color-chart-green)' },
  { key: '대기', match: (j: JobSummary) => ACTIVE_STATUSES.has(j.status) && j.status !== 'RUNNING', color: 'var(--color-chart-amber)' },
  { key: '완료', match: (j: JobSummary) => j.status === 'COMPLETED', color: 'var(--color-chart-slate)' },
  { key: '취소/실패', match: (j: JobSummary) => j.status === 'FAILED' || j.status === 'CANCELLED', color: 'var(--color-chart-rose)' },
]

export function JobsOverview({ jobList, loading, nameOf, network, now }: { jobList: JobSummary[]; loading: boolean; nameOf: (id: number) => string; network: NetworkStats | null; now: number }) {
  // 끝나지 않은 작업(대기 포함) 전체의 예상 비용 합 — 라벨에 "대기 포함"을 명시해 목록의 개별 비용과 헷갈리지 않게
  const activeCost = jobList.filter((j) => ACTIVE_STATUSES.has(j.status)).reduce((a, j) => a + j.estimatedCost, 0)
  const counts = SEGMENTS.map((s) => jobList.filter(s.match).length)
  const total = counts.reduce((a, b) => a + b, 0)

  // 진행 중 작업 — 진행률 내림차순, 3건씩 화살표 페이징
  const RUNNING_PER_PAGE = 3
  const [runningPage, setRunningPage] = useState(0)
  const allRunning = jobList.filter((j) => j.status === 'RUNNING').sort((a, b) => (b.progressPercent ?? 0) - (a.progressPercent ?? 0))
  const runningPages = Math.ceil(allRunning.length / RUNNING_PER_PAGE)
  const runningJobs = allRunning.slice(runningPage * RUNNING_PER_PAGE, (runningPage + 1) * RUNNING_PER_PAGE)

  // 최근 7일 새 작업 — createdAt(LocalDateTime, 로컬 시간) 기준. now 는 DashboardView 의 시계 상태(렌더 순수성)
  const weekAgo = now - 7 * 24 * 3600_000
  const recent7 = jobList.filter((j) => new Date(j.createdAt).getTime() >= weekAgo).length

  return (
    <>
      {/* 1행: 히어로 배너 + 상태 분포 */}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        {/* 히어로 — 연한 그린 틴트 단색. 브랜드 색은 배경 틴트와 "수익" 강조, CTA 에만 */}
        <section className="animate-enter relative flex flex-col justify-between gap-8 rounded-2xl border border-hairline bg-primary/10 p-8 shadow-2">
          <div className="relative flex flex-col gap-3">
            <h2 className="max-w-[420px] text-h1 leading-[1.2] font-extrabold tracking-display text-dark">
              유휴 GPU가 <span className="text-primary">수익</span>이 되는 곳
            </h2>
            <p className="max-w-[400px] text-label-sm leading-relaxed text-text-muted">워크로드를 등록하면 네트워크의 유휴 GPU에 배정되고, 진행 상황과 비용을 여기서 지켜볼 수 있습니다.</p>
          </div>
          <div className="relative">
            <Button href="/jobs/new">작업 만들기 +</Button>
          </div>
        </section>

        <section className="card animate-enter flex flex-col gap-5" style={{ animationDelay: '40ms' }}>
          <span className="text-caption font-semibold tracking-eyebrow text-text-muted">작업 상태 분포</span>
          <div className="flex items-center gap-6">
            <Donut counts={counts} total={total} />
            <ul className="flex min-w-0 flex-1 flex-col gap-2">
              {SEGMENTS.map((s, i) => (
                <li key={s.key} className="flex items-center gap-2 text-caption text-dark">
                  <span aria-hidden className="size-2 flex-none rounded-pill" style={{ backgroundColor: s.color }} />
                  <span className="min-w-0 flex-1 truncate">{s.key}</span>
                  <span className="font-semibold tabular-nums">{total ? Math.round((counts[i] / total) * 100) : 0}%</span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      </div>

      {/* 2행: 지표 — 도넛(개수·구성비)과 겹치지 않는 것만. 개수 중복 대신 비용·추세·네트워크 */}
      <div className="grid grid-cols-2 gap-6 lg:grid-cols-3">
        {[
          { label: '진행 중 예상 비용', value: formatCost(activeCost), sub: `진행·대기 ${counts[0] + counts[1]}건 견적 합`, d: 'M12 3a9 9 0 1 0 9 9M12 7v5l3 3' },
          { label: '최근 7일 새 작업', value: String(recent7), sub: '이번 주에 만든 작업', d: 'M3 3h8v8H3zM13 3h8v5h-8zM13 12h8v9h-8zM3 15h8v6H3z' },
          // 네트워크 전체(GET /network/stats) — 내 워커가 아니라 "지금 일 받을 수 있는 GPU가 몇 대냐"
          { label: '네트워크 온라인 워커', value: network ? `${network.onlineWorkers} / ${network.totalWorkers}` : '—', sub: '지금 작업을 받을 수 있는 GPU', d: 'M3 5h18v11H3zM8 20h8M12 16v4' },
        ].map((k, i) => (
          <section key={k.label} className="card animate-enter flex flex-col gap-4" style={{ animationDelay: `${80 + i * 40}ms` }}>
            <div className="flex items-center gap-2.5">
              <span className="grid size-8 flex-none place-items-center rounded-md bg-primary/10 text-primary">
                <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d={k.d} />
                </svg>
              </span>
              <span className="text-caption font-semibold text-text-muted">{k.label}</span>
            </div>
            <div className="flex flex-col gap-1">
              {loading ? <span className="h-8 w-20 animate-pulse rounded-xs bg-neutral-tint" /> : <span className="text-h1 leading-none font-bold tabular-nums tracking-heading text-dark">{k.value}</span>}
              <span className="text-caption text-text-muted tabular-nums">{k.sub}</span>
            </div>
          </section>
        ))}
      </div>

      {/* 3행: 진행 중 작업 — 이 화면의 주인공. card-featured(그린 테두리 + 글로우, 화면당 하나)로 위계를 준다 */}
      <section className="card card-featured animate-enter flex flex-col gap-4" style={{ animationDelay: '160ms' }}>
          <header className="flex items-center justify-between gap-3">
            <h2 className="text-body font-bold tracking-heading text-dark">진행 중 작업</h2>
            <div className="flex items-center gap-3">
              <span className="text-caption text-text-muted">15초마다 갱신</span>
              <Pager page={runningPage} totalPages={runningPages} onChange={setRunningPage} />
            </div>
          </header>
          {loading && <div className="h-40 animate-pulse rounded-md bg-neutral-tint" />}
          {!loading && runningJobs.length === 0 && (
            <p className="flex flex-1 items-center justify-center text-label-sm text-text-muted">지금 학습 중인 작업이 없습니다.</p>
          )}
          <ul className="flex flex-col gap-5">
            {runningJobs.map((j) => (
              <li key={j.jobId} className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-3 text-label-sm">
                  <Link href={`/jobs/${j.jobId}`} className="min-w-0 truncate font-semibold text-dark hover:underline">
                    #{j.jobId} {nameOf(j.workloadId)}
                  </Link>
                  <div className="flex flex-none items-center gap-4">
                    <span className="text-caption text-text-muted tabular-nums">{formatCost(j.estimatedCost)}</span>
                    <Link href={`/jobs/${j.jobId}/chain`} className="text-caption text-primary hover:underline">체인 보기 →</Link>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <div className="h-2 flex-1 overflow-hidden rounded-pill bg-neutral-tint">
                    <div className="h-full rounded-pill bg-primary transition-[width] duration-500" style={{ width: `${j.progressPercent ?? 0}%` }} />
                  </div>
                  <span className="w-10 text-right text-caption font-semibold text-dark tabular-nums">{j.progressPercent ?? 0}%</span>
                </div>
              </li>
            ))}
          </ul>
        </section>
    </>
  )
}

// 상태 도넛 — SVG 원 4조각(stroke-dasharray). 가운데는 전체 작업 수
function Donut({ counts, total }: { counts: number[]; total: number }) {
  const R = 44
  const C = 2 * Math.PI * R
  // 조각 i 의 시작 위치 = 앞 조각 길이의 누적 합
  const lens = counts.map((n) => (total ? (n / total) * C : 0))
  const starts = lens.map((_, i) => lens.slice(0, i).reduce((a, b) => a + b, 0))
  // 스크린리더용 요약 — 색으로만 구분되는 조각을 글자로: "전체 7개 · 진행 2, 대기 1, 완료 3, 종료 1"
  const label = total ? `작업 상태 분포, 전체 ${total}개 · ${SEGMENTS.map((s, i) => `${s.key} ${counts[i]}`).join(', ')}` : '작업 없음'
  return (
    <div className="relative size-[120px] flex-none">
      <svg viewBox="0 0 120 120" className="-rotate-90" role="img" aria-label={label}>
        {total === 0 && <circle cx="60" cy="60" r={R} fill="none" strokeWidth="12" className="stroke-neutral-tint" />}
        {counts.map(
          (n, i) =>
            n > 0 && <circle key={SEGMENTS[i].key} cx="60" cy="60" r={R} fill="none" strokeWidth="12" strokeDasharray={`${lens[i]} ${C - lens[i]}`} strokeDashoffset={-starts[i]} style={{ stroke: SEGMENTS[i].color }} />,
        )}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-h2 leading-none font-bold tabular-nums text-dark">{total}</span>
        <span className="text-hint text-text-muted">작업</span>
      </div>
    </div>
  )
}
