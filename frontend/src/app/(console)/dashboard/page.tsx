import { redirect } from 'next/navigation'

// /dashboard 는 /dashboard/jobs 로. 옛 ?tab=workers 주소(2026-09-10 이전 링크·북마크)는 /dashboard/workers 로 호환
export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string }> }) {
  const { tab } = await searchParams
  redirect(tab === 'workers' ? '/dashboard/workers' : '/dashboard/jobs')
}
