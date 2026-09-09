import type { Metadata } from 'next'
import { WorkloadWizard } from '@/features/jobs/components/workloadWizard'

export const metadata: Metadata = { title: '워크로드 등록 — G-shared' }

export default function Page() {
  return (
    <div className="flex w-full max-w-[880px] flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-h1 font-bold tracking-heading text-dark">워크로드 등록</h1>
        <p className="text-[15px] text-text-muted">무엇을 어떤 모델과 데이터로 돌릴지 단계별로 고르고, GPU 등급과 견적을 확인하면 주문이 만들어집니다.</p>
      </div>
      <WorkloadWizard />
    </div>
  )
}
