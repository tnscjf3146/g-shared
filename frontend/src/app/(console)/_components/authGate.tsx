'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useRef } from 'react'
import { safeNextPath } from '@/features/auth/next-path'
import { useAuth } from '@/features/auth/store'

// 콘솔 셸의 클라이언트 세션 가드. proxy.ts 는 서버 요청의 쿠키 존재만 보는데, 뒤로가기로 콘솔 항목에 돌아오면 Next 라우터가 서버를 안 거치고
// 클라이언트에서 복원하므로(로그아웃 뒤에도) 여기서 잡는다. 쿠키만 남은 채 들어온 경우(loading → guest)도 같다.
// guest 인 동안은 셸을 그리지 않고, 처음부터 guest 였으면 로그인으로. authed 였다가 guest 가 된 건 로그아웃(사이드바가 홈으로 보냄)·
// 다른 탭 로그아웃·bfcache 복원(authProvider 가 보냄)이라 여기서 보내지 않는다 — 홈으로 가는 로그아웃과 경쟁하지 않게
export function AuthGate({ children }: { children: React.ReactNode }) {
  const { status } = useAuth()
  const router = useRouter()
  const wasAuthed = useRef(false)
  useEffect(() => {
    if (status === 'authed') wasAuthed.current = true
    if (status === 'guest' && !wasAuthed.current) router.replace(`/login?next=${encodeURIComponent(safeNextPath(location.pathname + location.search))}&reason=expired`)
  }, [status, router])
  return status === 'guest' ? null : children
}
