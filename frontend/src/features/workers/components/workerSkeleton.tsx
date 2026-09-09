// 카드 2개 자리 스켈레톤 — 라우트 loading.tsx 와 WorkersPage 의 조회 중 상태가 같이 쓴다('use client' 불필요)
export function WorkerSkeleton() {
  return (
    <div aria-busy aria-label="불러오는 중" className="grid w-full grid-cols-1 gap-6 md:grid-cols-2">
      {[0, 1].map((i) => (
        <section key={i} className="card flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <div className="h-6 w-40 animate-pulse rounded-xs bg-neutral-tint" />
            <div className="h-6 w-16 animate-pulse rounded-pill bg-neutral-tint" />
          </div>
          <div className="h-4 w-3/4 animate-pulse rounded-xs bg-neutral-tint" />
          <div className="h-4 w-1/2 animate-pulse rounded-xs bg-neutral-tint" />
        </section>
      ))}
    </div>
  )
}
