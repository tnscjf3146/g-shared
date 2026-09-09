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
