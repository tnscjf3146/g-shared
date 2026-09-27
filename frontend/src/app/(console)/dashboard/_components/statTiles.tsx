// 현황판 — 지표 4개를 카드 하나에 담고 사이를 세로 헤어라인(|)으로 나눈다. 선은 위아래 16px 띄워 완전 분리가 아니라 구분 느낌만. 킥커(작은 라벨) · 큰 숫자 · 보조 문구
const DIVIDER = 'before:absolute before:inset-y-4 before:left-0 before:w-px before:bg-hairline'
const LG_DIVIDER = 'lg:before:absolute lg:before:inset-y-4 lg:before:left-0 lg:before:w-px lg:before:bg-hairline'
export function StatTiles({ items, loading }: { items: { label: string; value: string; sub: string }[]; loading: boolean }) {
  return (
    <section className="card animate-enter grid grid-cols-2 gap-0 p-0 lg:grid-cols-4" aria-busy={loading || undefined}>
      {items.map((s, i) => (
        <div key={s.label} className={`relative flex flex-col gap-1.5 px-6 py-5 ${i % 2 === 1 ? DIVIDER : i > 0 ? LG_DIVIDER : ''} ${i >= 2 ? 'border-t border-hairline lg:border-t-0' : ''}`}>
          <span className="text-caption font-semibold tracking-eyebrow text-dark">{s.label}</span>
          {loading ? <span className="h-8 w-24 animate-pulse rounded-xs bg-neutral-tint" /> : <span className="text-h1 leading-none font-bold tabular-nums tracking-heading text-dark">{s.value}</span>}
          <span className="text-caption text-text-muted">{s.sub}</span>
        </div>
      ))}
    </section>
  )
}
