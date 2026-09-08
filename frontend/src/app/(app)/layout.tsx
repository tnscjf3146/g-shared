import { SiteNav } from '../(marketing)/_components/siteNav'

// 로그인 필요 화면 라우트 그룹(/wallet …). 랜딩과 같은 고정 유리 네비를 재사용 — body[data-nav-theme] 이 없으면 밝은 배경용 기본 색.
// L0 배경 위에 카드 하나가 가운데 뜨는 단순 레이아웃. 네비 높이 72 만큼 위를 비운다
export default function AppLayout({ children }: LayoutProps<'/'>) {
  return (
    <>
      <SiteNav />
      <main className="flex min-h-dvh flex-1 flex-col items-center bg-surface-0 px-5 pt-[calc(72px+3rem)] pb-16 md:px-8">{children}</main>
    </>
  )
}
