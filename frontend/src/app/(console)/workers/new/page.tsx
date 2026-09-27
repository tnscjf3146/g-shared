import type { Metadata } from 'next'
import { WorkerRegisterCard } from '@/features/workers/components/workerRegisterCard'

export const metadata: Metadata = { title: 'GPU 등록 — G-shared' }

// 사이드바 [공급 › GPU 등록하기] · 공급 대시보드 [내 GPU 등록하기]. 실제 등록은 G-shared 앱이 하고, 여기는 안내(worker.md §0·§4)
export default function Page() {
  return (
    <div className="mx-auto flex w-full max-w-[880px] flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-h1 font-bold tracking-heading text-dark">GPU 등록</h1>
        <p className="text-label-sm text-text-muted">PC 에 G-shared 를 받아 실행하고 이 계정으로 로그인하면 그 PC 의 GPU 가 내 워커로 등록됩니다.</p>
      </div>
      <WorkerRegisterCard compact />
    </div>
  )
}
