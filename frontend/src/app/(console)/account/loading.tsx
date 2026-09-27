// 내 계정 로딩 — 제목 · 카드 2개
export default function Loading() {
  return (
    <>
      <div className="h-9 w-28 animate-pulse rounded-xs bg-neutral-tint" />
      <div className="card h-[220px] animate-pulse" />
      <div className="card h-[120px] animate-pulse" />
    </>
  )
}
