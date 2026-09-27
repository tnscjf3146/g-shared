import type { Metadata } from 'next'
import { DashboardView } from '../_components/dashboardView'

export const metadata: Metadata = { title: '공급 — G-shared' }

// /dashboard/workers — 내 PC(워커) 목록(공급자 역할). 사이드바 [공급]
export default function Page() {
  return <DashboardView tab="workers" />
}
