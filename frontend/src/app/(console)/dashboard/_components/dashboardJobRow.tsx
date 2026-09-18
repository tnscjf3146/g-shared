'use client'

import Link from 'next/link'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/statusBadge'
import { JobDetailInline } from '@/features/jobs/components/jobDetailInline'
import { CANCELLABLE_STATUSES, formatDateTime, jobStatusMeta } from '@/features/jobs/format'
import type { Catalog, JobSummary } from '@/features/jobs/types'

// 작업 카드 — 작업마다 카드 하나, 세로로 쌓인다.
// 접힘: [>] "순번. 워크로드" | 상태 | 생성 | 상세·취소 (비율 그리드라 카드마다 컬럼 위치가 같다).
// 줄 어디를 눌러도(상세·취소 제외) 아래로 표(상태·작업 종류·GPU 등급·예상 소요·진척도)가 펼쳐진다. 상세 버튼은 전체 페이지(/jobs/[id])로 이동
export function DashboardJobRow({ job, index, workloadName, catalog, onCancel, style }: { job: JobSummary; index: number; workloadName: string; catalog: Catalog | null; onCancel: (id: number) => void; style?: React.CSSProperties }) {
  const [open, setOpen] = useState(false)
  const [mounted, setMounted] = useState(false) // 한 번 열리면 표를 계속 붙여 둔다 → 닫힘 모션 동안에도 내용이 남아 높이가 자연스럽게 줄어든다
  const toggle = () => {
    setOpen((o) => !o)
    setMounted(true)
  }
  const panelId = `job-detail-${job.jobId}`
  // 끝난 상태 구분: 실패 = 빨간 틴트 배경, 완료·취소됨 = 어두운 회색 배경(가라앉은 느낌)
  const failed = jobStatusMeta(job.status, job.failReason).tone === 'danger'
  const completed = job.status === 'COMPLETED'
  const cancelled = job.status === 'CANCELLED'
  const done = completed || cancelled
  const rowBg = failed ? 'var(--color-danger-tint)' : done ? 'color-mix(in srgb, var(--color-hairline-strong) 45%, var(--color-surface-2))' : undefined
  return (
    <li className="card animate-enter flex flex-col gap-4 px-6 py-4" style={{ ...style, ...(rowBg && { background: rowBg }) }}>
      {/* 줄 어디를 눌러도 토글. 키보드 접근은 왼쪽 > 버튼(aria-expanded)이 담당. 상세·취소는 전파를 막아 토글되지 않게 */}
      <div onClick={toggle} className="grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-6 select-none md:grid-cols-[minmax(0,3fr)_1.4fr_2.2fr_auto]">
        <div className="flex min-w-0 items-center gap-2">
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
          <h3 className={`truncate text-[17px] font-bold tracking-heading ${cancelled ? 'text-text-muted line-through decoration-1' : 'text-dark'}`}>
            <span className="tabular-nums">{index}.</span> {workloadName}
          </h3>
        </div>
        {/* 완료는 초록 배지로 눈에 띄게, 취소됨은 취소선, 나머지는 글자만(실패는 빨강) */}
        <div className="hidden md:block">
          {completed ? (
            <StatusBadge tone="success">완료</StatusBadge>
          ) : (
            <span className={`text-[17px] font-medium ${failed ? 'text-danger' : cancelled ? 'text-text-muted line-through decoration-1' : 'text-dark'}`}>{jobStatusMeta(job.status, job.failReason).label}</span>
          )}
        </div>
        <div className="hidden md:block">
          <div className="text-[14px] text-text-muted">생성</div>
          <div className="text-[17px] font-medium tabular-nums text-dark">{formatDateTime(job.createdAt)}</div>
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
      <div
        id={panelId}
        aria-hidden={!open}
        className={`grid transition-[grid-template-rows,margin] duration-300 ease-out-expo motion-reduce:transition-none ${open ? 'grid-rows-[1fr]' : '-mt-4 grid-rows-[0fr]'}`}
      >
        <div className={`min-h-0 overflow-hidden pl-9 transition-opacity duration-200 ${open ? 'opacity-100 delay-100' : 'opacity-0'}`}>{mounted && <JobDetailInline job={job} catalog={catalog} />}</div>
      </div>
    </li>
  )
}
