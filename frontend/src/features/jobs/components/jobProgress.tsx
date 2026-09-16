import { ProgressBar } from '@/components/ui/progressBar'
import type { JobDetail, JobSummary } from '../types'

// 진행률 바 + "epoch 2/3 · 42%". RUNNING 이 아니면 그리지 않는다(상태 배지가 대신 말한다)
export function JobProgress({ job, compact = false }: { job: Pick<JobDetail, 'status' | 'progressPercent'> & Partial<Pick<JobDetail, 'progressEpoch' | 'progressTotalEpochs'>> | JobSummary; compact?: boolean }) {
  if (job.status !== 'RUNNING') return null
  const pct = job.progressPercent ?? 0
  const epoch = 'progressEpoch' in job && job.progressEpoch != null && job.progressTotalEpochs ? `에폭 ${job.progressEpoch}/${job.progressTotalEpochs} · ` : ''
  return (
    <div className={`flex items-center gap-3 ${compact ? 'w-40' : 'w-full'}`}>
      <ProgressBar value={pct} />
      <span className="shrink-0 text-[13px] font-medium tabular-nums text-text-muted">
        {compact ? '' : epoch}
        {pct}%
      </span>
    </div>
  )
}
