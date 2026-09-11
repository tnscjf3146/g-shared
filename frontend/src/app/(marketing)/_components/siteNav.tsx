import Image from 'next/image'
import Link from 'next/link'
import { NavAuth } from '@/features/auth/components/navAuth'

// 랜딩 상단 네비 — 고정 유리 레이어(Hero 가 빠진 뒤 랜딩은 dark 테마만 쓴다). 스타일은 globals.css 의 .nav-glass (밝은/어두운 배경 두 벌, 숨김 슬라이드).
// 어느 배경 위인지(body[data-nav-theme])와 표시 여부(body[data-nav])는 landingScene 이 정한다. 글자·테두리는 currentColor 를 따라 테마에 맞춰 바뀐다
// 배치: 로고 왼쪽 · 메뉴 가운데(absolute 로 화면 정중앙 고정) · 마이페이지 아이콘 오른쪽 (프론트 네브바.png 레퍼런스)
// 대상 페이지·섹션이 아직 없어 전부 홈으로 보낸다. 생기면 href 만 바꾼다
const MENU = [
  { label: '서비스 소개', href: '/' },
  { label: '이용 방법', href: '/' },
  { label: 'GPU 목록', href: '/' },
]

export function SiteNav() {
  return (
    <header className="nav-glass fixed inset-x-0 top-0 z-50">
      <div className="relative mx-auto flex h-[72px] max-w-[1440px] items-center justify-between px-16">
        <Link href="/" aria-label="G-shared 홈">
          <Image src="/logo-wordmark.png" alt="G-shared" width={116} height={20} priority unoptimized className="nav-logo h-[20px] w-auto" />
        </Link>
        {/* 메뉴는 로고·우측 아이콘 폭과 무관하게 화면 정중앙에 고정 */}
        <nav className="absolute left-1/2 flex -translate-x-1/2 items-center gap-8">
          {MENU.map((m) => (
            <Link key={m.label} href={m.href} className="text-[15px] font-medium opacity-75 transition-opacity hover:opacity-100">
              {m.label}
            </Link>
          ))}
        </nav>
        <NavAuth />
      </div>
    </header>
  )
}
