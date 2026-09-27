import type { Metadata } from 'next'
import { DashboardView } from '../_components/dashboardView'

export const metadata: Metadata = { title: '작업 — G-shared' }

// /dashboard/jobs — 내가 맡긴 학습 작업(소비자 역할). 사이드바 [작업]
export default function Page() {
  return <DashboardView tab="jobs" />
}
