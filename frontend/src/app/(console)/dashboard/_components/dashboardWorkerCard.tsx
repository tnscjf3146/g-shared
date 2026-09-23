'use client'

import { useCallback, useState } from 'react'
import { Button } from '@/components/ui/button'
import { PlainTable } from '@/features/jobs/components/plainTable'
import { OperatorApproval, type WorkAvailability } from '@/features/workers/components/operatorApproval'
import { formatReliability, formatTokensPerSec, formatVram, relativeTime } from '@/features/workers/format'
import type { Worker } from '@/features/workers/types'

// 워커 카드 — 작업 카드와 같은 구조. 제목 줄("순번. 기기 이름" | 연결·권한 | OS | 등록 해제) 아래 장식 없는 격자 표.
// 색·점·배지 없이 글자만. 온도·전력·가동률·수익·수신 토글은 백엔드에 없어 그리지 않는다
// 작업 가능/불가(상태)는 OperatorApproval 이 판정해 올려주고, 아래 표의 마지막 "상태" 칸에 표시한다(현재 작업 옆). ONLINE 은 연결 상태일 뿐 작업 가능이 아니다
// 컬럼 폭 고정: GPU | VRAM | 드라이버 | 벤치마크 | 작업 이력 | 하트비트 | 현재 작업 | 상태
const COLS = ['20%', '8%', '11%', '13%', '11%', '13%', '10%', '14%']
const WORK_TONE = { muted: 'text-text-muted', danger: 'text-danger', ok: 'font-semibold text-primary' }

export function DashboardWorkerCard({ worker, index, now, onUnlink, style }: { worker: Worker; index: number; now: number; onUnlink: (w: Worker) => void; style?: React.CSSProperties }) {
  const [work, setWork] = useState<WorkAvailability>({ text: '확인 중…', tone: 'muted' })
  // 같은 값이면 갱신 건너뛰기(같은 참조 반환 → React 리렌더 skip). onWork 는 안정적이라 자식 effect 의존성도 흔들지 않는다
  const onWork = useCallback((w: WorkAvailability) => setWork((prev) => (prev.text === w.text && prev.tone === w.tone ? prev : w)), [])
  const reliability = formatReliability(worker)
  const heartbeat = worker.lastHeartbeatAt ? relativeTime(worker.lastHeartbeatAt, now) : '기록 없음'
  // 오프라인 워커는 꺼진 기계처럼 — 작업 목록의 완료·취소와 같은 어두운 회색 배경
  const offline = worker.status === 'OFFLINE'
  return (
    <li className="card animate-enter flex flex-col gap-4 px-6 py-4" style={{ ...style, ...(offline && { background: 'color-mix(in srgb, var(--color-hairline-strong) 45%, var(--color-surface-2))' }) }}>
      {/* 넓은 화면: 이름 | 연결·권한 | OS | 등록해제 한 줄. 좁은 화면: 세로로 쌓아 아무것도 안 숨긴다 */}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1.5fr)_minmax(0,1.6fr)_auto] md:items-center md:gap-6">
        {/* 이름 + 등록해제 — 좁을 땐 한 줄, 넓을 땐 이름만(버튼은 grid 마지막 칸으로) */}
        <div className="flex items-center justify-between gap-4 md:contents">
          <h3 className="truncate text-body font-bold tracking-heading text-dark" title={worker.workerId}>
            <span className="tabular-nums">{index}.</span> {worker.deviceName}
          </h3>
          <Button variant="ghost" size="sm" className="flex-none md:order-last" disabled={worker.status === 'RUNNING'} title={worker.status === 'RUNNING' ? '작업이 끝난 뒤 해제할 수 있습니다' : undefined} onClick={() => onUnlink(worker)}>
            등록 해제
          </Button>
        </div>
        <div className="min-w-0">
          <OperatorApproval worker={worker} onWork={onWork} />
        </div>
        <div className="min-w-0">
          <div className="text-caption text-text-muted">OS</div>
          <div className="truncate text-body font-medium text-dark" title={worker.osVersion ?? undefined}>{worker.osVersion ?? '—'}</div>
        </div>
      </div>

      <PlainTable
        widths={COLS}
        minWidth={900}
        cols={[
          ['GPU', <span key="gpu" title={worker.gpuModel}>{worker.gpuModel.replace('NVIDIA GeForce ', '')}</span>],
          ['VRAM', worker.vramMb != null ? formatVram(worker.vramMb) : '—'],
          ['드라이버', worker.driverVersion ?? '—'],
          ['벤치마크', worker.benchmarkTokensPerSec != null ? formatTokensPerSec(worker.benchmarkTokensPerSec) : '측정 전'],
          // 완료/실행을 분모와 함께 — 표본이 적을 때 성공률만 띄우면 오해를 부른다
          ['작업 이력', reliability ? <span key="rel" className="tabular-nums" title={reliability.detail}>{reliability.short}</span> : '—'],
          ['하트비트', heartbeat],
          ['현재 작업', worker.status === 'RUNNING' && worker.currentJobId ? `#${worker.currentJobId}` : '—'],
          ['상태', <span key="work" className={WORK_TONE[work.tone]} title={work.text}>{work.text}</span>],
        ]}
      />
    </li>
  )
}
