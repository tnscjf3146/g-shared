'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { logout } from '../api'
import { useAuth } from '../store'

// 네비 우측: guest → [로그인] / authed → [작업][워커][지갑][로그아웃]. 전부 같은 회색 헤어라인 버튼(globals.css .nav-btn)
// 작업·워커·지갑 탭은 로그인한 뒤에만 보인다(2026-09-08 결정). URL 로 직접 오면 proxy.ts 가 /login?next= 로 보낸다
// loading 중엔 깜빡임 방지로 로그인 버튼 유지
export function NavAuth() {
  const { status } = useAuth()
  const router = useRouter()
  const [pending, setPending] = useState(false)

  if (status !== 'authed') return <LoginLink />
  return (
    <>
      <Link href="/jobs" className="nav-btn">
        <svg aria-hidden width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 3h10v10H3z" />
          <path d="M6 8h4M6 10.5h2.5M6 5.5h4" />
        </svg>
        작업
      </Link>
      <Link href="/workers" className="nav-btn">
        <svg aria-hidden width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="3" width="12" height="7" rx="1.5" />
          <path d="M5 13h6M8 10v3" />
        </svg>
        워커
      </Link>
      <Link href="/wallet" className="nav-btn">
        <svg aria-hidden width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="4" width="12" height="9" rx="1.5" />
          <path d="M2 7h12M10.5 10.5h1" />
        </svg>
        지갑
      </Link>
      <LogoutButton pending={pending} setPending={setPending} onDone={() => router.refresh()} />
    </>
  )
}

function LoginLink() {
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

function LogoutButton({ pending, setPending, onDone }: { pending: boolean; setPending: (v: boolean) => void; onDone: () => void }) {
  return (
    <button
      type="button"
      className="nav-btn"
      disabled={pending}
      onClick={async () => {
        setPending(true)
        await logout()
        setPending(false)
        onDone()
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
