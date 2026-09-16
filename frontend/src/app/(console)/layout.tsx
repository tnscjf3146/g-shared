import { AuthGate } from './_components/authGate'
import { ConsoleEntrance } from './_components/consoleEntrance'
import { DashboardSidebar } from './dashboard/_components/dashboardSidebar'

// 로그인 후 화면 공통 셸(대시보드·지갑·워크로드 등록·작업 상세) — 상단 유리 네비 대신 왼쪽 접히는 아이콘 사이드바 + 본문.
// 참고 디자인 "G-shared Dashboard" 구조, 토큰은 AGENTS.md. 랜딩(SiteNav)·로그인/회원가입((auth), 헤더 없음)만 이 셸 밖.
// 보호 경로는 proxy.ts(쿠키 존재) + authProvider(실제 세션) — guest 로 확정되면 AuthGate 가 셸을 숨긴다
export default function ConsoleLayout({ children }: LayoutProps<'/'>) {
  return (
    <div className="flex min-h-dvh bg-surface-0">
      {/* 진입 전환 — 어두운 랜딩에서 올 때만 검정 → 밝음(F5·직접 진입 제외). 콘솔 내부 이동에선 layout 이 유지돼 재생 안 됨 */}
      <ConsoleEntrance />
      <AuthGate>
        <DashboardSidebar />
        <main className="min-w-0 flex-1">
          <div className="mx-auto flex w-full max-w-[1200px] flex-col gap-6 px-6 pt-6 pb-10 md:px-8">{children}</div>
        </main>
      </AuthGate>
    </div>
  )
}
