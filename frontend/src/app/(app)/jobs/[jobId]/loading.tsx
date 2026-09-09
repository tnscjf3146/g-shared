import { JobDetailSkeleton } from '@/features/jobs/components/jobSkeleton'

export default function Loading() {
  return (
    <div className="flex w-full max-w-content flex-col gap-8">
      <JobDetailSkeleton />
    </div>
  )
}
