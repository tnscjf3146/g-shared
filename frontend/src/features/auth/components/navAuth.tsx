'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { logout } from '../api'
import { useAuth } from '../store'

// 네비 우측: 마이페이지 아이콘 하나(프론트 네브바.png). guest → 클릭 시 /login, authed → 클릭 시 [대시보드][로그아웃] 드롭다운
// 지갑은 대시보드 안으로 들어가 네비에서 뺐다(2026-09-10 결정). URL 로 직접 오면 proxy.ts 가 /login?next= 로 보낸다
// loading 중엔 깜빡임 방지로 guest 아이콘(로그인 링크) 유지
function UserIcon() {
  return (
    <svg aria-hidden width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="8" r="4" />
      <path d="M4 20c0-4 3.5-6 8-6s8 2 8 6" />
    </svg>
  )
}

export function NavAuth() {
  const { status } = useAuth()

  if (status !== 'authed') {
    return (
      <Link href="/login" aria-label="로그인" className="nav-btn !px-2">
        <UserIcon />
      </Link>
    )
  }
  return <AuthedMenu />
}

function AuthedMenu() {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState(false)

  return (
    <div className="relative">
      <button type="button" aria-label="내 메뉴" aria-expanded={open} className="nav-btn !px-2" onClick={() => setOpen((v) => !v)}>
        <UserIcon />
      </button>

      {open && (
        <>
          {/* 바깥 클릭 시 닫힘 — 리스너 대신 투명 백드롭 (ponytail: 작은 메뉴엔 이걸로 충분) */}
          <button type="button" aria-hidden tabIndex={-1} className="fixed inset-0 z-40 cursor-default" onClick={() => setOpen(false)} />
          <div className="animate-enter-spring-l3 absolute right-0 top-[calc(100%+8px)] z-50 flex w-40 flex-col gap-1 rounded-xl border border-[var(--color-hairline)] bg-[var(--color-surface-2)] p-1.5 shadow-[var(--shadow-3)]">
            <Link href="/dashboard" className="rounded-md px-3 py-2 text-left text-sm font-medium text-[var(--color-dark)] transition-colors hover:bg-[var(--color-white-dim)]" onClick={() => setOpen(false)}>
              대시보드
            </Link>
            <button
              type="button"
              disabled={pending}
              className="rounded-md px-3 py-2 text-left text-sm font-medium text-[var(--color-dark)] transition-colors hover:bg-[var(--color-white-dim)] disabled:opacity-60"
              onClick={async () => {
                setPending(true)
                await logout()
                setOpen(false)
                setPending(false)
                router.refresh()
              }}
            >
              {pending ? '로그아웃 중…' : '로그아웃'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
