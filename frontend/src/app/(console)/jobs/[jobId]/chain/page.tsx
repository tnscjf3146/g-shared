import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { ChainFlow } from './_components/chainFlow'

// /jobs/{jobId}/chain — 이 작업의 블록체인 흐름을 한눈에. Next 16: params 는 Promise
type Props = { params: Promise<{ jobId: string }> }

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { jobId } = await params
  return { title: `작업 #${jobId} 흐름 — G-shared` }
}

export default async function Page({ params }: Props) {
  const { jobId } = await params
  const id = Number(jobId)
  if (!Number.isInteger(id) || id <= 0) notFound()
  return (
    <div className="flex w-full max-w-content flex-col gap-8">
      <ChainFlow jobId={id} />
    </div>
  )
}
