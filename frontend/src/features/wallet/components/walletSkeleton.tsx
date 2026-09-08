// 카드 자리 스켈레톤 — 라우트 loading.tsx 와 WalletPage 의 조회 중 상태가 같이 쓴다('use client' 불필요)
export function WalletSkeleton() {
  return (
    <section aria-busy aria-label="불러오는 중" className="card flex w-[520px] max-w-full flex-col gap-6 p-10">
      <div className="h-7 w-40 animate-pulse rounded-xs bg-neutral-tint" />
      <div className="h-4 w-full animate-pulse rounded-xs bg-neutral-tint" />
      <div className="h-[46px] w-full animate-pulse rounded-md bg-neutral-tint" />
    </section>
  )
}
