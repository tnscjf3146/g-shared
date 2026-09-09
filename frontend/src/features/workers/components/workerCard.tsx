'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/statusBadge'
import { deleteWorker } from '../api'
import { workerMessageFor } from '../errors'
import { formatTokensPerSec, formatVram, relativeTime } from '../format'
import type { Worker, WorkerStatus } from '../types'

// 워커 한 대(worker.md §5). 상태·하트비트는 부모가 폴링으로 갱신해 props 로 내려준다
const STATUS: Record<WorkerStatus, { tone: 'success' | 'primary' | 'neutral'; label: string }> = {
  ONLINE: { tone: 'success', label: '대기 중' },
  RUNNING: { tone: 'primary', label: '작업 중' },
  OFFLINE: { tone: 'neutral', label: '오프라인' },
}

export function WorkerCard({ worker, now, onDeleted, style }: { worker: Worker; now: number; onDeleted: () => void; style?: React.CSSProperties }) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const status = STATUS[worker.status]
  const spec = [worker.gpuModel, worker.vramMb != null && formatVram(worker.vramMb), worker.driverVersion && `드라이버 ${worker.driverVersion}`, worker.osVersion]
    .filter(Boolean)
    .join(' · ')

  const remove = async () => {
    if (!confirm(`${worker.deviceName} 의 등록을 해제할까요? 이 PC 의 G-shared 는 다음 실행 때 다시 등록해야 합니다.`)) return
    setPending(true)
    setError(null)
    try {
      await deleteWorker(worker.workerId)
      onDeleted()
    } catch (e) {
      setError(workerMessageFor(e))
      setPending(false)
    }
  }

  return (
    <article className="card animate-enter flex flex-col gap-4" style={style}>
      <header className="flex items-start justify-between gap-3">
        <h3 className="truncate text-[18px] font-bold tracking-heading text-dark" title={worker.workerId}>
          {worker.deviceName}
        </h3>
        <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
      </header>

      <p className="text-[14px] text-text-muted">{spec}</p>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-[14px]">
        <dt className="text-text-muted">벤치마크</dt>
        <dd className="text-right font-medium tabular-nums text-dark">{worker.benchmarkTokensPerSec != null ? formatTokensPerSec(worker.benchmarkTokensPerSec) : '측정 전'}</dd>
        <dt className="text-text-muted">{worker.status === 'OFFLINE' ? '마지막 연결' : '하트비트'}</dt>
        <dd className="text-right font-medium tabular-nums text-dark">{worker.lastHeartbeatAt ? relativeTime(worker.lastHeartbeatAt, now) : '연결 이력 없음'}</dd>
        {worker.status === 'RUNNING' && worker.currentJobId && (
          <>
            <dt className="text-text-muted">현재 작업</dt>
            <dd className="truncate text-right font-medium tabular-nums text-dark" title={worker.currentJobId}>
              {worker.currentJobId}
            </dd>
          </>
        )}
      </dl>

      {error && (
        <p role="alert" className="rounded-md bg-danger-tint px-3 py-2 text-[13px] text-danger">
          {error}
        </p>
      )}

      <div className="flex justify-end">
        <Button
          variant="ghost"
          size="sm"
          disabled={pending || worker.status === 'RUNNING'}
          title={worker.status === 'RUNNING' ? '작업이 끝난 뒤 해제할 수 있습니다' : undefined}
          onClick={remove}
        >
          {pending ? '해제 중…' : '등록 해제'}
        </Button>
      </div>
    </article>
  )
}
