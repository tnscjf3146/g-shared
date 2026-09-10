import Link from 'next/link'
import { StatusBadge } from '@/components/ui/statusBadge'
import { STATUS_META, formatCost, formatDateTime } from '../format'
import type { JobSummary } from '../types'
import { JobProgress } from './jobProgress'

// 목록 한 줄 → /jobs/[id]. workload 이름은 부모가 카탈로그로 id→이름 매핑해 넘긴다(없으면 #id)
export function JobRow({ job, workloadName, style }: { job: JobSummary; workloadName?: string; style?: React.CSSProperties }) {
  const meta = STATUS_META[job.status]
  return (
    <Link href={`/jobs/${job.jobId}`} className="card layer-hover animate-enter flex items-center gap-4 py-4" style={style}>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex items-center gap-3">
          <span className="text-[13px] font-semibold tabular-nums text-text-muted">#{job.jobId}</span>
          <span className="truncate text-[16px] font-semibold text-dark">{workloadName ?? `작업 종류 ${job.workloadId}`}</span>
        </div>
        <span className="text-[13px] tabular-nums text-text-muted">{formatDateTime(job.createdAt)}</span>
      </div>
      <JobProgress job={job} compact />
      <span className="w-24 text-right text-[14px] font-medium tabular-nums text-dark">{formatCost(job.estimatedCost)}</span>
      <StatusBadge tone={meta.tone} className="w-[84px] justify-center">
        {meta.label}
      </StatusBadge>
    </Link>
  )
}
