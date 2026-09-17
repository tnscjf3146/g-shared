import type { AutoMode, JobStatus } from './types'

// 표시용 포맷·라벨 — 표준 라이브러리(Intl)만

/** 상태 → 라벨·배지 톤 (job.md §5) */
export const STATUS_META: Record<JobStatus, { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' | 'primary' }> = {
  CREATED: { label: '예치 대기', tone: 'warning' },
  FUNDED: { label: '배정 대기', tone: 'neutral' },
  ASSIGNED: { label: '시작 대기', tone: 'neutral' },
  RUNNING: { label: '학습 중', tone: 'primary' },
  CANCELLING: { label: '취소 중', tone: 'warning' },
  COMPLETED: { label: '완료', tone: 'success' },
  FAILED: { label: '실패', tone: 'danger' },
  CANCELLED: { label: '취소됨', tone: 'neutral' },
}
/** 상태 → 상세에 보이는 한 문장 */
export const STATUS_SENTENCE: Record<JobStatus, string> = {
  CREATED: '예치 대기 — 아래에서 GSC를 예치하면 워커 배정이 시작됩니다.',
  FUNDED: '예치 확인됨. 조건에 맞는 워커에 배정되는 중입니다.',
  ASSIGNED: '워커에 배정됐습니다. 곧 학습이 시작됩니다.',
  RUNNING: '학습이 진행 중입니다.',
  CANCELLING: '정리 중입니다. 예치금 처리가 확인되면 종료 상태로 바뀝니다.',
  COMPLETED: '학습이 끝났습니다. 아래에서 결과를 받을 수 있습니다.',
  FAILED: '학습이 실패했습니다.',
  CANCELLED: '취소된 작업입니다.',
}
/** 아직 끝나지 않은 상태 — 상세 폴링 대상 (CANCELLING 은 환불 확인까지 계속 지켜본다) */
export const ACTIVE_STATUSES: ReadonlySet<JobStatus> = new Set(['CREATED', 'FUNDED', 'ASSIGNED', 'RUNNING', 'CANCELLING'])
/** 사용자가 취소할 수 있는 상태(back JobService.cancelJob 과 동일) — CANCELLING 은 이미 취소 중이라 제외 */
export const CANCELLABLE_STATUSES: ReadonlySet<JobStatus> = new Set(['CREATED', 'FUNDED', 'ASSIGNED'])

/**
 * 실패 여부까지 구분한 표시용 메타. 상태(status)는 "돈이 어디까지 정리됐나"고,
 * 실패했는지는 failReason 의 유무가 말한다 — 실패한 작업이 환불 정리를 거치면
 * CANCELLING→CANCELLED 로 끝나므로, 라벨은 두 정보를 조합해야 정직하다.
 */
export function jobStatusMeta(status: JobStatus, failReason?: string | null): { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' | 'primary' } {
  if (failReason) {
    if (status === 'CANCELLING') return { label: '실패 · 환불 처리 중', tone: 'danger' }
    if (status === 'CANCELLED') return { label: '실패 (환불 완료)', tone: 'danger' }
  }
  return STATUS_META[status]
}

/** 실패 여부까지 구분한 상세 한 문장 (jobStatusMeta 와 같은 조합 규칙) */
export function jobStatusSentence(status: JobStatus, failReason?: string | null): string {
  if (failReason) {
    if (status === 'CANCELLING') return '학습이 실패했습니다. 예치금 환불을 처리하는 중입니다 — 완료되면 자동으로 반영됩니다.'
    if (status === 'CANCELLED') return '학습이 실패해 예치금이 환불됐습니다.'
  }
  return STATUS_SENTENCE[status]
}

export const AUTO_MODE_META: Record<AutoMode, { label: string; description: string }> = {
  SPEED: { label: '빠르게', description: '결과를 빨리 봅니다. 1 epoch, 짧게 자른 시퀀스.' },
  BALANCED: { label: '균형', description: '특별한 이유가 없을 때의 선택.' },
  TRAINING: { label: '충분히', description: '오래 걸려도 데이터를 더 많이, 더 여러 번 봅니다.' },
}

/** 초 → "1시간 20분" / "45분" / "30초" */
export function formatDuration(sec: number): string {
  const s = Math.max(0, Math.round(sec))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h > 0) return m > 0 ? `${h}시간 ${m}분` : `${h}시간`
  if (m > 0) return `${m}분`
  return `${s}초`
}

/** 비용(GSC 정수) → "1,200 GSC" */
export function formatCost(n: number): string {
  return `${Math.round(n).toLocaleString('ko-KR')} GSC`
}

const dtf = new Intl.DateTimeFormat('ko-KR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
/** LocalDateTime 문자열(Z 없음 → 로컬 시간으로 해석) → "9월 9일 10:00" */
export function formatDateTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : dtf.format(d)
}

/** 바이트 → "12.3 MB" */
export function formatBytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${Math.round(n / 1024)} KB`
}
