// 표시용 포맷 — 전부 표준 라이브러리(Intl). 라이브러리 없음

const rtf = new Intl.RelativeTimeFormat('ko', { numeric: 'auto' })
const STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['second', 60],
  ['minute', 60],
  ['hour', 24],
  ['day', Infinity],
]

/** ISO 시각 → "20초 전" / "2시간 전". 미래·파싱 실패는 "방금" */
export function relativeTime(iso: string, now = Date.now()): string {
  let diff = Math.round((now - Date.parse(iso)) / 1000)
  if (!Number.isFinite(diff) || diff < 5) return '방금'
  for (const [unit, size] of STEPS) {
    if (diff < size) return rtf.format(-diff, unit)
    diff = Math.round(diff / size)
  }
  return rtf.format(-diff, 'day')
}

/** MB → "12 GB"(1024 단위, 소수 없음). 1GB 미만은 MB 그대로 */
export function formatVram(mb: number): string {
  return mb >= 1024 ? `${Math.round(mb / 1024)} GB` : `${mb} MB`
}

/** 벤치마크 → "1,234 tok/s" */
export function formatTokensPerSec(v: number): string {
  return `${Math.round(v).toLocaleString('ko-KR')} tok/s`
}

/** 작업 이력 — 성공률만 띄우면 "몇 건 중에요?"가 바로 나온다. **분모를 항상 같이 보여 준다.**
 *  실행 0건은 `신규`(성공률 없음) — 신규 공급자를 "성공률 0%"로 낙인찍지 않는다(스펙 §6).
 *  백엔드가 아직 이력 필드를 안 주는 서버면 null 을 돌려 화면이 칸을 비운다. */
export function formatReliability(w: { completedCount?: number; totalExecutionCount?: number; successRate?: number | null }):
  | { short: string; detail: string }
  | null {
  const total = w.totalExecutionCount
  if (total == null) return null // 이 서버는 이력을 주지 않는다
  if (total === 0) return { short: '신규', detail: '아직 수행한 작업이 없습니다.' }
  const done = w.completedCount ?? 0
  const rate = w.successRate != null ? `${Math.round(w.successRate * 100)}%` : null
  // 성공률을 **분모와 함께** 한 줄에 — 둘 중 하나만 있으면 오해를 부른다.
  // 분모만: "좋은 건가?" / 성공률만: "몇 건 중에요?"(요청서 지적)
  // 표 한 칸에 들어가야 해서 슬래시 주변 공백은 뺀다 — "2 / 2 · 100%" 는 칸을 넘겨 잘렸다
  return {
    short: rate ? `${done}/${total} · ${rate}` : `${done}/${total}`,
    detail: `완료 ${done}건 · 실행 ${total}건 · 성공률 ${rate ?? '—'}`,
  }
}
