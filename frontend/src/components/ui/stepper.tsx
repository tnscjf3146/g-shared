// 단계 표시 — 번호 원 + 라벨. 완료(체크)·현재(프라이머리)·이후(회색). 클릭은 완료한 단계로 돌아갈 때만
type Step = { label: string; note?: string }

export function Stepper({ steps, current, maxReached, onSelect }: { steps: Step[]; current: number; maxReached: number; onSelect?: (i: number) => void }) {
  return (
    <ol className="flex w-full items-start gap-2" aria-label="진행 단계">
      {steps.map((s, i) => {
        const done = i < current
        const active = i === current
        const reachable = i <= maxReached && !!onSelect
        return (
          <li key={s.label} className="flex flex-1 flex-col items-center gap-2">
            <div className="flex w-full items-center">
              <span aria-hidden className={`h-px flex-1 ${i === 0 ? 'bg-transparent' : done || active ? 'bg-primary' : 'bg-hairline-strong'}`} />
              <button
                type="button"
                disabled={!reachable}
                onClick={() => onSelect?.(i)}
                aria-current={active ? 'step' : undefined}
                className={`flex size-8 shrink-0 items-center justify-center rounded-pill text-caption font-bold tabular-nums transition-colors ${
                  active ? 'bg-primary text-white shadow-glow-primary' : done ? 'bg-primary/15 text-primary' : 'bg-neutral-tint text-text-muted'
                } ${reachable ? 'cursor-pointer' : 'cursor-default'}`}
              >
                {done ? (
                  <svg aria-label="완료" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="m3 8.5 3 3 7-7" />
                  </svg>
                ) : (
                  i + 1
                )}
              </button>
              <span aria-hidden className={`h-px flex-1 ${i === steps.length - 1 ? 'bg-transparent' : done ? 'bg-primary' : 'bg-hairline-strong'}`} />
            </div>
            <div className="flex flex-col items-center gap-0.5">
              <span className={`text-caption font-semibold ${active ? 'text-dark' : 'text-text-muted'}`}>{s.label}</span>
              {s.note && <span className="rounded-xs bg-neutral-tint px-1.5 py-0.5 text-micro text-text-muted">{s.note}</span>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
