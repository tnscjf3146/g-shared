// 진행률 바 — <progress> 는 브라우저별 스타일 편차가 커서 div 2개 + aria. 서버 컴포넌트에서도 사용 가능
export function ProgressBar({ value, className = '' }: { value: number; className?: string }) {
  const v = Math.max(0, Math.min(100, Math.round(value)))
  return (
    <div role="progressbar" aria-valuenow={v} aria-valuemin={0} aria-valuemax={100} className={`h-2 w-full overflow-hidden rounded-pill bg-neutral-tint ${className}`}>
      <div className="h-full rounded-pill bg-primary transition-[width] duration-500 ease-out-expo" style={{ width: `${v}%` }} />
    </div>
  )
}
