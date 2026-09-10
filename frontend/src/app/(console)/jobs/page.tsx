import { redirect } from 'next/navigation'

// 목록은 대시보드 [작업] 탭으로 합쳐졌다(2026-09-09). 옛 링크·북마크 호환용 리다이렉트. /jobs/new, /jobs/[id] 는 그대로
export default function Page() {
  redirect('/dashboard/jobs')
}
