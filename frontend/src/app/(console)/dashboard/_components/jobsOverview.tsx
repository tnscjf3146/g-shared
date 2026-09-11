import Link from 'next/link'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ACTIVE_STATUSES, formatCost } from '@/features/jobs/format'
import type { JobSummary } from '@/features/jobs/types'
import { Pager } from './pager'

// 작업 탭 상단(대시보드.png 참고 구조, 프로필 사진 없음): 히어로 배너 + 상태 도넛 → KPI 카드 4개 → 워크로드별 작업 · 진행 중 작업(진행률)
// 데이터는 DashboardView 가 이미 들고 있는 작업 목록·카탈로그를 그대로 받는다(추가 fetch 없음, 15초 폴링도 거기서)

// 도넛 조각 — 상태 4묶음. 색은 정적 클래스(Tailwind v4 는 소스에 있는 클래스만 뽑는다)
const SEGMENTS = [
  { key: '진행', match: (j: JobSummary) => j.status === 'RUNNING', cls: 'stroke-primary', dot: 'bg-primary' },
  { key: '대기', match: (j: JobSummary) => ACTIVE_STATUSES.has(j.status) && j.status !== 'RUNNING', cls: 'stroke-accent-mint', dot: 'bg-accent-mint' },
  { key: '완료', match: (j: JobSummary) => j.status === 'COMPLETED', cls: 'stroke-dark', dot: 'bg-dark' },
  { key: '종료', match: (j: JobSummary) => j.status === 'FAILED' || j.status === 'CANCELLED', cls: 'stroke-hairline-strong', dot: 'bg-hairline-strong' },
]

export function JobsOverview({ jobList, loading, nameOf, onlineWorkers, totalWorkers }: { jobList: JobSummary[]; loading: boolean; nameOf: (id: number) => string; onlineWorkers: number; totalWorkers: number }) {
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

  return (
    <>
      {/* 1행: 히어로 배너(밝은 카드 — 페이지 톤과 같은 결, 그린은 포인트로만) + 상태 분포·워크로드별 */}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        {/* 히어로 — 연한 그린 틴트 단색(그라데이션·번짐 없음). 브랜드 색은 배경 틴트와 "수익" 강조, CTA 에만 */}
        <section className="animate-enter relative flex flex-col justify-between gap-8 rounded-2xl border border-hairline bg-primary/10 p-8 shadow-2">
          <div className="relative flex flex-col gap-3">
            <h2 className="max-w-[420px] text-[32px] leading-[1.2] font-extrabold tracking-display text-dark">
              유휴 GPU가 <span className="text-primary">수익</span>이 되는 곳
            </h2>
            <p className="max-w-[400px] text-[15px] leading-relaxed text-text-muted">워크로드를 등록하면 네트워크의 유휴 GPU에 배정되고, 진행 상황과 비용을 여기서 지켜볼 수 있습니다.</p>
          </div>
          <div className="relative">
            <Button href="/jobs/new">작업 만들기 +</Button>
          </div>
        </section>

        <section className="card animate-enter flex flex-col gap-5" style={{ animationDelay: '40ms' }}>
          <span className="text-[13px] font-semibold tracking-eyebrow text-text-muted">작업 상태 분포</span>
          <div className="flex items-center gap-6">
            <Donut counts={counts} total={total} />
            <ul className="flex min-w-0 flex-1 flex-col gap-2">
              {SEGMENTS.map((s, i) => (
                <li key={s.key} className="flex items-center gap-2 text-[14px] text-dark">
                  <span aria-hidden className={`size-2 flex-none rounded-pill ${s.dot}`} />
                  <span className="min-w-0 flex-1 truncate">{s.key}</span>
                  <span className="font-semibold tabular-nums">{total ? Math.round((counts[i] / total) * 100) : 0}%</span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      </div>

      {/* 2행: 지표 — 참고 디자인 KPI 처럼 아이콘 달린 독립 카드 4개 */}
      <div className="grid grid-cols-2 gap-6 lg:grid-cols-4">
        {[
          { label: '전체 작업', value: String(jobList.length), sub: `대기 ${counts[1]}개 포함`, d: 'M3 3h8v8H3zM13 3h8v5h-8zM13 12h8v9h-8zM3 15h8v6H3z' },
          { label: '진행 중', value: String(counts[0]), sub: `예상 비용(대기 포함) ${formatCost(activeCost)}`, d: 'M12 3a9 9 0 1 0 9 9M12 7v5l3 3' },
          { label: '완료', value: String(counts[2]), sub: `실패·취소 ${counts[3]}개`, d: 'M20 6 9 17l-5-5' },
          { label: '온라인 워커', value: `${onlineWorkers} / ${totalWorkers}`, sub: `${totalWorkers - onlineWorkers}대 오프라인`, d: 'M3 5h18v11H3zM8 20h8M12 16v4' },
        ].map((k, i) => (
          <section key={k.label} className="card animate-enter flex flex-col gap-4" style={{ animationDelay: `${80 + i * 40}ms` }}>
            <div className="flex items-center gap-2.5">
              <span className="grid size-8 flex-none place-items-center rounded-md bg-primary/10 text-primary">
                <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d={k.d} />
                </svg>
              </span>
              <span className="text-[14px] font-semibold text-text-muted">{k.label}</span>
            </div>
            <div className="flex flex-col gap-1">
              {loading ? <span className="h-9 w-20 animate-pulse rounded-xs bg-neutral-tint" /> : <span className="text-[34px] leading-none font-bold tabular-nums tracking-heading text-dark">{k.value}</span>}
              <span className="text-[13px] text-text-muted tabular-nums">{k.sub}</span>
            </div>
          </section>
        ))}
      </div>

      {/* 3행: 진행 중 작업 — 전체 폭 */}
      <section className="card animate-enter flex flex-col gap-4" style={{ animationDelay: '160ms' }}>
          <header className="flex items-center justify-between gap-3">
            <h2 className="text-[17px] font-bold tracking-heading text-dark">진행 중 작업</h2>
            <div className="flex items-center gap-3">
              <span className="text-[13px] text-text-muted">15초마다 갱신</span>
              <Pager page={runningPage} totalPages={runningPages} onChange={setRunningPage} />
            </div>
          </header>
          {loading && <div className="h-40 animate-pulse rounded-md bg-neutral-tint" />}
          {!loading && runningJobs.length === 0 && (
            <p className="flex flex-1 items-center justify-center text-[15px] text-text-muted">지금 학습 중인 작업이 없습니다.</p>
          )}
          <ul className="flex flex-col gap-5">
            {runningJobs.map((j) => (
              <li key={j.jobId} className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-3 text-[15px]">
                  <Link href={`/jobs/${j.jobId}`} className="min-w-0 truncate font-semibold text-dark hover:underline">
                    #{j.jobId} {nameOf(j.workloadId)}
                  </Link>
                  <div className="flex flex-none items-center gap-4">
                    <span className="text-[13px] text-text-muted tabular-nums">{formatCost(j.estimatedCost)}</span>
                    <Link href={`/jobs/${j.jobId}/chain`} className="text-[13px] text-primary hover:underline">체인 보기 →</Link>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <div className="h-2 flex-1 overflow-hidden rounded-pill bg-neutral-tint">
                    <div className="h-full rounded-pill bg-primary transition-[width] duration-500" style={{ width: `${j.progressPercent ?? 0}%` }} />
                  </div>
                  <span className="w-10 text-right text-[13px] font-semibold text-dark tabular-nums">{j.progressPercent ?? 0}%</span>
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
  return (
    <div className="relative size-[120px] flex-none">
      <svg viewBox="0 0 120 120" className="-rotate-90">
        {total === 0 && <circle cx="60" cy="60" r={R} fill="none" strokeWidth="12" className="stroke-neutral-tint" />}
        {counts.map(
          (n, i) =>
            n > 0 && <circle key={SEGMENTS[i].key} cx="60" cy="60" r={R} fill="none" strokeWidth="12" strokeDasharray={`${lens[i]} ${C - lens[i]}`} strokeDashoffset={-starts[i]} className={SEGMENTS[i].cls} />,
        )}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-[26px] leading-none font-bold tabular-nums text-dark">{total}</span>
        <span className="text-[12px] text-text-muted">작업</span>
      </div>
    </div>
  )
}
