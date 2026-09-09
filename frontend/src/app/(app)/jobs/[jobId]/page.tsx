import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { JobDetailPage } from '@/features/jobs/components/jobDetailPage'

// Next 16: params 는 Promise. 숫자가 아니면 404 로 (백엔드 id 는 Long)
type Props = { params: Promise<{ jobId: string }> }

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { jobId } = await params
  return { title: `작업 #${jobId} — G-shared` }
}

export default async function Page({ params }: Props) {
  const { jobId } = await params
  const id = Number(jobId)
  if (!Number.isInteger(id) || id <= 0) notFound()
  return (
    <div className="flex w-full max-w-content flex-col gap-8">
      <JobDetailPage jobId={id} />
    </div>
  )
}
