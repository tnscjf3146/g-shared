'use client'

import { useRouter } from 'next/navigation'
import { useEffect } from 'react'
import { hasSession, reissue, SESSION_FLAG } from '../api'
import { safeNextPath } from '../next-path'
import { isProtectedPath } from '../protected-paths'
import { authStore } from '../store'

// 앱 진입 시 1회: refresh 쿠키로 access 토큰을 복구한다(있으면 authed, 없으면 guest). 그동안 status='loading'.
// 다중 탭: 세션 표시(localStorage)는 탭 간에 공유되고 바뀔 때 다른 탭에 storage 이벤트가 온다 →
//   다른 탭 로그아웃(표시 제거) = 이 탭도 메모리 토큰을 비워 guest / 다른 탭 로그인(표시 생성) = 이 탭도 reissue 로 authed
// 보호 화면 가드(콘솔 AuthGate 와 분담): authed 였다가 세션이 없어지는 경우 중 다른 탭 로그아웃과 bfcache 복원(요청 없이 옛 화면이 그대로 돌아옴)은
//   여기서 로그인으로 보낸다. 세션 없음이 확정된 때만(401 로 표시까지 지워짐) — 일시 장애(네트워크·429·5xx)는 표시가 남아 있으니 보내지 않는다
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  useEffect(() => {
    const guard = (token: string | null) => {
      if (token || hasSession() || !isProtectedPath(location.pathname)) return
      router.replace(`/login?next=${encodeURIComponent(safeNextPath(location.pathname + location.search))}&reason=expired`)
    }
    void reissue() // 처음부터 guest 인 콘솔 진입은 AuthGate 가 보낸다
    const onStorage = (e: StorageEvent) => {
      if (e.key !== SESSION_FLAG && e.key !== null) return // null = storage.clear()
      if (e.newValue === '1') void reissue()
      else {
        authStore.signOut()
        guard(null)
      }
    }
    const onPageShow = (e: PageTransitionEvent) => {
      if (e.persisted) void reissue().then(guard) // bfcache 복원 — 마운트 effect 가 다시 안 돌므로 세션을 다시 확인
    }
    addEventListener('storage', onStorage)
    addEventListener('pageshow', onPageShow)
    return () => {
      removeEventListener('storage', onStorage)
      removeEventListener('pageshow', onPageShow)
    }
  }, [router])
  return children
}
