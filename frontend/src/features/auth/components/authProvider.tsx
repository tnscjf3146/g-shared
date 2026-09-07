'use client'

import { useEffect } from 'react'
import { reissue } from '../api'

// 앱 진입 시 1회: refresh 쿠키로 access 토큰을 복구한다(있으면 authed, 없으면 guest). 그동안 status='loading'.
export function AuthProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    void reissue()
  }, [])
  return children
}
