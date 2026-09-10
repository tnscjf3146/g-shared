// 대시보드(작업·공급) 로딩 — 제목 · 현황판 카드 · 목록 카드 4개. 하위 세그먼트(/jobs·/workers)에도 적용
export default function Loading() {
  return (
    <>
      <div className="h-9 w-24 animate-pulse rounded-xs bg-neutral-tint" />
      <div className="card h-[116px] animate-pulse" />
      <div className="h-8 w-32 animate-pulse rounded-xs bg-neutral-tint" />
      <div className="flex flex-col gap-3">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="card h-[76px] animate-pulse" />
        ))}
      </div>
    </>
  )
}
