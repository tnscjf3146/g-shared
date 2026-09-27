import Image from 'next/image'
import Link from 'next/link'
import { NavAuth } from '@/features/auth/components/navAuth'

// 랜딩 상단 네비 — 고정 유리 레이어(Hero 가 빠진 뒤 랜딩은 dark 테마만 쓴다). 스타일은 globals.css 의 .nav-glass (밝은/어두운 배경 두 벌, 숨김 슬라이드).
// 어느 배경 위인지(body[data-nav-theme])와 표시 여부(body[data-nav])는 landingScene 이 정한다. 글자·테두리는 currentColor 를 따라 테마에 맞춰 바뀐다
// 배치: 로고 왼쪽 · 우측 버튼(로그인 전=로그인 / 후=대시보드·로그아웃). 가운데 더미 메뉴는 뺐다(2026-09-15)
export function SiteNav() {
  return (
    <header className="nav-glass fixed inset-x-0 top-0 z-50">
      {/* 전체 폭이되 좌우 패딩 4rem(px-16) — 로고·버튼이 랜딩 히어로 카피·챕터 사이드바와 같은 세로선(left:4rem)에 정렬 */}
      <div className="relative flex h-[72px] items-center justify-between px-16">
        <Link href="/" aria-label="G-shared 홈">
          <Image src="/logo-wordmark.png" alt="G-shared" width={116} height={20} priority unoptimized className="nav-logo h-[20px] w-auto" />
        </Link>
        <NavAuth />
      </div>
    </header>
  )
}
