// 목록/상세 자리 스켈레톤 — loading.tsx 와 조회 중 상태가 같이 쓴다('use client' 불필요)
export function JobListSkeleton() {
  return (
    <div aria-busy aria-label="불러오는 중" className="flex w-full flex-col gap-3">
      {[0, 1, 2].map((i) => (
        <div key={i} className="card flex items-center justify-between gap-4 py-4">
          <div className="h-5 w-48 animate-pulse rounded-xs bg-neutral-tint" />
          <div className="h-6 w-16 animate-pulse rounded-pill bg-neutral-tint" />
        </div>
      ))}
    </div>
  )
}

export function JobDetailSkeleton() {
  return (
    <div aria-busy aria-label="불러오는 중" className="flex w-full flex-col gap-6">
      <div className="h-9 w-72 animate-pulse rounded-xs bg-neutral-tint" />
      <div className="card h-[104px] animate-pulse" />
      <div className="card h-[140px] animate-pulse" />
      <div className="card h-[140px] animate-pulse" />
    </div>
  )
}
