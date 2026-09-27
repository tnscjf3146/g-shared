'use client'

// 목록 페이지 이동 — 작은 위젯은 화살표만(numbered=false, "n / m" 표시), 메인 목록은 페이지 번호 + 화살표
// 데이터는 이미 다 받아 둔 배열을 클라이언트에서 자르는 용도(서버 페이징 아님)
const BTN = 'grid size-7 place-items-center rounded-md text-dark transition-colors hover:bg-black/5 disabled:opacity-40 disabled:hover:bg-transparent'

export function Pager({ page, totalPages, onChange, numbered = false }: { page: number; totalPages: number; onChange: (p: number) => void; numbered?: boolean }) {
  if (totalPages <= 1) return null
  return (
    <nav aria-label="페이지 이동" className="flex items-center gap-1">
      <button type="button" aria-label="이전 페이지" disabled={page === 0} onClick={() => onChange(page - 1)} className={BTN}>
        <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 6l-6 6 6 6" /></svg>
      </button>
      {numbered ? (
        Array.from({ length: totalPages }, (_, i) => (
          <button key={i} type="button" aria-label={`${i + 1}페이지`} aria-current={i === page ? 'page' : undefined} onClick={() => onChange(i)} className={`h-7 min-w-7 rounded-md px-1.5 text-caption tabular-nums transition-colors ${i === page ? 'bg-primary/10 font-semibold text-primary' : 'text-text-muted hover:bg-black/5'}`}>
            {i + 1}
          </button>
        ))
      ) : (
        <span className="px-1 text-caption text-text-muted tabular-nums">{page + 1} / {totalPages}</span>
      )}
      <button type="button" aria-label="다음 페이지" disabled={page >= totalPages - 1} onClick={() => onChange(page + 1)} className={BTN}>
        <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 6l6 6-6 6" /></svg>
      </button>
    </nav>
  )
}
