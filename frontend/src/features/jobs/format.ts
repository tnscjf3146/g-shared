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
  COMPLETED: '학습이 끝났습니다. 아래에서 어댑터를 받을 수 있습니다.',
  FAILED: '학습이 실패했습니다.',
  CANCELLED: '취소된 작업입니다.',
}
/** 아직 끝나지 않은 상태 — 상세 폴링 대상 (CANCELLING 은 환불 확인까지 계속 지켜본다) */
export const ACTIVE_STATUSES: ReadonlySet<JobStatus> = new Set(['CREATED', 'FUNDED', 'ASSIGNED', 'RUNNING', 'CANCELLING'])
/** 사용자가 취소할 수 있는 상태. RUNNING 은 서버는 허용하지만 화면에선 정책상 비노출(2026-09-21) —
 *  정산이 전액 환불뿐이라 실행 중 취소 시 공급자 진행분이 보상되지 않는다. 서버 허용은 비상용으로 유지 */
export const CANCELLABLE_STATUSES: ReadonlySet<JobStatus> = new Set(['CREATED', 'FUNDED', 'ASSIGNED'])

/** 취소 원인 제공자 (back V22 cancel_reason). 사용자가 아닌 원인이면 화면은 "실패"로 말한다 */
export type CancelReason = 'USER_REQUEST' | 'PROVIDER_TIMEOUT' | 'SYSTEM_RECONCILED'

/**
 * 실패·원인까지 구분한 표시용 메타. 상태(status)는 "돈이 어디까지 정리됐나"고,
 * 무엇이 잘못됐는지는 failReason(학습 실패 상세)·cancelReason(원인 제공자)이 말한다.
 * 규칙: 사용자가 시키지 않은 취소는 전부 "실패"로 보여준다 — "취소됨"은 내가 한 것처럼 읽히므로.
 */
export function jobStatusMeta(status: JobStatus, failReason?: string | null, cancelReason?: string | null): { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' | 'primary' } {
  if (failReason) {
    if (status === 'CANCELLING') return { label: '실패 · 환불 처리 중', tone: 'danger' }
    if (status === 'CANCELLED') return { label: '실패 (환불 완료)', tone: 'danger' }
  }
  if (status === 'CANCELLED' && cancelReason && cancelReason !== 'USER_REQUEST') {
    return cancelReason === 'PROVIDER_TIMEOUT'
      ? { label: '실패 · 공급자 무응답 (환불 완료)', tone: 'danger' }
      : { label: '실패 · 시스템 확인 (환불 완료)', tone: 'danger' }
  }
  return STATUS_META[status]
}

/** 실패·원인까지 구분한 상세 한 문장 (jobStatusMeta 와 같은 조합 규칙). 모든 취소 문장은 환불 사실을 함께 말한다 */
export function jobStatusSentence(status: JobStatus, failReason?: string | null, cancelReason?: string | null): string {
  if (failReason) {
    if (status === 'CANCELLING') return '학습이 실패했습니다. 예치금 환불을 처리하는 중입니다 — 완료되면 자동으로 반영됩니다.'
    if (status === 'CANCELLED') return '학습이 실패해 예치금이 환불됐습니다.'
  }
  if (status === 'CANCELLED' && cancelReason) {
    if (cancelReason === 'USER_REQUEST') return '요청하신 취소가 완료되었습니다. 예치금이 환불되었습니다.'
    if (cancelReason === 'PROVIDER_TIMEOUT') return '공급자가 응답하지 않아 작업이 진행되지 못했습니다. 예치금은 전액 자동 환불되었습니다.'
    // SYSTEM_RECONCILED 와 미래에 추가될 미지 코드 — meta 가 '실패'로 칠하므로 문장도 같은 결로 (라벨-문장 모순 방지)
    return '확인 과정에서 작업이 정리되었습니다. 예치금은 환불되었습니다.'
  }
  // cancelReason 이 NULL(=V22 이전 과거 행)이면 기존 '취소된 작업입니다.' — 과거 건은 소급하지 않는다(결정 2026-09-21)
  return STATUS_SENTENCE[status]
}

export const AUTO_MODE_META: Record<AutoMode, { label: string; description: string }> = {
  SPEED: { label: '빠르게', description: '결과를 빨리 봅니다.' },
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

// ── 어댑터(학습 결과물) 이름·버전 ────────────────────────────────
// 이름은 다운로드 파일 이름이 되고, 버전은 "이어서 학습"을 몇 번 쌓았는지다(처음 학습 v1 → 이어서 v2 → v3).
// 서버가 확정값을 주지만(JobDetail.adapterName/adapterVersion) 반영 전에는 비어 오므로 화면은 항상 대체값을 갖는다.

/** 베이스 모델 이름 → 어댑터 이름 기본값 제안. 사용자가 비워 두면 이 값으로 등록한다 */
export function suggestAdapterName(modelName: string | undefined): string {
  const slug = (modelName ?? 'adapter')
    .trim()
    .replace(/[^가-힣a-zA-Z0-9._-]+/g, '-')
    .replace(/^[._-]+|[._-]+$/g, '') // 이름은 한글·영숫자로 시작해야 한다(adapterNameSchema)
    .slice(0, 50)
  return `${slug || 'adapter'}-lora`
}

/** 표시용 "이름 v2" — 이름이 없으면 작업 번호로 대신한다 */
export function adapterLabel(jobId: number, name?: string | null, version?: number | null): string {
  return `${name?.trim() || `작업 #${jobId} 어댑터`}${version ? ` v${version}` : ''}`
}

/** 다운로드 파일 이름 — 이름-v2 + 결과 URL 의 확장자. 확장자를 못 읽으면 붙이지 않는다(브라우저가 정한다).
 *  ponytail: 같은 오리진일 때만 <a download> 가 먹는다 — 외부 스토리지면 서버 Content-Disposition 이 정본(job.md §11) */
export function adapterFileName(jobId: number, name: string | null | undefined, version: number | null | undefined, resultUrl: string): string {
  const stem = `${(name?.trim() || `job-${jobId}-adapter`).replace(/\s+/g, '-')}${version ? `-v${version}` : ''}`
  const path = resultUrl.split(/[?#]/)[0]
  const ext = /\.([a-zA-Z0-9]{1,12}(?:\.(?:gz|zst|bz2))?)$/.exec(path)?.[1]
  return ext ? `${stem}.${ext}` : stem
}
