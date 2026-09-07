'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { logout } from '../api'
import { useAuth } from '../store'

// 네비 우측: guest → 로그인, authed → 로그아웃. 둘 다 같은 회색 헤어라인 버튼(globals.css .nav-btn), 아이콘 방향만 다름
// loading 중엔 깜빡임 방지로 로그인 버튼 유지
export function NavAuth() {
  const { status } = useAuth()
  const router = useRouter()
  const [pending, setPending] = useState(false)

  if (status !== 'authed') {
    return (
      <Link href="/login" className="nav-btn">
        로그인
        <svg aria-hidden width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 3h5a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H6" />
          <path d="M2 8h7M7 5.5 9.5 8 7 10.5" />
        </svg>
      </Link>
    )
  }
  return (
    <button
      type="button"
      className="nav-btn"
      disabled={pending}
      onClick={async () => {
        setPending(true)
        await logout()
        setPending(false)
        router.refresh()
      }}
    >
      <svg aria-hidden width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M10 3H5a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h5" />
        <path d="M7 8h7M11.5 5.5 14 8l-2.5 2.5" />
      </svg>
      {pending ? '로그아웃 중…' : '로그아웃'}
    </button>
  )
}
