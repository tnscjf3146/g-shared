import { JobListSkeleton } from '@/features/jobs/components/jobSkeleton'

export default function Loading() {
  return (
    <div className="flex w-full max-w-content flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-h1 font-bold tracking-heading text-dark">내 작업</h1>
        <p className="text-[15px] text-text-muted">주문한 GPU 학습 작업과 진행 상태입니다.</p>
      </div>
      <JobListSkeleton />
    </div>
  )
}
