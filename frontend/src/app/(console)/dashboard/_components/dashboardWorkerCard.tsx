import { Button } from '@/components/ui/button'
import { PlainTable } from '@/features/jobs/components/plainTable'
import { formatTokensPerSec, formatVram, relativeTime } from '@/features/workers/format'
import type { Worker, WorkerStatus } from '@/features/workers/types'

// 워커 카드 — 작업 카드와 같은 구조. 제목 줄("순번. 기기 이름" | 상태 | OS | 등록 해제) 아래 장식 없는 격자 표.
// 색·점·배지 없이 글자만. 온도·전력·가동률·수익·수신 토글은 백엔드에 없어 그리지 않는다
const STATUS_LABEL: Record<WorkerStatus, string> = { ONLINE: '대기 · 배정 가능', RUNNING: '작업 중', OFFLINE: '오프라인' }
// 컬럼 폭 고정: GPU | VRAM | 드라이버 | 벤치마크 | 하트비트 | 현재 작업
const COLS = ['26%', '10%', '14%', '16%', '18%', '16%']

export function DashboardWorkerCard({ worker, index, now, onUnlink, style }: { worker: Worker; index: number; now: number; onUnlink: (w: Worker) => void; style?: React.CSSProperties }) {
  const heartbeat = worker.lastHeartbeatAt ? relativeTime(worker.lastHeartbeatAt, now) : '기록 없음'
  // 오프라인 워커는 꺼진 기계처럼 — 작업 목록의 완료·취소와 같은 어두운 회색 배경
  const offline = worker.status === 'OFFLINE'
  return (
    <li className="card animate-enter flex flex-col gap-4 px-6 py-4" style={{ ...style, ...(offline && { background: 'color-mix(in srgb, var(--color-hairline-strong) 45%, var(--color-surface-2))' }) }}>
      {/* 이름 칸 ≈ 26% — 상태가 아래 표의 VRAM 열 시작점 부근에서 시작하게 */}
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-6 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1.5fr)_minmax(0,1.6fr)_auto]">
        <h3 className="truncate text-[17px] font-bold tracking-heading text-dark" title={worker.workerId}>
          <span className="tabular-nums">{index}.</span> {worker.deviceName}
        </h3>
        {/* 좁은 폭에서 두 줄로 꺾이지 않게 — 전부 한 줄 truncate, 잘리면 … + title 툴팁 */}
        <div className="hidden min-w-0 md:block">
          <div className="truncate text-[17px] font-medium text-dark" title={STATUS_LABEL[worker.status]}>{STATUS_LABEL[worker.status]}</div>
          {/* 지갑 미연결이면 예치 때 넣을 공급자 주소가 없어 작업 후보에 오르지 못한다 — 주인만 알 수 있으니 여기서 알린다 */}
          {!worker.walletLinked && <div className="truncate text-[14px] text-danger" title="지갑 미연결 · 작업을 받을 수 없습니다">지갑 미연결 · 작업을 받을 수 없습니다</div>}
        </div>
        <div className="hidden min-w-0 md:block">
          <div className="text-[14px] text-text-muted">OS</div>
          <div className="truncate text-[17px] font-medium text-dark" title={worker.osVersion ?? undefined}>{worker.osVersion ?? '—'}</div>
        </div>
        <Button variant="ghost" size="sm" disabled={worker.status === 'RUNNING'} title={worker.status === 'RUNNING' ? '작업이 끝난 뒤 해제할 수 있습니다' : undefined} onClick={() => onUnlink(worker)}>
          등록 해제
        </Button>
      </div>

      <PlainTable
        widths={COLS}
        minWidth={720}
        cols={[
          ['GPU', <span key="gpu" title={worker.gpuModel}>{worker.gpuModel.replace('NVIDIA GeForce ', '')}</span>],
          ['VRAM', worker.vramMb != null ? formatVram(worker.vramMb) : '—'],
          ['드라이버', worker.driverVersion ?? '—'],
          ['벤치마크', worker.benchmarkTokensPerSec != null ? formatTokensPerSec(worker.benchmarkTokensPerSec) : '측정 전'],
          ['하트비트', heartbeat],
          ['현재 작업', worker.status === 'RUNNING' && worker.currentJobId ? `#${worker.currentJobId}` : '—'],
        ]}
      />
    </li>
  )
}
