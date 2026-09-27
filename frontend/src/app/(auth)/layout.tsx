// 로그인/회원가입 라우트 그룹 — 흰 배경 + 가운데 카드(피그마 P2/P3), 헤더 없음. 장식 원은 페이지별 <AuthBackdrop variant/>
// py-6: 회원가입 카드(≈660)가 노트북 뷰포트(≈730)에도 스크롤 없이 가운데. overflow-clip: hidden 은 스크롤 가능한 컨테이너라
// Next 가 이동 후 첫 요소를 scrollIntoView 할 때 화면 밖 장식 원 쪽으로 100px 밀렸던 버그 방지
export default function AuthLayout({ children }: LayoutProps<'/'>) {
  return (
    <main className="relative flex min-h-dvh flex-1 items-center justify-center overflow-clip bg-surface-2 px-6 py-6">
      {children}
    </main>
  )
}
