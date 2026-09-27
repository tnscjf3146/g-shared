'use client'

import Link from 'next/link'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { JobDetailInline } from '@/features/jobs/components/jobDetailInline'
import { ACTIVE_STATUSES, CANCELLABLE_STATUSES, formatDateTime, jobStatusMeta } from '@/features/jobs/format'
import type { Catalog, JobSummary } from '@/features/jobs/types'

// 작업 카드 — 작업마다 카드 하나, 세로로 쌓인다.
// 접힘: [>] "순번. 워크로드" | 상태 | 생성 | 상세·취소 (비율 그리드라 카드마다 컬럼 위치가 같다).
// 진행·대기(active)만 줄을 눌러 아래로 표가 펼쳐진다(상세·취소 버튼 제외). 끝난 작업(완료·실패·취소)은 펼치지 않고 [상세]로만 본다.
export function DashboardJobRow({ job, index, workloadName, catalog, onCancel, style }: { job: JobSummary; index: number; workloadName: string; catalog: Catalog | null; onCancel: (id: number) => void; style?: React.CSSProperties }) {
  const [open, setOpen] = useState(false)
  const [mounted, setMounted] = useState(false) // 한 번 열리면 표를 계속 붙여 둔다 → 닫힘 모션 동안에도 내용이 남아 높이가 자연스럽게 줄어든다
  // 끝난 상태 구분: 실패 = 빨간 틴트, 완료·취소됨 = 어두운 회색(가라앉음). 취소됨은 상세 버튼 뺀 나머지에 취소선
  const failed = jobStatusMeta(job.status, job.failReason, job.cancelReason).tone === 'danger'
  const completed = job.status === 'COMPLETED'
  const cancelled = job.status === 'CANCELLED'
  // 끝난 작업(완료·실패·취소)은 펼치지 않는다 — [상세] 버튼으로만 본다. 진행·대기만 인라인으로 펼침
  const expandable = ACTIVE_STATUSES.has(job.status)
  const toggle = () => {
    if (!expandable) return
    setOpen((o) => !o)
    setMounted(true)
  }
  const panelId = `job-detail-${job.jobId}`
  const gray = 'color-mix(in srgb, var(--color-hairline-strong) 70%, var(--color-surface-2))'
  const rowBg = failed ? 'var(--color-danger-tint)' : completed || cancelled ? gray : undefined
  const strike = cancelled && !failed ? 'text-text-muted line-through decoration-1' : '' // 실패+취소 겹치면 빨강 배경만(취소선과 충돌 방지)
  return (
    <li className="card animate-enter flex flex-col gap-4 px-6 py-4" style={{ ...style, ...(rowBg && { background: rowBg }) }}>
      {/* 펼칠 수 있는 줄만 클릭 토글. 상세·취소는 전파를 막아 토글되지 않게 */}
      <div onClick={toggle} className={`grid grid-cols-[minmax(0,1fr)_auto] items-center gap-6 select-none md:grid-cols-[minmax(0,3fr)_1.4fr_2.2fr_auto] ${expandable ? 'cursor-pointer' : ''}`}>
        <div className="flex min-w-0 items-center gap-2">
          {expandable ? (
            <button
              type="button"
              aria-expanded={open}
              aria-controls={panelId}
              aria-label={open ? '접기' : '펼치기'}
              onClick={(e) => {
                e.stopPropagation() // 줄 클릭 토글과 겹쳐 두 번 뒤집히지 않게
                toggle()
              }}
              className="grid size-7 flex-none place-items-center rounded-md text-text-muted transition-colors hover:bg-black/5 hover:text-dark"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`transition-transform duration-200 ${open ? 'rotate-90' : ''}`}>
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
          ) : (
            <span aria-hidden className="size-7 flex-none" /> // 펼침 불가여도 제목 시작 위치는 같게
          )}
          <h3 className={`truncate text-body font-bold tracking-heading ${cancelled ? strike : 'text-dark'}`}>
            <span className="tabular-nums">{index}.</span> {workloadName}
          </h3>
        </div>
        {/* 다른 상태와 같은 본문(18px) 글자. 완료는 검정(배경 회색으로 구분), 실패는 빨강, 취소됨은 회색+취소선 */}
        <div className="hidden md:block">
          <span className={`text-body font-medium ${failed ? 'text-danger' : cancelled ? strike : 'text-dark'}`}>{jobStatusMeta(job.status, job.failReason, job.cancelReason).label}</span>
        </div>
        <div className={`hidden md:block ${strike}`}>
          <div className="text-caption text-text-muted">생성</div>
          <div className={`text-body font-medium tabular-nums ${cancelled ? '' : 'text-dark'}`}>{formatDateTime(job.createdAt)}</div>
        </div>
        {/* 상세는 항상 왼쪽 칸, 취소는 오른쪽 칸(없으면 빈 칸) — 줄마다 상세 위치가 같도록 */}
        <div onClick={(e) => e.stopPropagation()} className="grid grid-cols-2 gap-2">
          <Link href={`/jobs/${job.jobId}`} className="btn btn-sm btn-ghost">
            상세
          </Link>
          {CANCELLABLE_STATUSES.has(job.status) && (
            <Button variant="secondary" size="sm" className="text-danger" onClick={() => onCancel(job.jobId)}>
              취소
            </Button>
          )}
        </div>
      </div>

      {/* 펼침 모션: grid 행을 0fr ↔ 1fr 로 전환 → 패널 높이가 늘어나며 아래 카드들이 밀려 내려간다. 카드 gap 도 함께 전환 */}
      {expandable && (
        <div
          id={panelId}
          aria-hidden={!open}
          className={`grid transition-[grid-template-rows,margin] duration-300 ease-out-expo motion-reduce:transition-none ${open ? 'grid-rows-[1fr]' : '-mt-4 grid-rows-[0fr]'}`}
        >
          <div className={`min-h-0 overflow-hidden pl-9 transition-opacity duration-200 ${open ? 'opacity-100 delay-100' : 'opacity-0'}`}>{mounted && <JobDetailInline job={job} catalog={catalog} />}</div>
        </div>
      )}
    </li>
  )
}
