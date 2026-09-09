import type { AutoMode, JobStatus } from './types'

// 표시용 포맷·라벨 — 표준 라이브러리(Intl)만

/** 상태 → 라벨·배지 톤 (job.md §5) */
export const STATUS_META: Record<JobStatus, { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' | 'primary' }> = {
  CREATED: { label: '예치 대기', tone: 'warning' },
  FUNDED: { label: '배정 대기', tone: 'neutral' },
  ASSIGNED: { label: '시작 대기', tone: 'neutral' },
  RUNNING: { label: '학습 중', tone: 'primary' },
  COMPLETED: { label: '완료', tone: 'success' },
  FAILED: { label: '실패', tone: 'danger' },
  CANCELLED: { label: '취소됨', tone: 'neutral' },
}
/** 아직 끝나지 않은 상태 — 상세 폴링 대상 */
export const ACTIVE_STATUSES: ReadonlySet<JobStatus> = new Set(['CREATED', 'FUNDED', 'ASSIGNED', 'RUNNING'])
/** 사용자가 취소할 수 있는 상태(back JobService.cancelJob 과 동일) */
export const CANCELLABLE_STATUSES: ReadonlySet<JobStatus> = new Set(['CREATED', 'FUNDED', 'ASSIGNED'])

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
