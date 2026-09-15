'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { logout } from '../api'
import { useAuth } from '../store'

// 네비 우측(2026-09-15): guest → [로그인] 글자. authed → [마이페이지 아이콘][로그아웃 아이콘].
// 테두리·배경 없는 텍스트/아이콘 버튼 — currentColor 라 다크·라이트 테마 자동 대응, hover 로 불투명도만
// 터치 타깃 최소 44px 확보(size-11) — 아이콘은 가운데. hover 로 불투명도만
const ICON_BTN = 'inline-flex size-11 items-center justify-center rounded-md opacity-70 transition-opacity hover:opacity-100 disabled:opacity-40'
const TEXT_BTN = 'text-[15px] font-medium opacity-75 transition-opacity hover:opacity-100'
function UserIcon() {
  return (
    <svg aria-hidden width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="8" r="4" />
      <path d="M4 20c0-4 3.5-6 8-6s8 2 8 6" />
    </svg>
  )
}
function LogoutIcon() {
  return (
    <svg aria-hidden width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M15 4H6a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h9" />
      <path d="M10 12h11M17.5 8.5 21 12l-3.5 3.5" />
    </svg>
  )
}

export function NavAuth() {
  const { status } = useAuth()
  const router = useRouter()
  const [pending, setPending] = useState(false)

  if (status !== 'authed') {
    return (
      <Link href="/login" className={TEXT_BTN}>
        로그인
      </Link>
    )
  }

  return (
    <div className="flex items-center gap-1">
      <Link href="/dashboard" aria-label="대시보드" title="대시보드" className={ICON_BTN}>
        <UserIcon />
      </Link>
      <button
        type="button"
        aria-label="로그아웃"
        title="로그아웃"
        disabled={pending}
        className={ICON_BTN}
        onClick={async () => {
          setPending(true)
          try {
            await logout() // logout 은 내부에서 실패해도 로컬 세션을 비운다(auth/api). 그래도 네트워크 예외로 throw 될 수 있어 방어
            router.refresh() // 성공 시 리렌더 → guest 로. 성공 경로에선 pending 을 내리지 않는다(전환 완료까지 로딩 유지)
          } catch {
            setPending(false) // 실패 시 다시 시도할 수 있게 버튼 복구
          }
        }}
      >
        <LogoutIcon />
      </button>
    </div>
  )
}
