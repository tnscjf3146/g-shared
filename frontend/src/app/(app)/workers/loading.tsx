import { WorkerSkeleton } from '@/features/workers/components/workerSkeleton'

export default function Loading() {
  return (
    <div className="flex w-full max-w-content flex-col items-center gap-8">
      <div className="flex w-full flex-col items-center gap-2">
        <h1 className="text-h1 font-bold tracking-heading text-dark">내 워커</h1>
        <p className="text-[15px] text-text-muted">이 계정에 등록된 GPU PC 와 연결 상태입니다.</p>
      </div>
      <WorkerSkeleton />
    </div>
  )
}
