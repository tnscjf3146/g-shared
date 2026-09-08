'use client'

import { useEffect } from 'react'
import { reissue, SESSION_FLAG } from '../api'
import { authStore } from '../store'

// 앱 진입 시 1회: refresh 쿠키로 access 토큰을 복구한다(있으면 authed, 없으면 guest). 그동안 status='loading'.
// 다중 탭: 세션 표시(localStorage)는 탭 간에 공유되고 바뀔 때 다른 탭에 storage 이벤트가 온다 →
//   다른 탭 로그아웃(표시 제거) = 이 탭도 메모리 토큰을 비워 guest / 다른 탭 로그인(표시 생성) = 이 탭도 reissue 로 authed
export function AuthProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    void reissue()
    const onStorage = (e: StorageEvent) => {
      if (e.key !== SESSION_FLAG && e.key !== null) return // null = storage.clear()
      if (e.newValue === '1') void reissue()
      else authStore.signOut()
    }
    addEventListener('storage', onStorage)
    return () => removeEventListener('storage', onStorage)
  }, [])
  return children
}
