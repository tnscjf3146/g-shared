import type { ReactNode } from 'react'

// 상태 필(pill) — AGENTS.md "상태 컬러(StatusBadge)" 토큰. pill 은 상태/선택 표시에만 쓴다(버튼 금지). 서버 컴포넌트에서 그대로 사용 가능
type Tone = 'success' | 'warning' | 'danger' | 'neutral' | 'primary'

// Tailwind v4 는 소스에 글자 그대로 있는 클래스만 뽑는다 → 정적 맵
const TONE: Record<Tone, string> = {
  success: 'bg-success-tint text-success',
  warning: 'bg-warning-tint text-warning',
  danger: 'bg-danger-tint text-danger',
  neutral: 'bg-neutral-tint text-neutral',
  primary: 'bg-primary/10 text-primary',
}

export function StatusBadge({ tone, children, className = '' }: { tone: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-pill px-3 py-1 text-[12px] font-semibold ${TONE[tone]} ${className}`}>
      <span aria-hidden className="size-1.5 rounded-pill bg-current" />
      {children}
    </span>
  )
}
